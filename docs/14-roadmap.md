# 14 · Roadmap

Ordem pensada para entregar valor cedo **sem risco para a conta do LinkedIn**: as fases F1–F4 já resolvem a maior parte da rotina. Cada fase termina com uma demo verificável.

Estimativas para dedicação parcial com assistência de IA.

---

## F0 · Fundação (1 semana)
**Objetivo:** esqueleto rodando de ponta a ponta.
- [x] Monorepo conforme `04-arquitetura.md` §3; `.env.example`; `docker-compose.yml` (Postgres+pgvector, Redis; storage local, ADR-013)
- [x] Backend: FastAPI, config (pydantic-settings), SQLAlchemy async, Alembic, logging JSON, `audit_log`, crypto (AES-GCM), feature flags
- [x] Celery worker + beat com uma tarefa de exemplo
- [x] Auth: login Google restrito a `AUTH_ALLOWED_EMAIL` (RF-PLA-01)
- [x] Frontend: Next.js + Tailwind + shadcn/ui, layout do dashboard, cliente OpenAPI gerado, WebSocket conectado
- [x] Wrapper `integrations/claude/client.py` com `ai_call` e custo
- [x] Backup diário (RF-PLA-06)
- [x] CI básico
- [x] Validar Playwright com janela visível no WSL2 (WSLg) ou no Windows
**Demo:** login → tela "Hoje" vazia → tarefa de teste aparece em tempo real.

## F1 · Perfil e currículos (1–2 semanas)
- [x] Importar `data/master-profile.yaml` como versão 1 do mestre; editor do mestre; versões e diff (RF-PER-04)
- [x] Importação de PDF/DOCX com revisão (RF-PER-02)
- [x] Tradução PT↔EN do mestre (RF-PER-05)
- [x] Preferências, banco de respostas inicial (RF-PER-07/08)
- [x] Render LaTeX do `classic` via tectonic em container (RF-CV-01), validado contra o PDF de referência
- [x] Validador de fatos (RF-CV-05) e checagem ATS (RF-CV-06)
- [x] Coringas iniciais (`11-curriculos.md` §4) em EN e PT (RF-CV-02)
- [x] CV específico a partir de uma vaga colada (RF-CV-04), versionamento (RF-CV-08)
- [x] Painel de custo de IA (RF-PLA-04)
**Demo:** colar uma vaga → receber um PDF específico no estilo `classic`, validado e com diff.

## F2 · Vagas, avaliação e tracker (2 semanas)
- [x] Importar vaga por URL/texto (RF-VAG-05), normalização (RF-VAG-06), dedup (RF-VAG-07), snapshots (RF-VAG-08)
- [x] Greenhouse, Lever, Ashby + empresas monitoradas + watchlist (RF-VAG-03/09)
- [x] Buscas salvas agendadas para fontes com API (RF-VAG-01)
- [x] Filtros eliminatórios, Chance, Atratividade básica, quadrantes, explicação (RF-AVA-01..05, 09, 10)
- [x] Escolha do CV por vaga e idioma (RF-CV-03/09); carta (RF-CV-07)
- [x] Candidaturas: máquina de estados, linha do tempo, registro manual "enviei" (RF-EXT-04, RF-ACO-11)
- [x] Dashboard: Hoje, lista de vagas com cartão de avaliação, kanban, detalhe (RF-DSH-01..03, 05, 10)
- [x] Lista de bloqueio, políticas (RF-PER-09, RF-PLA-02)
**Demo:** vagas de 5 empresas monitoradas ranqueadas por prioridade, com motivos; candidatura manual registrada no kanban.

## F3 · Gmail, agenda e notificações (2 semanas)
- [x] OAuth Gmail + Calendar com app publicado (RF-ACO-01, RF-AGE-01)
- [x] Ingestão por push + fallback, pré-filtro, classificação, associação, status automático (RF-ACO-02..07, 10)
- [x] Rascunhos no Gmail (`draft.reply`) para disponibilidade (RF-AGE-03)
- [x] Eventos: convite, prazo de teste, rascunho/confirmação, conflitos, fusos, remarcação (RF-AGE-02, 04..06, 08..10)
- [x] Telegram: vínculo, alertas, silêncio, resumo diário e semanal (RF-NOT-01, 02, 04, 05)
- [x] Funil e relatório semanal (RF-ANA-01, 07); saúde das integrações (RF-PLA-05); PWA responsivo (RF-DSH-11)
**Demo:** um e-mail de convite real vira status "entrevista" + evento no Calendar + alerta no Telegram em menos de 5 min.

