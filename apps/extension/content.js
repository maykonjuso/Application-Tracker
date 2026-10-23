// Application Tracker — assistente do navegador (content script).
// Regras: só age em páginas de vaga/candidatura; preenche e sugere; NUNCA envia formulários nem clica em "enviar".
(() => {
  if (window.__trackerAssistant) return;
  window.__trackerAssistant = true;

  const IS_TOP = window === window.top;
  const KNOWN_HOSTS = ["linkedin.com/jobs", "greenhouse.io", "lever.co", "ashbyhq.com", "gupy.io", "myworkdayjobs.com", "smartrecruiters.com",
    "workable.com", "inhire", "solides", "kenoby", "recrutei", "teamtailor.com", "breezy.hr", "jobvite.com", "icims.com", "bamboohr.com",
    "recruitee.com", "vagas.com.br", "infojobs.com.br", "catho.com.br", "remotive.com", "remoteok.com", "himalayas.app", "jobicy.com",
    "weworkremotely.com", "workingnomads.com", "programathor.com.br", "arbeitnow.com", "pinpointhq.com", "jazzhr.com", "rippling.com/jobs"];
  const JOB_WORDS = /(responsibilities|requirements|qualifications|about the role|job description|apply now|responsabilidades|requisitos|qualifica[cç][oõ]es|sobre a vaga|candidate-se|benef[ií]cios|modelo de trabalho)/gi;
  const SUCCESS = /(application (was )?(sent|submitted|received)|thank(s| you) for (applying|your application)|we.ve received your application|candidatura (foi )?(enviada|recebida|realizada|conclu[ií]da)|obrigad[oa] por se candidatar|inscri[cç][aã]o (realizada|conclu[ií]da)|recebemos sua candidatura)/i;
  const SUBMIT_WORDS = /^(submit|send|apply|enviar|candidatar|finalizar|concluir|confirmar)\b|submit application|enviar candidatura|send application/i;
  // nunca lê nem preenche: senhas, cartões, códigos de verificação, documentos e busca do site
  const SKIP = /password|senha|card.?number|cart[aã]o|\bcc-|cvv|cvc|csc|security.?code|one-time-code|\botp\b|verification.?code|c[oó]digo de verifica|captcha|token|iban|\bcpf\b|\brg\b|search|busca|pesquis/i;
  const SUBMIT_WINDOW_MS = 15 * 60 * 1000; // a confirmação só conta se você enviou há pouco
  const AI_SOURCES = new Set(["ai"]);

  const state = {
    filling: false, epoch: 0, cfg: null, card: null, pageInfo: null, fields: [], status: new Map(), edits: new Map(),
    reported: false, busy: false, msg: "", minimized: false, hidden: false, logo: null, forcedPanel: false,
    relevant: false, failures: 0, showFilled: false, tab: {}, tailor: null,
  };

  // ---------------- comunicação ----------------
  const bg = (msg) => new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => (r?.ok ? resolve(r.data) : reject(new Error(r?.error || chrome.runtime.lastError?.message || "sem resposta"))));
    } catch (e) { reject(e); } // extensão recarregada: a página antiga perde o contato
  });
  const api = (method, path, body) => bg({ type: "api", method, path, body });
  const tab = {
    get: () => state.tab,
    set: (v) => { state.tab = { ...state.tab, ...v }; bg({ type: "tab:set", value: v }).catch(() => {}); },
    load: async () => { state.tab = await bg({ type: "tab:get" }).catch(() => ({})) || {}; },
  };
  const jobId = () => state.card?.job_id || tab.get().jobId || null;
  const ignoredHere = () => (state.cfg?.ignoredDomains || []).some((d) => d && location.host.includes(d));
  const paused = () => (state.cfg?.pausedUntil || 0) > Date.now();
  const active = () => !!state.cfg?.enabled && !!state.cfg?.hasToken && !ignoredHere() && !paused();
  const knownHost = () => KNOWN_HOSTS.some((h) => location.href.includes(h));
  const recentSubmit = () => Date.now() - (tab.get().formSubmitted || 0) < SUBMIT_WINDOW_MS;
  /** Contexto de candidatura: só aqui o assistente preenche sozinho e anexa o CV. */
  const applyContext = () => !!(state.card?.known || state.card?.flow_page || state.pageInfo?.flow_page || knownHost() || state.pageInfo?.looks_like_job || tab.get().jobId
    || state.fields.some((f) => f.kind === "file" && CV_LABEL.test(f.label))); // "trabalhe conosco" que pede o currículo
  /** O painel aparece sozinho? (auto: vagas e formulários · forms: só formulários · manual: só a pedido) */
  function panelAllowed() {
    if (state.forcedPanel) return true;
    if (state.hidden) return false;
    const mode = state.cfg?.panelMode || "auto";
    if (mode === "manual") return false;
    if (mode === "forms") return state.fields.length > 0;
    return true;
  }
  function badge() {
    if (!IS_TOP) return;
    if (!active()) return bg({ type: "badge", text: "" }).catch(() => {});
    const pending = state.fields.filter((f) => isEmpty(f)).length;
    const text = state.fields.length ? (pending ? String(pending) : "✓") : "";
    bg({ type: "badge", text, tone: pending ? "info" : "ok" }).catch(() => {});
  }
  function teardown() {
    host?.remove(); host = null; shadow = null; lastHtml = "";
    hideBubble();
    bg({ type: "badge", text: "" }).catch(() => {});
  }

  // ---------------- texto e comparação ----------------
  const txt = (el) => (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const firstLine = (el) => ((el?.innerText || el?.textContent || "").split("\n").map((s) => s.trim()).find(Boolean) || "");
  const fold = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const norm = (s) => fold(s).replace(/[^a-z0-9+#.]+/g, " ").trim();
  const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";
  // equivalências PT/EN mais comuns em formulários
  const SYN = [
    ["sim", "yes", "true", "y"], ["nao", "no", "false", "n"],
    ["fluente", "fluent", "nativo", "native", "proficient", "c2"], ["avancado", "advanced", "c1"], ["intermediario", "intermediate", "b2", "b1"], ["basico", "basic", "beginner", "iniciante", "a2", "a1"],
    ["remoto", "remote", "home office", "100 remoto"], ["hibrido", "hybrid"], ["presencial", "on site", "onsite", "in office", "in person"],
    ["masculino", "male", "homem"], ["feminino", "female", "mulher"],
    ["ensino superior completo", "bachelor", "bacharelado", "graduacao", "superior completo", "bachelor s degree"],
    ["pos graduacao", "postgraduate", "especializacao", "mba"], ["mestrado", "master", "master s degree"], ["doutorado", "doctorate", "phd"],
    ["clt", "full time employee", "employee"], ["pj", "contractor", "pessoa juridica"],
  ];
  const synOf = (s) => { const n = norm(s); return SYN.find((g) => g.includes(n)) || [n]; };
  const wordIn = (hay, needle) => new RegExp(`(^| )${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}( |$)`).test(hay);

  /** Melhor opção para um valor: exata → sinônimo → contém (palavra inteira) → faixa numérica ("3–5 anos"). */
  function bestOption(options, value) {
    const v = norm(value);
    if (!v) return -1;
    const vs = synOf(value);
    let idx = options.findIndex((o) => norm(o) === v);
    if (idx >= 0) return idx;
    idx = options.findIndex((o) => vs.includes(norm(o)) || synOf(o).some((x) => vs.includes(x)));
    if (idx >= 0) return idx;
    idx = options.findIndex((o) => { const n = norm(o); return n && Math.min(n.length, v.length) >= 4 && (wordIn(n, v) || wordIn(v, n)); });
    if (idx >= 0) return idx;
    const num = parseFloat(String(value).replace(",", "."));
    if (!Number.isNaN(num) && /^\s*\d/.test(String(value))) {
      idx = options.findIndex((o) => {
        const r = fold(o).replace(/[–—]/g, "-");
        const m = r.match(/(\d+(?:[.,]\d+)?)\s*(?:a|-|to|ate|até)\s*(\d+(?:[.,]\d+)?)/);
        if (m) return num >= parseFloat(m[1].replace(",", ".")) && num <= parseFloat(m[2].replace(",", "."));
        const plus = r.match(/(\d+)\s*\+|mais de (\d+)|more than (\d+)|acima de (\d+)|over (\d+)/);
        if (plus) return num >= +(plus[1] || plus[2] || plus[3] || plus[4] || plus[5]);
        const less = r.match(/menos de (\d+)|less than (\d+)|ate (\d+)|up to (\d+)|under (\d+)/);
        if (less) return num < +(less[1] || less[2] || less[3] || less[4] || less[5]);
        return false;
      });
      if (ranges.length === 1) return ranges[0];
    }
    return -1;
  }

  // ---------------- leitura dos campos (rótulos acessíveis) ----------------
  function labelFor(el) {
    const aria = el.getAttribute("aria-label"); if (aria && aria.length > 1) return aria.trim();
    const lb = el.getAttribute("aria-labelledby");
    if (lb) { const t = lb.split(/\s+/).map((id) => txt(document.getElementById(id))).join(" ").trim(); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && txt(l)) return txt(l); }
    const wrap = el.closest("label"); if (wrap && txt(wrap)) return txt(wrap);
    const ph = (el.getAttribute("placeholder") || "").trim();
    const usefulPh = ph.length > 3 && !GENERIC_PH.test(ph) && !/^(ex|e\.g)[.:]/i.test(ph);
    if (usefulPh && ph.split(/\s+/).length >= 2) return ph; // "Your LinkedIn URL" diz o que é; "Enter" não
    const fs = el.closest("fieldset"); const lg = fs?.querySelector("legend"); if (lg && txt(lg)) return txt(lg);
    // procura o texto mais próximo acima (formulários sem <label>: muitos ATS usam <div>/<span> como rótulo)
    let node = el;
    for (let depth = 0; depth < 6 && node && node !== document.body; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        const cls = typeof prev.className === "string" ? prev.className : "";
        // pula partes do próprio dropdown (placeholder, valor escolhido, ícones) e blocos de outros campos
        const widget = /(placeholder|single-?value|multi-?value|indicator|menu|option|select__|-control)/i.test(cls);
        if (!widget && !prev.matches("input,select,textarea,button,script,style") && !prev.querySelector("input,select,textarea")) {
          const t = txt(prev);
          if (t && t.length < 300 && !GENERIC_PH.test(t)) return t;
        }
        prev = prev.previousElementSibling;
      }
      node = node.parentElement;
    }
    return (usefulPh ? ph : "") || humanName(el.name || el.id || "");
  }
  // placeholders que não dizem nada sobre o campo
  const GENERIC_PH = /^(enter|search|select|choose|type( here)?|type your answer|enter your answer|your answer|digite( aqui)?|digite sua resposta( aqui)?|selecione|escolha|buscar|pesquisar|procurar|sua resposta|start typing|\.{3}|-+)\.?…?$/i;
  /** "first_name" → "first name" (último recurso para campos sem rótulo nenhum). */
  const humanName = (s) => (/^[a-z][\w-]*$/i.test(s) && !/^(input|field|react-select|trk)\d*/i.test(s) ? s.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim() : "");
  /** Texto de uma opção de rádio/checkbox. Sem <label>, procura o texto ao lado; nunca devolve "on" (valor padrão do navegador). */
  function radioOptionLabel(r) {
    const byFor = r.id ? txt(document.querySelector(`label[for="${CSS.escape(r.id)}"]`)) : "";
    if (byFor) return byFor;
    const wrap = txt(r.closest("label")); if (wrap) return wrap;
    const aria = r.getAttribute("aria-label"); if (aria) return aria.trim();
    const lb = r.getAttribute("aria-labelledby");
    if (lb) { const t = lb.split(/\s+/).map((id) => txt(document.getElementById(id))).join(" ").trim(); if (t) return t; }
    // texto logo depois do botão (nó de texto ou elemento irmão)
    for (let n = r.nextSibling; n; n = n.nextSibling) {
      if (n.nodeType === 1 && n.matches?.("input,select,textarea")) break;
      const t = (n.textContent || "").replace(/\s+/g, " ").trim();
      if (t) return t;
    }
    // o menor bloco que só contém esta opção
    for (let box = r.parentElement, d = 0; box && d < 3; box = box.parentElement, d++) {
      if (box.querySelectorAll("input").length > 1) break;
      const t = txt(box); if (t) return t;
    }
    const v = (r.value || "").trim();
    return v && v.toLowerCase() !== "on" ? v : "";
  }
  function groupQuestion(first, group) {
    const fs = first.closest("fieldset"); const lg = fs?.querySelector("legend"); if (lg) return txt(lg);
    const rg = first.closest('[role="radiogroup"]');
    if (rg) { const l = rg.getAttribute("aria-label") || txt(document.getElementById(rg.getAttribute("aria-labelledby") || "")); if (l) return l; }
    // o menor ancestral que contém o grupo todo; a pergunta é o texto logo antes dele
    let box = first.parentElement;
    while (box && group && !group.every((r) => box.contains(r))) box = box.parentElement;
    // dentro do bloco do campo: o texto antes da primeira opção ("What is your level of English?")
    if (box) {
      let child = first;
      while (child.parentElement && child.parentElement !== box) child = child.parentElement;
      for (let prev = child.previousElementSibling; prev; prev = prev.previousElementSibling) {
        if (prev.querySelector("input,select,textarea")) break;
        const t = txt(prev);
        if (t && t.length < 300) return t;
      }
    }
    for (let node = box, depth = 0; node && depth < 3; node = node.parentElement, depth++) {
      const prev = node.previousElementSibling;
      if (prev && txt(prev) && txt(prev).length < 220 && !prev.querySelector("input")) return txt(prev);
    }
    return labelFor(first);
  }
  function kindOf(el) {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "SELECT") return "select";
    if (el.tagName === "TEXTAREA") return "textarea";
    if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list" || el.getAttribute("aria-haspopup") === "listbox") return "typeahead";
    if (["email", "tel", "number", "date", "month", "file", "checkbox", "url"].includes(type)) return type === "url" ? "text" : type;
    return "text";
  }
  // chave de cada campo fica num WeakMap (a página não vê nem consegue forjar)
  const keys = new WeakMap();
  const els = new Map(); // chave → elemento (ou lista de rádios)
  let seq = 0;
  const keyOf = (el) => { let k = keys.get(el); if (!k) { k = `trk${seq++}`; keys.set(el, k); } return k; };
  const skipEl = (el, type) => SKIP.test(`${el.name || ""} ${el.id || ""} ${el.getAttribute("autocomplete") || ""} ${type}`);
  function extractFields() {
    const out = []; const seen = new Set();
    els.clear();
    document.querySelectorAll("input, select, textarea").forEach((el) => {
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (["hidden", "submit", "button", "image", "reset", "password", "search", "range", "color"].includes(type) || el.disabled || el.readOnly) return;
      if (type !== "file" && !visible(el)) return;
      if (type === "file" && !(visible(el) || visible(el.closest("label, div, section, form")))) return;
      if (skipEl(el, type)) return;
      if (type === "radio") {
        const name = el.name || el.id; if (!name || seen.has(name)) return; seen.add(name);
        const group = [...(el.form || document).querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)];
        const key = keyOf(group[0]);
        group.forEach((r) => keys.set(r, key));
        els.set(key, group);
        const opts = group.map(radioOptionLabel);
        // opções sem texto ou repetidas: não dá para saber qual é qual, então não marca nada (fica para você)
        if (opts.some((o) => !o) || new Set(opts.map(norm)).size < opts.length) return;
        const checked = group.find((r) => r.checked);
        out.push({ key, label: groupQuestion(el, group), kind: "radio", options: opts, required: group.some((r) => r.required), current_value: checked ? opts[group.indexOf(checked)] : null });
        return;
      }
      const kind = kindOf(el);
      const label = labelFor(el);
      if (!label || GENERIC_PH.test(label)) return;
      const key = keyOf(el);
      els.set(key, el);
      const options = kind === "select" ? [...el.options].filter((o) => o.value && !/^(select|selecione|escolha|--)/i.test(o.text.trim())).map((o) => o.text.trim()) : [];
      out.push({ key, label: label.slice(0, 300), kind, options, required: el.required || el.getAttribute("aria-required") === "true",
        current_value: kind === "checkbox" ? String(el.checked) : kind === "select" ? (el.value ? el.options[el.selectedIndex]?.text?.trim() : null) : kind === "file" ? (el.files?.length ? el.files[0].name : null) : el.value || null });
    });
    return out;
  }
  const byKey = (key) => { const e = els.get(key); return Array.isArray(e) ? null : e?.isConnected ? e : null; };
  const radiosOf = (key) => { const e = els.get(key); return Array.isArray(e) ? e.filter((r) => r.isConnected) : []; };
  const anchorOf = (f) => (f.kind === "radio" ? radiosOf(f.key)[0] : byKey(f.key));

  // ---------------- preenchimento robusto (React, Vue, Angular, máscaras) ----------------
  function nativeSet(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  /** Simula a digitação (o mesmo caminho de colar texto): frameworks e máscaras aceitam. */
  function typeInto(el, value) {
    const v = String(value);
    try {
      el.focus({ preventScroll: true });
      if (typeof el.select === "function") el.select();
      const ok = document.execCommand("insertText", false, v);
      if (ok && el.value && el.value.replace(/\D/g, "").length >= Math.min(1, v.replace(/\D/g, "").length)) {
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
    } catch {}
    nativeSet(el, v);
    return !!el.value;
  }
  async function attachResume(el, manual) {
    if (!manual && (state.cfg?.attachResume === false || !applyContext())) return null;
    const { b64, filename, renderId } = await bg({ type: "resume", jobId: jobId() });
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], filename, { type: "application/pdf" }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    tab.set({ renderId });
    return filename;
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function pickFromListbox(value) {
    for (let i = 0; i < 8; i++) {
      await sleep(150);
      const opts = [...document.querySelectorAll('[role="option"]')].filter(visible);
      if (opts.length) {
        const idx = bestOption(opts.map(txt), value);
        if (idx >= 0) { opts[idx].scrollIntoView({ block: "nearest" }); opts[idx].click(); return true; }
      }
    }
    return false;
  }
  const TONE = { ok: "#16a34a", ai: "#2563eb", you: "#d97706" };
  function paint(el, tone) {
    if (state.cfg?.highlight === false) return;
    const target = el?.closest?.("fieldset") || el;
    if (!target?.style) return;
    target.style.outline = `2px solid ${TONE[tone] || TONE.you}`;
    target.style.outlineOffset = "2px";
    target.style.borderRadius = target.style.borderRadius || "6px";
  }
  function unpaint(el) {
    const target = el?.closest?.("fieldset") || el;
    if (target?.style) { target.style.outline = ""; target.style.outlineOffset = ""; }
  }
  const comboValue = (el) => el.closest("[class*=select],[class*=Select]")?.querySelector("[class*=single-value],[class*=singleValue]");

  /** Coloca o valor no campo. Devolve true se ficou preenchido. */
  async function setValue(field, value) {
    if (value == null || value === "") return false;
    if (field.kind === "radio") {
      const group = radiosOf(field.key);
      const idx = bestOption(group.map(radioOptionLabel), value);
      if (idx < 0) return false;
      const r = group[idx];
      const lab = r.id ? document.querySelector(`label[for="${CSS.escape(r.id)}"]`) : r.closest("label");
      (lab || r).click();
      if (!r.checked) r.click();
      return r.checked;
    }
    const el = byKey(field.key);
    if (!el) return false;
    if (field.kind === "file") { if (value === "__resume__" && !el.files?.length) await attachResume(el); return !!el.files?.length; }
    if (field.kind === "checkbox") {
      const want = ["true", "sim", "yes", "1", "on"].includes(norm(value));
      if (el.checked !== want) el.click();
      return el.checked === want;
    }
    if (field.kind === "select") {
      const opts = [...el.options];
      const idx = bestOption(opts.map((o) => o.text.trim()), value);
      if (idx < 0) return false;
      nativeSet(el, opts[idx].value);
      return true;
    }
    if (field.kind === "typeahead") {
      typeInto(el, value);
      await pickFromListbox(value);
      return !!el.value || !!comboValue(el);
    }
    if (field.kind === "month") {
      // <input type="month"> espera "aaaa-mm"; aceita também "mm/aaaa"
      const s = String(value);
      const m = s.match(/^(\d{4})-(\d{2})$/) || s.match(/^(\d{2})\/(\d{4})$/);
      if (!m) return false;
      nativeSet(el, m[1].length === 4 ? `${m[1]}-${m[2]}` : `${m[2]}-${m[1]}`);
      return !!el.value;
    }
    if (field.kind === "date" && /^\d{2}\/\d{2}\/\d{4}$/.test(String(value))) {
      const [d, m, y] = String(value).split("/");
      nativeSet(el, `${y}-${m}-${d}`);
      return !!el.value;
    }
    return typeInto(el, value);
  }
  const isEmpty = (field) => {
    if (field.kind === "radio") return !radiosOf(field.key).some((r) => r.checked);
    const el = byKey(field.key);
    if (!el) return false;
    if (field.kind === "file") return !el.files?.length;
    if (field.kind === "checkbox") return false; // checkbox só por pedido explícito
    if (field.kind === "typeahead") return !el.value && !comboValue(el);
    return !el.value;
  };

  // ---------------- preenchimento ----------------
  // status por campo: filled (verde) | ai (azul, revisar) | user (você) | suggestion (laranja) | sensitive | empty
  async function fillFields(fields, allowAi) {
    if (!fields.length) return { filled: 0, ai: 0 };
    const epoch = state.epoch;
    const res = await api("POST", "/fields", { url: location.href, job_id: jobId(), fields, allow_ai: allowAi });
    if (epoch !== state.epoch) return { filled: 0, ai: 0 }; // a página mudou enquanto a API respondia
    const before = document.activeElement;
    state.filling = true; // ignora os eventos causados pelo próprio preenchimento (sem bolha, sem aprender)
    try {
      return await applyResults(fields, res, allowAi);
    } finally {
      // devolve o foco para onde você estava; se não estava num campo, não deixa nenhum focado
      if (before && before !== document.body && before.isConnected) before.focus({ preventScroll: true });
      else if (document.activeElement instanceof HTMLElement && document.activeElement !== document.body) document.activeElement.blur();
      setTimeout(() => { state.filling = false; }, 50);
    }
  }
  async function applyResults(fields, res, allowAi) {
    let filled = 0, ai = 0;
    for (const f of fields) {
      const known = res.values[f.key];
      const sug = res.suggestions[f.key];
      const prev = state.status.get(f.key);
      if (known && isEmpty(f) && (await setValue(f, known.value))) {
        filled++;
        const isAi = AI_SOURCES.has(known.source);
        if (isAi) ai++;
        state.status.set(f.key, { label: f.label, status: isAi ? "ai" : "filled", value: known.value, source: known.source, auto: true });
        paint(anchorOf(f), isAi ? "ai" : "ok");
      } else if (sug && allowAi && isEmpty(f) && (await setValue(f, sug.value))) {
        filled++; ai++;
        state.status.set(f.key, { label: f.label, status: "ai", value: sug.value, source: "ai", auto: true });
        paint(anchorOf(f), "ai");
      } else if (res.sensitive.includes(f.key)) {
        state.status.set(f.key, { label: f.label, status: "sensitive" });
      } else if (!isEmpty(f)) {
        state.status.set(f.key, prev?.status ? prev : { label: f.label, status: "user", value: f.current_value, source: "user" });
      } else {
        state.status.set(f.key, { label: f.label, status: sug ? "suggestion" : "empty", value: sug?.value });
      }
    }
    if (filled) bg({ type: "stat", field: "filled", n: filled }).catch(() => {});
    if (ai) bg({ type: "stat", field: "ai", n: ai }).catch(() => {});
    return { filled, ai };
  }
  async function autoFill(onlyNew, manual) {
    if (state.busy || !active()) return;
    if (!manual && (state.cfg?.autoFill === false || !applyContext())) return;
    if (!manual && state.failures >= 3) return; // API com problema: espera você pedir
    const all = extractFields();
    state.fields = all;
    const todo = onlyNew ? all.filter((f) => !state.status.has(f.key)) : all;
    if (!todo.length) return render();
    state.busy = true;
    render("Preenchendo com o seu perfil…");
    try {
      const { filled } = await fillFields(todo, false);
      state.failures = 0;
      state.msg = filled ? `${filled} campo(s) preenchido(s) automaticamente. Confira antes de enviar.` : manual ? "Nada novo para preencher com o que eu sei." : "";
    } catch (e) { state.failures++; state.msg = `Não consegui preencher: ${e.message}`; }
    state.busy = false;
    render();
  }
  async function aiFill() {
    if (state.busy || !active()) return;
    state.fields = extractFields();
    const empty = state.fields.filter((f) => isEmpty(f) && f.kind !== "file");
    const pending = empty.filter((f) => state.status.get(f.key)?.status !== "sensitive");
    if (!pending.length) { state.msg = empty.length ? "Só faltam perguntas sensíveis (salário, visto…): essas ficam para você." : "Tudo já está preenchido."; return render(); }
    state.busy = true;
    render(`A IA está escrevendo ${pending.length} resposta(s)… (pode levar até 1 minuto)`);
    try {
      const { filled } = await fillFields(pending, true);
      state.failures = 0;
      state.msg = filled ? `✨ ${filled} resposta(s) escritas pela IA (contorno azul). Leia e ajuste se precisar.` : "A IA não encontrou o que responder nos campos restantes.";
    } catch (e) { state.msg = `A IA falhou: ${explainError(e)}`; }
    state.busy = false;
    render();
  }
  /** Desfaz o que o assistente preencheu (o que você digitou fica). */
  function undoFill() {
    let n = 0;
    for (const [key, st] of state.status) {
      if (!st.auto) continue;
      const f = state.fields.find((x) => x.key === key);
      if (!f) continue;
      const el = byKey(key);
      if (f.kind === "radio") { for (const r of radiosOf(key)) if (r.checked) { r.checked = false; r.dispatchEvent(new Event("change", { bubbles: true })); } unpaint(radiosOf(key)[0]); }
      else if (el && f.kind === "file") { el.value = ""; el.dispatchEvent(new Event("change", { bubbles: true })); unpaint(el); }
      else if (el && f.kind !== "checkbox") { nativeSet(el, ""); unpaint(el); }
      state.status.set(key, { label: st.label, status: "empty", undone: true });
      n++;
    }
    state.msg = n ? `${n} campo(s) limpos. “Preencher de novo” refaz.` : "Nada preenchido por mim para desfazer.";
    render();
  }

  // ---------------- painel (shadow DOM, visual do app; botões só aceitam cliques de verdade) ----------------
  let host, shadow;
  let lastHtml = ""; // último conteúdo desenhado: evita redesenhar (e piscar) quando nada mudou
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const I = (d, s = 16) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICON = {
    spark: I('<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>'),
    min: I('<path d="M5 12h14"/>'),
    x: I('<path d="M18 6 6 18M6 6l12 12"/>'),
    gear: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    pause: I('<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>'),
    check: I('<path d="M20 6 9 17l-5-5"/>', 14),
    undo: I('<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>', 14),
    refresh: I('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>', 14),
    ext: I('<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>', 14),
    chev: I('<path d="m6 9 6 6 6-6"/>', 14),
  };
  const CSS_TEXT = `
    :host{all:initial}
    *{box-sizing:border-box}
    .root{--p:#2563eb;--p2:#0ea5e9;--bg:#fff;--fg:#0f172a;--mut:#526075;--bd:#e3e7ef;--soft:#f4f6fa;--acc:#eaf2ff;
      font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,"Segoe UI",sans-serif;font-size:13px;line-height:1.45;color:var(--fg);letter-spacing:-.01em}
    @media (prefers-color-scheme:dark){.root{--bg:#11151f;--fg:#e5e9f2;--mut:#a3adc2;--bd:#232a3b;--soft:#1a2030;--acc:#172554}}
    .fab{display:flex;align-items:center;gap:8px;border:0;cursor:pointer;background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;border-radius:999px;padding:10px 14px;box-shadow:0 8px 24px rgba(37,99,235,.35);font:600 13px/1 inherit;font-family:inherit}.fab.enter{animation:pop .25s cubic-bezier(.2,.9,.3,1.2)}
    .fab:focus-visible,.b:focus-visible,.ib:focus-visible,.it:focus-visible{outline:2px solid var(--p);outline-offset:2px}
    .fab .n{background:#fff;color:var(--p);border-radius:999px;padding:2px 7px;font-size:11px}
    .card{width:344px;max-height:min(78vh,640px);display:flex;flex-direction:column;background:var(--bg);border:1px solid var(--bd);border-radius:16px;box-shadow:0 2px 4px rgba(15,23,42,.06),0 18px 40px -12px rgba(15,23,42,.28);overflow:hidden}.card.enter{animation:in .22s cubic-bezier(.2,.8,.2,1)}
    @keyframes in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
    @keyframes pop{from{opacity:0;transform:scale(.8)}to{opacity:1;transform:none}}
    @media (prefers-reduced-motion:reduce){.card,.fab{animation:none}.prog i{transition:none}}
    .hd{display:flex;align-items:center;gap:6px;padding:9px 8px 9px 12px;border-bottom:1px solid var(--bd);cursor:grab;user-select:none}
    .brand{display:grid;place-items:center;width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;flex-shrink:0}
    .ttl{font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .dot{width:7px;height:7px;border-radius:50%;background:#16a34a;display:inline-block;margin-left:6px;vertical-align:middle}
    .dot.busy{background:#f59e0b;animation:bl 1s infinite}@keyframes bl{50%{opacity:.3}}
    .ib{border:0;background:transparent;color:var(--mut);cursor:pointer;border-radius:8px;width:28px;height:28px;display:grid;place-items:center;flex-shrink:0}
    .ib:hover{background:var(--soft);color:var(--fg)}
    .bd{padding:12px;overflow:auto;display:flex;flex-direction:column;gap:12px}
    .job{display:flex;gap:10px;align-items:flex-start}
    .logo{width:40px;height:40px;border-radius:10px;border:1px solid var(--bd);background:#fff;display:grid;place-items:center;overflow:hidden;flex-shrink:0;font-weight:600;color:#fff}
    .logo img{width:100%;height:100%;object-fit:contain;padding:4px}
    .jt{font-weight:600;font-size:14px;line-height:1.3}
    .muted{color:var(--mut);font-size:12px}
    .pill{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:600}
    .p-top{background:#05966920;color:#047857}.p-bet{background:var(--acc);color:var(--p)}.p-volume{background:#d9770620;color:#b45309}.p-discard,.p-blocked{background:var(--soft);color:var(--mut)}
    .bars{display:grid;grid-template-columns:1fr 1fr;gap:8px}
    .bar{font-size:11px;color:var(--mut)}.bar b{color:var(--fg)}
    .track{height:6px;border-radius:99px;background:var(--soft);margin-top:3px;overflow:hidden}.track i{display:block;height:100%;border-radius:99px}
    .sec{border:1px solid var(--bd);border-radius:12px;padding:10px}
    .sec h4{margin:0 0 6px;font-size:12px;font-weight:600;color:var(--fg);display:flex;align-items:center;gap:6px}
    .prog{height:6px;border-radius:99px;background:var(--soft);overflow:hidden;margin:6px 0}.prog i{display:block;height:100%;background:linear-gradient(90deg,var(--p),var(--p2));transition:width .4s}
    .list{display:flex;flex-direction:column;gap:4px;margin-top:6px;max-height:180px;overflow:auto}
    .it{display:flex;align-items:center;gap:6px;border:0;background:var(--soft);border-radius:8px;padding:6px 8px;text-align:left;cursor:pointer;font:inherit;color:inherit;width:100%}
    .it:hover{background:var(--acc)}
    .it span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .chip{font-size:10px;font-weight:600;border-radius:999px;padding:1px 6px;white-space:nowrap;font-style:normal}
    .c-empty{background:#d9770620;color:#b45309}.c-suggestion{background:var(--acc);color:var(--p)}.c-sensitive{background:#dc262618;color:#b91c1c}
    .c-ai{background:var(--acc);color:var(--p)}.c-filled{background:#16a34a1f;color:#047857}.c-user{background:#d977061a;color:#b45309}
    .tog{border:0;background:none;color:var(--mut);font:600 11px/1 inherit;font-family:inherit;cursor:pointer;display:inline-flex;align-items:center;gap:4px;padding:6px 0 0}
    .tog svg{transition:transform .2s}.tog.open svg{transform:rotate(180deg)}
    .btns{display:flex;gap:6px;flex-wrap:wrap}
    .b{border:0;border-radius:10px;padding:8px 12px;font:600 12px/1 inherit;font-family:inherit;cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:transform .1s,background .15s}
    .b:active{transform:scale(.97)}
    .b1{background:var(--p);color:#fff;box-shadow:0 2px 8px rgba(37,99,235,.3)}.b1:hover{background:#1d4ed8}
    .b2{background:var(--soft);color:var(--fg)}.b2:hover{background:var(--bd)}
    .b3{background:none;color:var(--mut);padding:8px 6px}.b3:hover{color:var(--fg)}
    .b[disabled]{opacity:.55;cursor:wait}
    .grow{flex:1;justify-content:center}
    .msg{font-size:12px;color:var(--fg);background:var(--soft);border-radius:10px;padding:8px 10px}
    .ok{color:#047857}.bad{color:#b91c1c}
    .legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;color:var(--mut)}.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:-1px}
    .spin{width:12px;height:12px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:s .7s linear infinite}
    @keyframes s{to{transform:rotate(360deg)}}
    .ft{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-top:1px solid var(--bd);font-size:11px;color:var(--mut)}
    kbd{font:600 10px/1 inherit;font-family:inherit;border:1px solid var(--bd);border-bottom-width:2px;border-radius:4px;padding:2px 4px;background:var(--soft)}
    .logo .ini{background:linear-gradient(135deg,var(--p),var(--p2));width:100%;height:100%;display:grid;place-items:center}`;
  function ensureHost() {
    if (host?.isConnected) return;
    lastHtml = "";
    host = document.createElement("div");
    host.id = "tracker-assistant";
    const side = state.cfg?.panelSide === "left" ? "left" : "right";
    host.style.cssText = `position:fixed;${side}:16px;bottom:16px;top:auto;z-index:2147483646`;
    if (side === "right") host.style.left = "auto"; else host.style.right = "auto";
    shadow = host.attachShadow({ mode: "open" });
    document.documentElement.appendChild(host);
    try {
      const pos = JSON.parse(localStorage.getItem("trackerAssistantPos") || "null");
      if (pos?.left && pos?.top && parseInt(pos.left) < innerWidth - 60 && parseInt(pos.top) < innerHeight - 40) Object.assign(host.style, { left: pos.left, top: pos.top, right: "auto", bottom: "auto" });
    } catch {}
    const saved = tab.get().minimized;
    state.minimized = saved === undefined ? !!state.cfg?.startMinimized : saved === true;
    placeLayer(true);
  }
  // ---------------- camada: o painel fica visível mesmo com modal do site aberto ----------------
  // <dialog> modal e popovers ficam na "top layer", acima de qualquer z-index, e deixam o resto da página inerte.
  // Então o painel entra no modal aberto (fica clicável) e vira popover (fica por cima).
  function openModal() {
    let modals = [];
    try { modals = [...document.querySelectorAll("dialog:modal")]; } catch { modals = [...document.querySelectorAll("dialog[open]")]; }
    return modals.filter((d) => d.isConnected && visible(d)).pop() || null;
  }
  function raise(el) {
    if (!el?.isConnected || typeof el.showPopover !== "function") return;
    if (!el.hasAttribute("popover")) {
      el.setAttribute("popover", "manual");
      // popover vem com estilo próprio (centralizado, borda, fundo branco): neutraliza só o que o elemento
      // não definiu, para não apagar o fundo escuro e o espaçamento da sugestão 💡
      const neutral = { margin: "0", border: "0", padding: "0", background: "transparent", overflow: "visible", color: "inherit" };
      for (const [k, v] of Object.entries(neutral)) if (!el.style[k] && !(k === "background" && el.style.backgroundColor)) el.style[k] = v;
      for (const k of ["top", "right", "bottom", "left"]) if (!el.style[k]) el.style[k] = "auto"; // sem isso o popover estica (inset: 0)
      el.style.width = el.style.width || "max-content";
      el.style.height = el.style.height || "auto";
    }
    try { if (el.matches(":popover-open")) el.hidePopover(); el.showPopover(); } catch {}
  }
  let lastLayer = null;
  function placeLayer(force) {
    const parent = openModal() || document.documentElement;
    for (const el of [host, bubble]) {
      if (!el?.isConnected && el !== host) continue;
      if (!el) continue;
      if (el.parentNode !== parent) { parent.appendChild(el); force = true; }
    }
    // o painel volta para o topo quando aparece um modal ou popover novo
    const top = [...document.querySelectorAll(":popover-open, dialog[open]")].filter((x) => x !== host && x !== bubble).pop() || null;
    if (force || top !== lastLayer) { lastLayer = top; raise(host); if (bubble) raise(bubble); }
  }
  setInterval(() => { if (host?.isConnected) placeLayer(false); }, 700);

  const QUAD = { top: "Prioridade máxima", bet: "Aposta", volume: "Volume", discard: "Descartar", blocked: "Bloqueada" };
  const SRC = { profile: "perfil", bank: "banco", rule: "regra", user: "você", ai: "IA" };
  const scoreColor = (v) => (v >= 60 ? "#16a34a" : v >= 40 ? "#d97706" : "#dc2626");
  const initials = (n) => (n || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const isOwn = (node) => !!host && (node === host || host.contains(node));

  function render(busyMsg) {
    badge();
    if (!active() || !panelAllowed()) { if (host && !active()) teardown(); return; }
    if (!IS_TOP && !state.fields.length) return;
    if (!state.forcedPanel && !state.card && !state.pageInfo?.looks_like_job && !(state.fields.length && applyContext())) return;
    ensureHost();
    const c = state.card;
    const fieldsTotal = state.fields.length;
    const rows = state.fields.map((f) => ({ f, s: state.status.get(f.key) || { status: "empty" }, empty: isEmpty(f) }));
    const pending = rows.filter((r) => r.empty);
    const done = rows.filter((r) => !r.empty);
    if (state.minimized) {
      const fab = `fab|${pending.length}`;
      if (fab === lastHtml && shadow.getElementById("open")) return; // nada mudou: não redesenha (sem piscar)
      const enter = !lastHtml.startsWith("fab|") ? " enter" : "";
      lastHtml = fab;
      shadow.innerHTML = `<style>${CSS_TEXT}</style><div class="root"><button class="fab${enter}" id="open" title="Abrir o assistente (Alt+Shift+P)">${ICON.spark} Assistente${pending.length ? ` <span class="n">${pending.length}</span>` : ""}</button></div>`;
      shadow.getElementById("open").onclick = (e) => { if (e.isTrusted) { state.minimized = false; tab.set({ minimized: false }); render(); } };
      return;
    }
    let body = "";
    if (c?.known) {
      const s = c.score;
      const logo = state.logo ? `<img src="${esc(state.logo)}" alt="">` : `<span class="ini">${esc(initials(c.company))}</span>`;
      body += `<div class="job"><div class="logo">${logo}</div><div style="flex:1;min-width:0"><div class="jt">${esc(c.title)}</div><div class="muted">${esc(c.company)}${c.location ? ` · ${esc(c.location)}` : ""}</div>
        ${s ? `<div style="margin-top:4px"><span class="pill p-${esc(s.quadrant)}">${esc(QUAD[s.quadrant] || s.quadrant)} · ${Math.round(s.chance * 0.5 + s.attractiveness * 0.5)}</span></div>` : ""}</div></div>`;
      if (s) body += `<div class="bars"><div class="bar">Chance <b>${Math.round(s.chance)}</b><div class="track"><i style="width:${+s.chance || 0}%;background:${scoreColor(s.chance)}"></i></div></div>
        <div class="bar">Atratividade <b>${Math.round(s.attractiveness)}</b><div class="track"><i style="width:${+s.attractiveness || 0}%;background:${scoreColor(s.attractiveness)}"></i></div></div></div>`;
      const extra = [];
      if (c.responsiveness?.rate != null) extra.push(`Costuma responder ~${Math.round(c.responsiveness.rate * 100)}%`);
      if (c.reputation?.rating) extra.push(`Reputação ${Number(c.reputation.rating).toFixed(1)}`);
      if (c.reputation?.layoffs) extra.push('<span class="bad">layoff recente</span>');
      if (extra.length) body += `<div class="muted">${extra.join(" · ")}</div>`;
      if (s?.missing_must?.length) body += `<div class="muted">Você não tem: ${esc(s.missing_must.slice(0, 4).join(", "))}</div>`;
      if (c.application?.submitted_at) body += `<div class="msg ok">${ICON.check} Você já se candidatou a esta vaga.</div>`;
      body += tailorSection(c);
    } else if (state.pageInfo?.looks_like_job) {
      body += `<div class="sec"><h4>Vaga nova</h4><div class="muted">Ainda não está no seu tracker. Avaliar usa uma chamada de IA.</div><div class="btns" style="margin-top:8px"><button class="b b1 grow" id="an">Avaliar e salvar vaga</button></div></div>`;
    }
    if (fieldsTotal) {
      const pct = Math.round((done.length / fieldsTotal) * 100);
      body += `<div class="sec"><h4>Formulário <span class="muted" style="margin-left:auto">${done.length} de ${fieldsTotal}</span></h4><div class="prog" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${Math.max(4, pct)}%"></i></div>`;
      if (pending.length) {
        body += `<div class="muted">Faltam (clique para ir ao campo):</div><div class="list">${pending.slice(0, 12).map(({ f, s }) =>
          `<button class="it" data-go="${esc(f.key)}"><span>${esc(f.label)}</span><em class="chip c-${esc(s.status)}">${{ empty: "você responde", suggestion: "tem sugestão", sensitive: "sensível", ai: "IA" }[s.status] || "falta"}</em></button>`).join("")}</div>`;
        if (pending.length > 12) body += `<div class="muted">+${pending.length - 12} campo(s)</div>`;
      } else body += `<div class="muted ok">Tudo preenchido. Revise e clique em enviar no site.</div>`;
      if (done.length) {
        body += `<button class="tog ${state.showFilled ? "open" : ""}" id="tog">${ICON.chev} ${state.showFilled ? "Esconder" : "Revisar"} preenchidos (${done.length})</button>`;
        if (state.showFilled) body += `<div class="list">${done.map(({ f, s }) => {
          const tone = s.status === "ai" ? "ai" : s.status === "filled" ? "filled" : "user";
          return `<button class="it" data-go="${esc(f.key)}"><span>${esc(f.label)}</span><em class="chip c-${tone}">${esc(SRC[s.source] || (tone === "user" ? "você" : "assistente"))}</em></button>`;
        }).join("")}</div><div class="legend" style="margin-top:6px"><span><i style="background:${TONE.ok}"></i>do seu perfil</span><span><i style="background:${TONE.ai}"></i>IA (revise)</span></div>`;
      }
      const spin = '<i class="spin"></i>';
      body += `<div class="btns" style="margin-top:8px"><button class="b b1 grow" id="ai" ${state.busy ? "disabled" : ""} title="Alt+Shift+I">${state.busy ? spin : ICON.spark} Completar com IA</button>
        <button class="b b2" id="refill" ${state.busy ? "disabled" : ""} title="Preencher de novo (Alt+Shift+F)">${ICON.refresh}</button>
        <button class="b b2" id="undo" ${state.busy ? "disabled" : ""} title="Desfazer o que eu preenchi">${ICON.undo}</button></div></div>`;
    }
    const msg = busyMsg || state.msg;
    if (msg) body += `<div class="msg" role="status">${esc(msg)}</div>`;
    body += `<div class="btns"><button class="b b2 grow" id="sent">${ICON.check} Já me candidatei</button>${c?.known ? `<button class="b b2" id="openapp" title="Abrir no tracker">${ICON.ext}</button>` : ""}</div>`;
    const title = c?.known ? "Assistente" : "Assistente de candidatura";
    const sig = `card|${title}|${state.busy}|${body}`;
    if (sig === lastHtml && shadow.querySelector(".card")) return; // nada mudou: mantém o DOM (sem piscar nem perder o scroll)
    const enter = !lastHtml.startsWith("card|") ? " enter" : ""; // animação só quando o painel aparece
    const scroll = shadow.querySelector(".bd")?.scrollTop || 0;
    lastHtml = sig;
    shadow.innerHTML = `<style>${CSS_TEXT}</style><div class="root"><div class="card${enter}" role="dialog" aria-label="Assistente de candidatura">
      <div class="hd" title="Arraste para mover"><span class="brand">${ICON.spark}</span><span class="ttl">${title}<i class="dot ${state.busy ? "busy" : ""}"></i></span>
        <button class="ib" id="pause" title="Desligar neste site">${ICON.pause}</button><button class="ib" id="opts" title="Opções">${ICON.gear}</button>
        <button class="ib" id="min" title="Minimizar">${ICON.min}</button><button class="ib" id="close" title="Fechar nesta página">${ICON.x}</button></div>
      <div class="bd">${body}</div>
      <div class="ft"><span><kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> painel · <kbd>F</kbd> preencher · <kbd>I</kbd> IA</span></div></div></div>`;
    const bdEl = shadow.querySelector(".bd");
    if (bdEl && scroll) bdEl.scrollTop = scroll;
    const $ = (id) => shadow.getElementById(id);
    // só cliques reais do usuário: scripts da página não acionam o assistente
    const on = (id, fn) => { const el = $(id); if (el) el.onclick = (e) => { if (e.isTrusted) fn(e); }; };
    on("min", () => { state.minimized = true; tab.set({ minimized: true }); render(); });
    on("close", () => { state.hidden = true; state.forcedPanel = false; teardown(); });
    on("opts", () => bg({ type: "openOptions" }).catch(() => {}));
    on("pause", () => bg({ type: "pauseSite" }).catch(() => {}));
    const hd = shadow.querySelector(".hd");
    hd.onmousedown = (ev) => {
      if (ev.target.closest("button")) return;
      ev.preventDefault();
      const r = host.getBoundingClientRect();
      const dx = ev.clientX - r.left, dy = ev.clientY - r.top;
      const move = (m) => {
        host.style.left = `${Math.max(4, Math.min(innerWidth - r.width - 4, m.clientX - dx))}px`;
        host.style.top = `${Math.max(4, Math.min(innerHeight - 60, m.clientY - dy))}px`;
        host.style.right = "auto"; host.style.bottom = "auto";
      };
      const up = () => {
        removeEventListener("mousemove", move, true); removeEventListener("mouseup", up, true);
        try { localStorage.setItem("trackerAssistantPos", JSON.stringify({ left: host.style.left, top: host.style.top })); } catch {}
      };
      addEventListener("mousemove", move, true); addEventListener("mouseup", up, true);
    };
    on("an", () => analyze(false));
    on("ai", aiFill);
    on("refill", () => { state.status.clear(); state.failures = 0; autoFill(false, true); });
    on("undo", undoFill);
    on("tog", () => { state.showFilled = !state.showFilled; render(); });
    on("sent", () => reportSubmitted(true));
    on("tailor", startTailor);
    on("tailorApprove", approveTailor);
    on("tailorView", () => c?.tailored && bg({ type: "openPdf", variantId: c.tailored.variant_id }).catch((e) => say(e.message)));
    on("tailorEdit", () => bg({ type: "openDashboard", path: "/curriculos" }).catch(() => {}));
    on("tailorAttach", reattachResume);
    on("openapp", () => bg({ type: "openDashboard", path: `/vagas/${c.job_id}` }).catch(() => {}));
    shadow.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", (e) => {
      if (!e.isTrusted) return;
      const f = state.fields.find((x) => x.key === b.getAttribute("data-go"));
      const el = f && anchorOf(f);
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      setTimeout(() => el.focus({ preventScroll: true }), 350);
    }));
  }
  function say(m) { state.msg = m; render(); }

  // ---------------- vaga: classificar, avaliar, logo ----------------
  const pageText = () => (document.body?.innerText || "").slice(0, 20000);
  function localLooksRelevant() {
    if (knownHost()) return true;
    const hits = new Set((pageText().match(JOB_WORDS) || []).map((x) => x.toLowerCase())).size;
    return hits >= 2;
  }
  // formulário de candidatura: campo de CV, ou e-mail + LinkedIn/GitHub/portfólio, ou perguntas típicas de vaga
  const CV_LABEL = /curr[ií]culo|resum[eé]|\bcv\b|cover letter|carta de apresenta/i;
  const APPLY_LABEL = /linkedin|github|portf[oó]lio|pretens[aã]o salarial|salary expectation|sponsorship|visto|years of experience|anos de experi[eê]ncia|why do you want|por que voc[eê] quer/i;
  const formLooksLikeApplication = (fields) =>
    fields.some((f) => f.kind === "file" && CV_LABEL.test(f.label)) ||
    (fields.some((f) => f.kind === "email" || /e-?mail/i.test(f.label)) && fields.filter((f) => APPLY_LABEL.test(f.label)).length >= 1 && fields.length >= 3);
  async function loadLogo() {
    state.logo = null;
    if (!state.card?.company_id) return;
    const epoch = state.epoch;
    try { const url = await bg({ type: "logo", companyId: state.card.company_id }); if (epoch === state.epoch) { state.logo = url; render(); } } catch {}
  }
  async function classify() {
    const epoch = state.epoch;
    const info = await api("POST", "/page", { url: location.href, title: document.title, text: pageText(), has_form: state.fields.length > 0, session_job_id: tab.get().jobId || null });
    if (epoch !== state.epoch) return;
    if (info.known) {
      state.card = info;
      tab.set(info.flow_page ? { jobId: info.job_id } : { jobId: info.job_id, jobUrl: location.href, company: info.company, jobTitle: info.title });
      loadLogo();
      const tt = tab.get();
      if (tt.tailorTask && tt.tailorJob === info.job_id && !state.tailor) { state.tailor = { taskId: tt.tailorTask, msg: "Gerando com IA…" }; pollTailor(); }
      else if (info.tailor_task && !state.tailor) {
        // vaga boa com formulário aberto: o tracker já começou o CV sob medida enquanto você preenche
        state.tailor = { taskId: info.tailor_task, msg: "Preparando um CV para esta vaga enquanto você preenche…" };
        tab.set({ tailorTask: info.tailor_task, tailorJob: info.job_id });
        pollTailor();
      }
      if (info.needs_details) analyze(true); // vaga importada sem descrição: completa com o texto desta página
    } else {
      state.pageInfo = info;
      // outra vaga (desconhecida): não carrega a vaga anterior desta aba para cá
      if (info.looks_like_job && !info.flow_page) tab.set({ jobId: null, company: null, jobTitle: null, renderId: null, jobUrl: location.href, pageTitle: document.title });
    }
    if (info.auto_analyze && !info.known && state.cfg?.autoAnalyze !== false) await analyze(false);
    if (info.success_page && state.cfg?.autoRegister !== false && recentSubmit()) await reportSubmitted(false);
  }
  async function analyze(silent) {
    const epoch = state.epoch;
    try {
      if (!silent) say("Avaliando a vaga…");
      const card = await api("POST", "/analyze", { url: location.href, title: document.title, text: pageText() });
      if (epoch !== state.epoch) return;
      state.card = card;
      tab.set({ jobId: card.job_id, jobUrl: location.href, company: card.company, jobTitle: card.title });
      if (!silent) state.msg = "Vaga salva e avaliada.";
      loadLogo();
      render();
      if (state.fields.length) autoFill(true);
    } catch (e) { if (!silent) say(`Não foi possível avaliar: ${e.message}`); }
  }

  // ---------------- currículo sob medida para a vaga ----------------
  function tailorSection(c) {
    const t = c.tailored;
    const run = state.tailor;
    const spin = '<i class="spin" style="color:var(--p)"></i>';
    let html = `<div class="sec"><h4>📄 Currículo para esta vaga</h4>`;
    if (run) {
      html += `<div class="muted" style="display:flex;gap:8px;align-items:center">${spin} ${esc(run.msg || "Gerando com IA…")}</div>`;
    } else if (!t) {
      html += `<div class="muted">Um CV adaptado a esta vaga, só com fatos do seu currículo (o validador confere tudo).</div>
        <div class="btns" style="margin-top:8px"><button class="b b1 grow" id="tailor">${ICON.spark} Gerar currículo para esta vaga</button></div>`;
    } else if (!t.passed) {
      html += `<div class="muted bad">A IA citou algo que não está no seu currículo: corrija no tracker ou gere de novo.</div>
        <div class="btns" style="margin-top:8px"><button class="b b2 grow" id="tailorEdit">Corrigir no tracker</button><button class="b b2" id="tailor">Gerar de novo</button></div>`;
    } else if (t.status !== "approved") {
      html += `<div class="muted">Pronto! Revise e aprove: depois disso ele é o CV anexado nesta vaga.</div>
        <div class="btns" style="margin-top:8px"><button class="b b1 grow" id="tailorApprove">${ICON.check} Aprovar e usar</button>${t.has_pdf ? '<button class="b b2" id="tailorView">Ver PDF</button>' : ""}<button class="b b3" id="tailor" title="Gerar outra versão">${ICON.refresh}</button></div>`;
    } else {
      const fileField = state.fields.find((f) => f.kind === "file" && CV_LABEL.test(f.label));
      html += `<div class="muted ok">${ICON.check} Aprovado: é o CV anexado nesta vaga${t.filename ? ` (${esc(t.filename)})` : ""}.</div>
        <div class="btns" style="margin-top:8px">${fileField ? '<button class="b b2 grow" id="tailorAttach">Anexar este CV no formulário</button>' : ""}${t.has_pdf ? '<button class="b b2" id="tailorView">Ver PDF</button>' : ""}<button class="b b3" id="tailor" title="Gerar outra versão">${ICON.refresh}</button></div>`;
    }
    return html + "</div>";
  }
  async function startTailor() {
    const id = state.card?.job_id;
    if (!id || state.tailor) return;
    state.tailor = { msg: "Na fila…" };
    render();
    try {
      const { task_id } = await api("POST", "/tailor", { job_id: id });
      state.tailor = { taskId: task_id, msg: "Gerando com IA… (cerca de 30 a 60 segundos)" };
      tab.set({ tailorTask: task_id, tailorJob: id });
      pollTailor();
    } catch (e) { state.tailor = null; say(`Não consegui gerar: ${e.message}`); }
  }
  async function pollTailor() {
    const run = state.tailor;
    if (!run?.taskId) return;
    const epoch = state.epoch;
    try {
      const r = await api("GET", `/tasks/${run.taskId}`);
      if (epoch !== state.epoch) return;
      if (r.status === "done" || r.status === "failed") {
        state.tailor = null;
        tab.set({ tailorTask: null });
        if (r.status === "failed") return say(`A geração falhou: ${r.error || "tente de novo"}`);
        if (state.card) state.card = { ...state.card, tailored: r.tailored };
        state.msg = r.tailored?.passed ? "📄 Currículo pronto: revise e aprove." : "O validador barrou fatos fora do seu currículo.";
        return render();
      }
      state.tailor = { ...run, msg: r.message ? `${r.message}…` : run.msg };
      render();
    } catch {}
    setTimeout(pollTailor, 3000);
  }
  async function approveTailor() {
    const v = state.card?.tailored;
    if (!v) return;
    try {
      const r = await api("POST", `/tailored/${v.variant_id}/approve`);
      state.card = { ...state.card, tailored: r.tailored };
      state.msg = "✓ Aprovado: este CV será anexado nesta vaga.";
      render();
    } catch (e) { say(e.message); }
  }
  /** Troca o arquivo já anexado pelo CV aprovado da vaga. */
  async function reattachResume() {
    const f = state.fields.find((x) => x.kind === "file" && CV_LABEL.test(x.label));
    const el = f && byKey(f.key);
    if (!el) return say("Não achei o campo de currículo nesta página.");
    state.filling = true;
    try {
      el.value = "";
      const name = await attachResume(el, true);
      if (name) { state.status.set(f.key, { label: f.label, status: "filled", value: "__resume__", source: "profile", auto: true }); paint(el, "ok"); say(`✓ ${name} anexado.`); }
    } catch (e) { say(`Não consegui anexar: ${e.message}`); }
    finally { setTimeout(() => { state.filling = false; }, 50); }
  }

  // ---------------- sugestão ao clicar num campo ----------------
  let bubble, hideTimer;
  const cache = new Map();
  function hideBubble() { bubble?.remove(); bubble = null; }
  function bubbleBox() {
    const box = document.createElement("div");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Sugestão do assistente");
    box.style.cssText = "position:fixed;z-index:2147483647;max-width:380px;font:13px/1.4 -apple-system,BlinkMacSystemFont,Inter,'Segoe UI',sans-serif;background:#0f172a;color:#fff;padding:8px 10px;border-radius:12px;box-shadow:0 10px 30px rgba(2,6,23,.35);display:flex;align-items:center;gap:8px;flex-wrap:wrap";
    return box;
  }
  const bubbleBtn = (label, primary, action) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.style.cssText = `border:0;border-radius:8px;padding:5px 10px;cursor:pointer;font:600 12px/1 inherit;font-family:inherit;${primary ? "background:#2563eb;color:#fff" : "background:rgba(255,255,255,.12);color:#fff"}`;
    b.onmousedown = (ev) => { ev.preventDefault(); action(); }; // mouse: não tira o foco do campo
    b.onclick = (ev) => { if (ev.isTrusted && ev.detail === 0) action(); }; // teclado (Enter/Espaço)
    return b;
  };
  /** O painel nunca cobre o campo em que você está: troca de lado quando houver sobreposição. */
  let lastDodge = 0;
  function dodge(el) {
    if (!host || Date.now() - lastDodge < 2500) return;
    const a = host.getBoundingClientRect(), b = el.getBoundingClientRect();
    const hits = (l, r, tp, bt) => !(b.right < l || b.left > r || b.bottom < tp || b.top > bt);
    if (!hits(a.left, a.right, a.top, a.bottom)) return;
    const onRight = a.left > innerWidth / 2;
    // só troca de lado se do outro lado o campo fica livre (senão ficaria indo e voltando)
    const l2 = onRight ? 16 : innerWidth - 16 - a.width, t2 = innerHeight - 16 - a.height;
    if (hits(l2, l2 + a.width, t2, t2 + a.height)) return;
    lastDodge = Date.now();
    host.style.transition = "opacity .15s";
    host.style.opacity = "0";
    setTimeout(() => {
      Object.assign(host.style, { top: "auto", bottom: "16px" });
      if (onRight) Object.assign(host.style, { left: "16px", right: "auto" }); else Object.assign(host.style, { right: "16px", left: "auto" });
      host.style.opacity = "1";
    }, 150);
  }
  async function onFocus(e) {
    if (state.filling || !active() || !state.relevant) return;
    clearTimeout(hideTimer);
    const el = e.target;
    if (!(el instanceof HTMLElement) || !el.matches("input, select, textarea") || isOwn(el)) return;
    dodge(el);
    if (!e.isTrusted || state.cfg?.suggestOnFocus === false) return;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (["hidden", "password", "file", "submit", "button", "checkbox", "radio"].includes(type) || skipEl(el, type)) return;
    if (el.value && el.tagName !== "SELECT") return;
    const field = extractFields().find((f) => f.key === keys.get(el));
    if (!field) return;
    const show = (s, aiTried) => {
      if (document.activeElement !== el) return;
      hideBubble();
      const r = el.getBoundingClientRect();
      bubble = bubbleBox();
      bubble.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 390))}px`;
      bubble.style.top = `${r.bottom + 6 + 60 > innerHeight ? r.top - 52 : r.bottom + 6}px`;
      const val = s?.value;
      const label = document.createElement("span");
      label.style.cssText = "flex:1;min-width:0";
      label.innerHTML = val ? `💡 <b>${esc(String(val).slice(0, 220))}</b>` : s?.sensitive ? "🔒 Pergunta sensível: responda você" : "Sem resposta salva para esta pergunta.";
      bubble.appendChild(label);
      if (val) {
        bubble.appendChild(bubbleBtn("Usar", true, async () => {
          state.filling = true;
          const ok = await setValue(field, val).finally(() => setTimeout(() => { state.filling = false; }, 50));
          const isAi = s.source === "ai";
          state.status.set(field.key, { label: field.label, status: ok ? (isAi ? "ai" : "filled") : "empty", value: val, source: s.source || "bank", auto: ok });
          if (ok) paint(anchorOf(field) || el, isAi ? "ai" : "ok");
          hideBubble();
          render();
        }));
      }
      if (!aiTried && !val && !s?.sensitive && ["text", "textarea", "typeahead"].includes(field.kind)) {
        bubble.appendChild(bubbleBtn("✨ Gerar com IA", false, async () => {
          label.textContent = "✨ Escrevendo…";
          try { show(await api("POST", "/suggest", { url: location.href, job_id: jobId(), field, allow_ai: true }), true); } catch (er) { label.textContent = er.message; }
        }));
      }
      (openModal() || document.documentElement).appendChild(bubble);
      raise(bubble);
    };
    const known = state.status.get(field.key);
    if (known?.value && known.status === "suggestion") return show({ value: known.value, source: "ai" }, false);
    const ck = `${jobId()}|${field.label}|${field.options.join(";")}`;
    try {
      const s = cache.get(ck) ?? (await api("POST", "/suggest", { url: location.href, job_id: jobId(), field, allow_ai: false }));
      cache.set(ck, s);
      show(s, false);
    } catch {}
  }

  // ---------------- aprendizado: só o que VOCÊ digitou/escolheu ----------------
  function onChange(e) {
    if (!active() || !e.isTrusted || state.filling) return; // eventos do próprio assistente ou do site não contam
    const el = e.target;
    if (!(el instanceof HTMLElement) || !el.matches("input, select, textarea") || isOwn(el)) return;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (["password", "hidden", "file"].includes(type) || skipEl(el, type)) return;
    let label, value, kind = "text", options = [];
    if (type === "radio") { label = groupQuestion(el, [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name || "")}"]`)]); value = radioOptionLabel(el); kind = "radio"; }
    else {
      label = labelFor(el); kind = kindOf(el);
      value = kind === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.options[el.selectedIndex]?.text?.trim() : el.value;
      if (el.tagName === "SELECT") options = [...el.options].map((o) => o.text.trim()).filter(Boolean);
    }
    const key = keys.get(el);
    if (label && value !== null && value !== "" && value !== undefined) {
      state.edits.set(label, { question: label.slice(0, 500), value, field_kind: kind, options: options.slice(0, 50), source: "user" });
      tab.set({ answers: answers() });
    }
    if (key && state.status.has(key)) {
      const st = state.status.get(key);
      state.status.set(key, { ...st, status: "user", source: "user", auto: false });
      const f = state.fields.find((x) => x.key === key);
      if (f) paint(anchorOf(f), "you");
      clearTimeout(renderTimer); renderTimer = setTimeout(() => render(), 300);
    }
  }
  let renderTimer;
  /** Respostas desta candidatura: o que o assistente preencheu (com a origem) + o que você digitou. */
  function answers() {
    const out = new Map();
    for (const st of state.status.values()) {
      if (!st.auto || st.value == null || st.value === "__resume__" || !st.label) continue;
      out.set(st.label, { question: st.label.slice(0, 500), value: st.value, field_kind: "text", options: [], source: ["profile", "bank", "rule", "ai", "user"].includes(st.source) ? st.source : "rule" });
    }
    for (const [k, v] of state.edits) out.set(k, v);
    return [...out.values()].slice(0, 200);
  }

  // ---------------- detecção de envio ----------------
  async function reportSubmitted(manual) {
    if (state.reported) return;
    const s = tab.get();
    if (!manual && !s.jobUrl && !state.card) return;
    state.reported = true;
    try {
      const res = await api("POST", "/submitted", {
        url: s.jobUrl || location.href, title: s.pageTitle || document.title, text: manual && !state.card ? pageText() : "",
        job_id: jobId(), company: s.company || null, job_title: s.jobTitle || null,
        answers: s.answers?.length ? s.answers : answers(), render_id: s.renderId || null,
      });
      tab.set({ answers: [], submittedAppId: res.application_id, minimized: false, formSubmitted: 0 });
      if (!res.already) bg({ type: "stat", field: "apps" }).catch(() => {});
      state.minimized = false; state.hidden = false; state.forcedPanel = true; // confirmação importante: o painel abre para mostrar
      say(res.already ? "Esta candidatura já estava no seu acompanhamento." : "✓ Candidatura adicionada ao seu acompanhamento.");
    } catch (e) { state.reported = false; say(`Erro ao registrar: ${explainError(e)}`); }
  }
  function markSubmit() { tab.set({ formSubmitted: Date.now(), answers: answers() }); }

  // ---------------- observação da página ----------------
  let scanTimer, observer;
  function watch() {
    observer = new MutationObserver((muts) => {
      if (muts.every((m) => isOwn(m.target))) return;
      clearTimeout(scanTimer);
      scanTimer = setTimeout(() => {
        if (!active()) return;
        if (state.cfg?.autoRegister !== false && recentSubmit() && SUCCESS.test(pageText())) { reportSubmitted(false); return; }
        // formulários em etapas: campos novos são preenchidos assim que aparecem
        const all = extractFields();
        const fresh = all.filter((f) => !state.status.has(f.key));
        if (!fresh.length) return;
        if (!state.relevant && formLooksLikeApplication(all)) { state.relevant = true; state.fields = all; classify().catch(() => {}).finally(() => render()); }
        if (state.relevant) autoFill(true);
      }, 900);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  // ---------------- início ----------------
  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    document.addEventListener("focusin", onFocus, true);
    document.addEventListener("mousemove", (ev) => {
      const t = ev.target;
      if (state.relevant && !state.filling && t instanceof HTMLElement && t.matches?.("input, select, textarea") && !isOwn(t)) dodge(t);
    }, { capture: true, passive: true });
    document.addEventListener("focusout", () => { clearTimeout(hideTimer); hideTimer = setTimeout(hideBubble, 200); }, true);
    document.addEventListener("change", onChange, true);
    document.addEventListener("submit", (e) => { if (e.isTrusted) markSubmit(); }, true);
    // muitos sites enviam por botão sem <form>: o clique de verdade num botão de envio conta como envio
    document.addEventListener("click", (e) => {
      if (!e.isTrusted) return;
      const b = e.target instanceof Element ? e.target.closest("button, input[type=submit], [role=button]") : null;
      if (b && !isOwn(b) && SUBMIT_WORDS.test((b.innerText || b.value || b.getAttribute("aria-label") || "").trim())) markSubmit();
    }, true);
    watch();
  }
  async function start() {
    const epoch = state.epoch;
    state.cfg = await bg({ type: "config" }).catch(() => null);
    await tab.load();
    if (!active() || epoch !== state.epoch) return;
    if (state.cfg.autoRegister !== false && tab.get().jobUrl && recentSubmit() && SUCCESS.test(pageText())) { state.relevant = true; wire(); await reportSubmitted(false); return; }
    state.fields = extractFields();
    // iframes: só formulários de ATS conhecidos (Greenhouse/Lever embutidos), nunca de terceiros quaisquer
    if (!IS_TOP && !(knownHost() && state.fields.length)) return;
    state.relevant = localLooksRelevant() || formLooksLikeApplication(state.fields) || !!tab.get().jobId;
    wire(); // escuta mudanças (formulário que aparece depois), mas só age quando a página é relevante
    if (!state.relevant) return;
    try { await classify(); } catch { state.pageInfo = { looks_like_job: false }; }
    if (epoch !== state.epoch) return;
    render();
    if (state.fields.length) autoFill(false);
  }

  function reset() {
    state.epoch++;
    state.card = null; state.pageInfo = null; state.reported = false; state.status.clear(); state.edits.clear(); cache.clear();
    state.msg = ""; state.logo = null; state.tailor = null; state.fields = []; state.failures = 0; state.hidden = false; state.forcedPanel = false;
    hideBubble();
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "tracker:config-changed") {
      bg({ type: "config" }).then((c) => {
        const was = active();
        state.cfg = c;
        if (!active()) teardown();
        else if (!was) { reset(); start(); }
        else render();
      }).catch(() => {});
      return;
    }
    if (!IS_TOP && msg.type === "tracker:show-panel") return;
    if (!state.cfg || !active()) return;
    if (msg.type === "tracker:show-panel") {
      // alterna: se já está aberto, minimiza
      state.forcedPanel = true; state.hidden = false; state.relevant = true;
      state.minimized = host?.isConnected && !state.minimized ? true : false;
      tab.set({ minimized: state.minimized });
      wire();
      state.fields = extractFields();
      if (state.card) render(); else classify().catch(() => {}).finally(() => render());
    }
    if (msg.type === "tracker:fill") { state.forcedPanel = true; state.relevant = true; wire(); state.status.clear(); state.failures = 0; autoFill(false, true); }
    if (msg.type === "tracker:ai") { state.forcedPanel = true; state.relevant = true; wire(); aiFill(); }
  });

  // SPAs (LinkedIn etc.) mudam de URL sem recarregar
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      reset();
      if (host) render();
      setTimeout(start, 1200);
    }
  }, 1000);

  setTimeout(start, 800);
})();
