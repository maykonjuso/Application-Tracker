// Service worker: única peça que fala com a API. O token fica aqui (chrome.storage.local) e nunca vai para a página.
importScripts("defaults.js");
const DEFAULTS = globalThis.TRACKER_DEFAULTS;
const logos = new Map(); // cache por empresa durante a sessão do navegador

// O que um content script pode pedir à API (o resto só popup/opções, que são páginas da própria extensão).
const PAGE_API = new Set(["POST /page", "POST /analyze", "POST /fields", "POST /suggest", "POST /submitted", "POST /tailor"]);
const PAGE_API_RE = [/^GET \/tasks\/[0-9a-f-]{36}$/i, /^POST \/tailored\/[0-9a-f-]{36}\/approve$/i];
const pageAllowed = (m, p) => PAGE_API.has(`${m} ${p}`) || PAGE_API_RE.some((r) => r.test(`${m} ${p}`));

async function token() {
  const { token: local } = await chrome.storage.local.get({ token: "" });
  if (local) return local;
  // migração: versões antigas guardavam o token em storage.sync
  const { token: old } = await chrome.storage.sync.get({ token: "" });
  if (old) {
    await chrome.storage.local.set({ token: old });
    await chrome.storage.sync.remove("token");
  }
  return old || "";
}

async function config() {
  const c = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...c };
}

/** Configuração para as páginas: sem o token, só se ele existe. */
async function publicConfig() {
  const c = await config();
  delete c.token;
  return { ...c, hasToken: !!(await token()) };
}