## F4 · Entrevista (1–2 semanas)
- [x] Perfil de empresa por pesquisa web com cache (RF-AVA-06)
- [x] Banco de histórias STAR (RF-PER-06)
- [x] Dossiê completo + página de cola (RF-ENT-01/02), bloco de estudo (RF-AGE-07)
- [x] Simulação de entrevista (RF-ENT-03)
- [x] Pós-entrevista, agradecimento, banco de perguntas, testes técnicos (RF-ENT-04..07)
**Demo:** entrevista marcada → dossiê com fontes pronto + simulação funcionando.

## F5 · Easy Apply assistido (2–3 semanas) · *robô removido na F10 (ADR-020)*
- [x] Agente local: navegador persistente, ritmo humano, segurança, kill switch, protocolo WS (RF-CAN-02, 09..11)
- [x] Busca de vagas no LinkedIn (RF-VAG-02, 10)
- [x] Motor do modal + resolução de perguntas + pendências + Telegram (RF-CAN-03..06, RF-NOT-03)
- [x] Modo revisão + fila com atalhos + aprovação em lote (RF-CAN-07, RF-DSH-04)
- [x] Dry run, snapshot imutável, transmissão ao vivo, fallback por IA (RF-CAN-12..15)
- [x] Editor do banco de respostas (RF-DSH-07)
**Critério de saída:** 10 candidaturas em dry run + 20 reais em modo revisão sem incidente.

## F6 · Externas, mensagens e relacionamento (2 semanas)
- [x] Pacote para candidatura externa + preenchimento assistido Greenhouse/Lever/Gupy (RF-EXT-01..03, 05)
- [x] Fonte Gupy (RF-VAG-04)
- [x] Mensagens do LinkedIn (somente leitura) (RF-ACO-09)
- [x] CRM de pessoas, recrutador da vaga, indicações (RF-REL-01..03)
- [x] Follow-ups (RF-ACO-08), desistência (RF-CAN-16)
- [x] Cofre de documentos, referências, busca global, exportação/exclusão (RF-PER-10/11, RF-DSH-12, RF-PLA-07)

## F7 · Automação e inteligência (contínuo)
- [x] Modo automático com limites (RF-CAN-08)
- [x] Responsividade bayesiana + calibração dos pesos (RF-AVA-07/08)
- [x] Desempenho por CV, gaps de skills, plano de estudo, rejeições, metas (RF-ANA-02..05, 08)
- [x] Otimização do perfil do LinkedIn (RF-ANA-06); sugestão de novos coringas (RF-CV-10); GitHub (RF-PER-03)
- [x] Ofertas: registro, CLT x PJ, referência salarial, negociação (RF-OFE-01..05)

## F8 · Assistente no navegador e fontes (concluída)
- [x] Extensão MV3: painel com cartão da vaga, preenchimento, sugestão no foco, aprendizado, detecção de envio (RF-NAV-01..07)
- [x] API `/api/v1/assistant/*` com token próprio e CORS para `chrome-extension://`
- [x] ATS SmartRecruiters e Workable; 7 boards; 100 empresas verificadas; filtro de relevância (RF-FON-01..04)
- [x] Marcadores `Vagas/*` no Gmail (RF-INB-01)
- [x] Emprego atual como base no comparador de ofertas (PLR, previdência, custos)

## F9 · Para qualquer candidato (concluída)
- [x] Onboarding guiado: IA, currículo (PDF do LinkedIn/CV), preferências, emprego atual, integrações (ADR-019)
- [x] Guias de configuração no app e em `docs/guia-configuracao.md`
- [x] Nada pessoal no código; padrões derivados do currículo de quem usa
- [x] Windows, macOS, Linux e WSL (`tracker-init`, `tracker-dev`, worker em threads)
- [x] Filtros de vagas por tecnologia, empresa, salário, modelo, senioridade, regime, local, data e notas
- [x] Interface refeita: tema claro/escuro, animações (Motion), kanban animado, cartões de vaga com notas em anel

## F10 · Sem robô, extensão no centro (concluída)
- [x] Robô local removido (código, fila, tabelas, configurações, telas) (ADR-020)
- [x] Vagas do LinkedIn por alertas no Gmail e análise manual de páginas individuais
- [x] Extensão: preenchimento automático ao detectar o formulário, etapas seguintes, "Completar com IA", sugestão com "Usar", equivalências de opções, painel novo que não cobre campos
