// Opções da extensão (fonte única: popup, opções, service worker e páginas usam este arquivo).
// O token NÃO fica aqui: vai em chrome.storage.local (só neste navegador) e nunca chega às páginas.
globalThis.TRACKER_DEFAULTS = {
  apiUrl: "http://localhost:8000",
  dashboardUrl: "http://localhost:3100",
  enabled: true, // liga/desliga geral
  pausedUntil: 0, // pausa temporária (timestamp em ms); 0 = sem pausa
  autoFill: true, // preenche sozinho quando um formulário de candidatura aparece
  autoAi: true, // depois do preenchimento, a IA escreve as perguntas abertas (você revisa; nada é enviado)
  attachResume: true, // anexa o PDF do CV escolhido para a vaga nos campos de arquivo
  highlight: true, // contornos coloridos nos campos preenchidos (verde perfil · azul IA · laranja você)
  panelMode: "auto", // auto: vagas e formulários · forms: só formulários · manual: só pelo ícone/atalho
  panelSide: "right", // lado inicial do painel
  startMinimized: false,
  suggestOnFocus: true, // bolha 💡 ao clicar num campo vazio
  autoAnalyze: true, // avaliar vagas novas sozinho em sites de vagas conhecidos (usa IA)
  autoRegister: true, // registrar a candidatura ao detectar a confirmação depois que você envia
  ignoredDomains: ["mail.google.com", "web.whatsapp.com", "localhost:3100", "127.0.0.1:3100"],
};
