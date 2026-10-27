const D = globalThis.TRACKER_DEFAULTS;
const $ = (id) => document.getElementById(id);
const SWITCHES = ["enabled", "autoFill", "autoAi", "attachResume", "highlight", "suggestOnFocus", "autoAnalyze", "autoRegister", "startMinimized"];
let sites = [];

function renderSites() {
  $("sites").replaceChildren(...sites.map((s) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = s;
    const x = document.createElement("button");
    x.textContent = "×";
    x.title = `Remover ${s}`;
    x.setAttribute("aria-label", `Remover ${s}`);
    x.onclick = () => { sites = sites.filter((v) => v !== s); renderSites(); save(); };
    chip.append(x);
    return chip;
  }));
  $("noSites").style.display = sites.length ? "none" : "block";
}

function fill(c, token) {
  $("apiUrl").value = c.apiUrl; $("dashboardUrl").value = c.dashboardUrl;
  if (token !== undefined) $("token").value = token;
  for (const k of SWITCHES) $(k).checked = c[k] !== false && !!c[k];
  for (const r of document.querySelectorAll("input[name=panelMode]")) r.checked = r.value === c.panelMode;
  for (const r of document.querySelectorAll("input[name=panelSide]")) r.checked = r.value === (c.panelSide || "right");
  sites = [...(c.ignoredDomains || [])];
  renderSites();
}

function collect() {
  const out = {
    apiUrl: $("apiUrl").value.trim().replace(/\/$/, "") || D.apiUrl,
    dashboardUrl: $("dashboardUrl").value.trim().replace(/\/$/, "") || D.dashboardUrl,
    panelMode: document.querySelector("input[name=panelMode]:checked")?.value || "auto",
    panelSide: document.querySelector("input[name=panelSide]:checked")?.value || "right",
    ignoredDomains: sites,
  };
  for (const k of SWITCHES) out[k] = $(k).checked;
  return out;
}

/** API fora de localhost (ex.: VPS): o navegador pede permissão para falar com esse endereço. */
async function ensureHostPermission(apiUrl) {
  let origin;
  try { origin = new URL(apiUrl).origin; } catch { return false; }
  if (/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) return true;
  const perm = { origins: [`${origin}/*`] };
  if (await chrome.permissions.contains(perm)) return true;
  return chrome.permissions.request(perm);
}

async function save() {
  const data = collect();
  const ok = await ensureHostPermission(data.apiUrl);
  await chrome.storage.sync.set(data);
  await chrome.storage.local.set({ token: $("token").value.trim() });
  $("saved").textContent = ok ? "Salvo ✓" : "Salvo, mas sem permissão para acessar a API nesse endereço";
  $("saved").style.color = ok ? "" : "var(--bad)";
  setTimeout(() => ($("saved").textContent = ""), 2500);
}

$("addSite").onclick = () => {
  const v = $("newSite").value.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (v && !sites.includes(v)) { sites.push(v); renderSites(); save(); }
  $("newSite").value = "";
};
$("newSite").onkeydown = (e) => e.key === "Enter" && $("addSite").click();
$("save").onclick = save;
$("reset").onclick = () => { fill({ ...D }); save(); };
$("showToken").onclick = () => {
  const t = $("token");
  t.type = t.type === "password" ? "text" : "password";
  $("showToken").textContent = t.type === "password" ? "Mostrar" : "Ocultar";
};
$("test").onclick = async () => {
  await save();
  const r = $("testResult");
  r.className = "pill"; r.textContent = "testando…";
  chrome.runtime.sendMessage({ type: "ping" }, (res) => {
    if (res?.ok) { r.className = "pill ok"; r.textContent = `Conectado · ${res.data.name || res.data.email || "ok"}`; }
    else { r.className = "pill bad"; r.textContent = res?.error || "falhou"; }
  });
};
$("openDash").onclick = () => chrome.runtime.sendMessage({ type: "openDashboard", path: "/config" });
$("clearStats").onclick = async () => { await chrome.storage.local.set({ stats: {} }); $("clearStats").textContent = "Zerado ✓"; };
// salvar automaticamente ao mexer nos interruptores
for (const k of SWITCHES) $(k).addEventListener("change", save);
for (const r of document.querySelectorAll("input[name=panelMode], input[name=panelSide]")) r.addEventListener("change", save);

(async () => {
  const c = { ...D, ...(await chrome.storage.sync.get(D)) };
  const { token } = await chrome.storage.local.get({ token: "" });
  const legacy = (await chrome.storage.sync.get({ token: "" })).token;
  fill(c, token || legacy);
  if (!token && !legacy) $("token").focus();
})();