async function api(method, path, body, timeoutMs = 120000) {
  const c = await config();
  const t = await token();
  if (!t) throw new Error("Configure o token da extensão nas opções");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${c.apiUrl.replace(/\/$/, "")}/api/v1/assistant${path}`, {
      method,
      signal: ctrl.signal,
      credentials: "omit",
      headers: { Authorization: `Extension ${t}`, ...(body ? { "content-type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "A API demorou demais para responder" : "Não consegui falar com o app. Ele está rodando?");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let msg = `Erro ${res.status}`;
    try { msg = (await res.json()).error?.message ?? msg; } catch {}
    if (res.status === 401) msg = "Token inválido: gere outro no dashboard e cole nas opções";
    throw new Error(msg);
  }
  return res;
}

const toB64 = async (res) => {
  const buf = new Uint8Array(await res.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(bin);
};

// Estado por aba (vaga de origem, respostas, envio): sobrevive à troca de domínio no meio da candidatura
// (ex.: vaga no LinkedIn → formulário na Gupy) e não fica exposto ao JavaScript da página.
const tabKey = (id) => `tab:${id}`;
async function tabState(id) {
  return (await chrome.storage.session.get({ [tabKey(id)]: {} }))[tabKey(id)];
}
chrome.tabs?.onRemoved.addListener((id) => chrome.storage.session.remove(tabKey(id)));

// Contadores do dia (mostrados no popup)
async function bump(field, n = 1) {
  const today = new Date().toISOString().slice(0, 10);
  const { stats } = await chrome.storage.local.get({ stats: {} });
  const s = stats.day === today ? stats : { day: today, filled: 0, ai: 0, apps: 0 };
  s[field] = (s[field] || 0) + n;
  await chrome.storage.local.set({ stats: s });
}

const fromPage = (sender) => !!sender.tab; // content script (página) × páginas da extensão

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return false;
  (async () => {
    try {
      if (msg.type === "config") return reply({ ok: true, data: await publicConfig() });
      if (msg.type === "ping") {
        const res = await api("GET", "/profile-summary", undefined, 8000);
        return reply({ ok: true, data: await res.json() });
      }
      if (msg.type === "api") {
        if (fromPage(sender) && !pageAllowed(msg.method, msg.path)) throw new Error("chamada não permitida");
        const res = await api(msg.method, msg.path, msg.body);
        return reply({ ok: true, data: await res.json() });
      }
      if (msg.type === "logo") {
        const id = String(msg.companyId || "");
        if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error("empresa inválida");
        if (logos.has(id)) return reply({ ok: true, data: logos.get(id) });
        const res = await api("GET", `/logo?company_id=${id}`, undefined, 15000);
        const url = `data:${res.headers.get("content-type") || "image/png"};base64,${await toB64(res)}`;
        logos.set(id, url);
        return reply({ ok: true, data: url });
      }
      if (msg.type === "resume") {
        const id = msg.jobId && /^[0-9a-f-]{36}$/i.test(msg.jobId) ? msg.jobId : "";
        const res = await api("GET", `/resume${id ? `?job_id=${id}` : ""}`);
        return reply({ ok: true, data: { b64: await toB64(res), filename: res.headers.get("x-filename") || "curriculo.pdf", renderId: res.headers.get("x-render-id") } });
      }
      if (msg.type === "tab:get") return reply({ ok: true, data: sender.tab?.id != null ? await tabState(sender.tab.id) : {} });
      if (msg.type === "tab:set" && sender.tab?.id != null) {
        const cur = await tabState(sender.tab.id);
        await chrome.storage.session.set({ [tabKey(sender.tab.id)]: msg.replace ? msg.value : { ...cur, ...msg.value } });
        return reply({ ok: true });
      }
      if (msg.type === "stat") { await bump(msg.field, msg.n || 1); return reply({ ok: true }); }
      if (msg.type === "badge" && sender.tab?.id != null) {
        // contador no ícone: campos que ainda precisam de você nesta aba
        await chrome.action.setBadgeBackgroundColor({ tabId: sender.tab.id, color: msg.tone === "ok" ? "#16a34a" : "#2563eb" });
        await chrome.action.setBadgeText({ tabId: sender.tab.id, text: String(msg.text || "").slice(0, 3) });
        return reply({ ok: true });
      }
      if (msg.type === "pauseSite" && sender.tab?.url) {
        const host = new URL(sender.tab.url).host;
        const c = await config();
        if (!c.ignoredDomains.includes(host)) await chrome.storage.sync.set({ ignoredDomains: [...c.ignoredDomains, host] });
        return reply({ ok: true });
      }
      if (msg.type === "openPdf") {
        // abre o PDF do CV sob medida numa aba (blob da própria extensão: sem expor o token)
        const id = String(msg.variantId || "");
        if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error("currículo inválido");
        await chrome.tabs.create({ url: chrome.runtime.getURL(`pdf.html#${id}`) });
        return reply({ ok: true });
      }
      if (msg.type === "pdf") {
        const id = String(msg.variantId || "");
        if (!/^[0-9a-f-]{36}$/i.test(id)) throw new Error("currículo inválido");
        const res = await api("GET", `/tailored/${id}/pdf`);
        return reply({ ok: true, data: await toB64(res) });
      }
      if (msg.type === "openOptions") { await chrome.runtime.openOptionsPage(); return reply({ ok: true }); }
      if (msg.type === "openDashboard") {
        const c = await config();
        const path = /^\/[\w/-]*$/.test(msg.path || "") ? msg.path : "/vagas";
        await chrome.tabs.create({ url: `${c.dashboardUrl.replace(/\/$/, "")}${path}` });
        return reply({ ok: true });
      }
      reply({ ok: false, error: "mensagem desconhecida" });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;
});

// atalhos de teclado (chrome://extensions/shortcuts para trocar): só o quadro principal da aba.
// `?.`: alguns navegadores/perfis não expõem chrome.commands; sem isso o service worker inteiro parava aqui.
chrome.commands?.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  const type = { "toggle-panel": "tracker:show-panel", "fill-form": "tracker:fill", "ai-fill": "tracker:ai" }[command];
  if (type) chrome.tabs.sendMessage(tab.id, { type }, { frameId: 0 }).catch(() => {});
});

// ao mudar as opções, as páginas abertas reagem (ex.: pausar na hora)
chrome.storage.onChanged.addListener(async (changes, area) => {
  if (area !== "sync") return;
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) if (t.id != null) chrome.tabs.sendMessage(t.id, { type: "tracker:config-changed", keys: Object.keys(changes) }).catch(() => {});
});

// pausa temporária: ao acabar, avisa as páginas para voltarem
chrome.alarms?.onAlarm.addListener(async (a) => {
  if (a.name === "resume") await chrome.storage.sync.set({ pausedUntil: 0 });
});

chrome.runtime.onInstalled.addListener(async (d) => {
  await token(); // migra o token antigo
  // primeira instalação sem token: abre as opções para colar o token (num instante, sem atrapalhar abas abertas)
  if (d.reason === "install") setTimeout(async () => { if (!(await token())) chrome.tabs.create({ url: chrome.runtime.getURL("options.html"), active: true }); }, 1500);
});
