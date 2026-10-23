# 15 · Decisões de arquitetura (ADRs)

Formato curto: contexto → decisão → consequências. Novas decisões são adicionadas no fim.

### ADR-001 · Next.js + Python (FastAPI)
**Contexto:** preferência do usuário; o ecossistema Python é o melhor para automação de navegador, IA e dados.
**Decisão:** frontend Next.js (App Router) sem regra de negócio; backend FastAPI.
**Consequências:** duas linguagens; tipos sincronizados via OpenAPI gerado.

### ADR-002 · Arquitetura híbrida (hospedado + agente local) · *substituída pela ADR-020*
**Contexto:** Gmail push, Telegram e dashboard no celular pedem disponibilidade 24 h; o LinkedIn exige a sessão real, o IP residencial e um navegador comum.
**Decisão:** API, workers e banco hospedados; o agente Playwright roda na máquina do usuário com conexão de saída.
**Consequências:** o Easy Apply só roda com o computador ligado; também existe um modo 100% local.

### ADR-003 · Playwright em vez de Selenium ou extensão de navegador · *substituída pela ADR-020*
**Contexto:** modais de várias etapas, uploads, esperas.
**Decisão:** Playwright (Python) com contexto persistente e localizadores por papel/rótulo.
**Alternativa considerada:** extensão do Chrome (menor risco de detecção, mas separa a lógica em JS e complica a orquestração). Pode ser reavaliada se houver bloqueios.

### ADR-004 · Celery + Redis
**Decisão:** Celery (com Beat) para filas e agendamentos; Redis também para pub/sub do WebSocket.
**Alternativas:** ARQ (mais simples, async nativo), Dramatiq. Celery escolhido pela maturidade do Beat e das ferramentas.

### ADR-005 · PostgreSQL + pgvector
**Decisão:** um banco só para relacional + vetores (dedup, matching, banco de respostas).
**Consequências:** sem um serviço de vetores separado; índices HNSW.

### ADR-006 · Embeddings locais multilíngues
**Contexto:** o Claude não oferece embeddings; o conteúdo é PT e EN; o volume é baixo.
**Decisão:** `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (768 dim) local via fastembed (ONNX, sem PyTorch).
**Consequências:** custo zero, ~1 GB de disco e RAM no worker; testes usam um embedder determinístico por hash; trocar por Voyage AI se a qualidade não bastar.

### ADR-007 · Claude Opus 5.5 como padrão, modelo configurável por tarefa
**Decisão:** `claude-opus-5-5` em todas as tarefas, com effort por tarefa; troca de modelo por rota só após evals e por decisão do usuário.
**Consequências:** qualidade máxima no começo; custo controlado por cache, batches, effort e pré-filtros.

### ADR-008 · LaTeX para currículos
**Contexto:** currículos de tecnologia bem avaliados por ATS e recrutadores costumam ser feitos em LaTeX (tipografia limpa, texto extraível).
**Decisão:** templates Jinja2 → LaTeX, compilados com tectonic (ou TeX Live) em container isolado. Estilo `classic` em `templates/resumes/classic/`. Substitui a ideia inicial de Typst.
**Consequências:** visual profissional e consistente; compilação mais pesada (container); escape rigoroso obrigatório.

### ADR-009 · Nada é enviado em nome do usuário sem política; e-mail e mensagem nunca
**Decisão:** candidaturas seguem `automation_policy` (padrão: revisão); e-mails e mensagens do LinkedIn são sempre rascunhos. O escopo `gmail.compose` é usado só para rascunhos.
**Consequências:** um pouco mais de trabalho manual; risco reputacional e de conta muito menor.

### ADR-010 · Pesquisa de reputação via pesquisa web do Claude
**Contexto:** Glassdoor e similares proíbem scraping.
**Decisão:** `web_search`/`web_fetch` server-side do Claude com fontes citadas e cache por empresa.
**Consequências:** custo por pesquisa (mitigado pelo cache de 30–60 dias); a cobertura depende do que é público.

### ADR-011 · Telegram como canal de notificação e ação
**Decisão:** bot do Telegram (gratuito, API simples, botões inline).
**Alternativa:** WhatsApp Business API (pago, burocrático). Pode ser adicionado depois.

### ADR-012 · Dados pessoais fora do git
**Decisão:** `data/` (seed do mestre, CVs pessoais, exportações) no `.gitignore`; o repositório contém só código, templates e documentação.

### ADR-013 · Storage local no desenvolvimento, S3 em produção
**Contexto:** a imagem `minio/minio` deixou de ser publicada no Docker Hub.
**Decisão:** interface `Storage` com dois backends: `local` (pasta `STORAGE_LOCAL_DIR`, padrão `data/storage/`) para desenvolvimento e 100% local; `s3` (Cloudflare R2, Backblaze B2 ou qualquer S3) para produção.
**Consequências:** um container a menos no dev; URLs de download servidas pela API no modo local.

### ADR-014 · Protocolo do agente via HTTP com heartbeat curto
**Contexto:** o protocolo previa WebSocket; a parte crítica (kill switch em ≤ 5 s) só precisa de sinalização frequente.
**Decisão:** o agente fala com a API por HTTP (`/api/v1/agent/*`, token de dispositivo) e envia heartbeat a cada 2 s durante uma tarefa; a resposta do heartbeat carrega o sinal de parada. Pausas (pergunta pendente, revisão) liberam a tarefa: ao ser retomada, o agente reabre a vaga e preenche de novo (as respostas já estão salvas), sem manter o navegador preso.
**Consequências:** mais simples e robusto a quedas de rede; o dashboard continua recebendo os eventos em tempo real pelo WebSocket da API.

### ADR-015 · `gmail.modify` apenas para marcadores
**Contexto:** o usuário quer os e-mails de vagas separados no Gmail.
**Decisão:** pedir `gmail.modify` e usá-lo somente para criar e aplicar marcadores `Vagas/*`. Nenhum código envia e-mail (`gmail.send` não é pedido; um teste estático impede chamadas de envio).
**Consequências:** quem já conectou o Google precisa reconectar para conceder o escopo; sem ele, a classificação continua e só os marcadores são pulados.

### ADR-016 · Extensão do navegador em vez de robô para candidaturas externas
**Contexto:** robôs em ATS variados são frágeis e parecem bots; o usuário prefere aplicar na própria sessão.
**Decisão:** extensão MV3 que lê a página, preenche (sem enviar) e registra o envio, conversando com a API local. Fontes de vagas restritas a APIs públicas e boards com termos permissivos; sem scraping de sites que proíbem.
**Consequências:** zero risco de bloqueio por automação nesses sites; o usuário clica em "enviar". A Gupy não tem API agregada pública (404); usamos o portal de cada empresa.

### ADR-017 · Claude Code local como backend de IA (sem chave de API)
**Contexto:** o app roda só no PC do usuário, que já tem assinatura do Claude Code; uma chave de API seria um custo à parte.
**Decisão:** `AI_BACKEND=auto` usa a API quando há `ANTHROPIC_API_KEY` e, sem chave, o CLI `claude -p` (`ClaudeCodeClient`). Mesmo contrato do wrapper: saída estruturada (`--json-schema`), effort por tarefa, cache por hash e registro em `ai_call`. Cada chamada roda num diretório temporário vazio, sem sessão salva, sem configurações/MCP do usuário e só com as ferramentas da tarefa (`WebSearch`/`WebFetch` para pesquisa, `Read` para anexos PDF). As fontes reais vêm do stream de eventos (links devolvidos pela busca e páginas lidas), então a checagem de afirmações sem fonte continua valendo.
**Consequências:** custo por token zero (conta no limite de uso da assinatura; `ai_call.cost_usd` = 0 e o orçamento mensal não bloqueia); latência maior por chamada (~3–10 s, pesquisa de empresa ~2 min); exige o CLI atualizado (Opus 5.5 pede ≥ 2.1.280) e logado na máquina onde roda o worker.

### ADR-018 · Telegram por long polling quando o app é local
**Contexto:** webhook exige URL pública; o app roda em `localhost`.
**Decisão:** `TELEGRAM_MODE=auto` liga um laço `getUpdates` dentro da API quando `API_BASE_URL` é local (remove o webhook antes); com URL pública, usa o webhook. Os dois caminhos chamam o mesmo `handle_update`.
**Consequências:** botões e comandos funcionam sem expor a máquina; rodar só um processo da API para não duplicar respostas.

### ADR-019 · App para qualquer candidato, em qualquer sistema
**Contexto:** o app nasceu para uma pessoa e tinha dados dela no código (e-mail, empregadores bloqueados, foco dos currículos).
**Decisão:** nada pessoal no código nem nos docs. O primeiro acesso abre o **onboarding** (IA → currículo por PDF do LinkedIn/CV → preferências → emprego atual → integrações), com guias passo a passo dentro do app e em `docs/guia-configuracao.md`. Padrões derivam do currículo de quem usa (empregador atual bloqueado, maior formação). Conta Google = a que for conectada. Multiplataforma: worker Celery em threads (prefork não existe no Windows e duplicava o modelo de embeddings), `tracker-init` gera o `.env` sem openssl, `tracker-dev` sobe API + worker + agendador num comando, `shlex` sem modo POSIX no Windows; CI roda um smoke em Windows e macOS.
**Consequências:** qualquer pessoa clona, roda e configura pelo navegador; os dados de cada um ficam só no banco e em `data/`.

### ADR-020 · Sem robô: candidatura com o assistente do navegador
**Contexto:** o robô local (Playwright no LinkedIn) exigia login numa janela automatizada (o Google bloqueia), ficava parado sem tarefas e trazia risco à conta. A extensão já preenchia formulários na sessão real do usuário.
**Decisão:** remover o robô por completo (`tracker-agent`, `agent_gateway`, fila `agent_job`, kill switch, limites do LinkedIn, comandos do Telegram ligados a ele). Vagas do LinkedIn entram por **alertas de vaga no Gmail**; o usuário também pode abrir e avaliar manualmente uma vaga individual na extensão. A extensão não coleta listas nem resultados de busca. Ela auxilia no preenchimento e registra o envio, mas não envia candidaturas.
**Consequências:** sem coleta de resultados de busca nem cliques automatizados; as ações na página são iniciadas pelo usuário; migração `b2f1c0d7a913` remove as tabelas do robô.

### ADR-021 · Endurecimento para uso local (revisão de segurança)
**Contexto:** a revisão encontrou serviços escutando em todas as interfaces (Next, Postgres, Redis com senha padrão), login de desenvolvimento acessível pela rede, CORS aberto a qualquer extensão e o token da extensão chegando ao content script.
**Decisão:** Postgres, Redis e o dashboard só em `127.0.0.1`; `TrustedHostMiddleware` (hosts de `APP_BASE_URL`/`API_BASE_URL` + `ALLOWED_HOSTS`); `dev-login` só de loopback e com Host local; sem CORS para extensões (o service worker usa permissão de host); `/assistant` aceita o cookie só fora de origens de extensão e com CSRF; token em `chrome.storage.local`, nunca entregue às páginas, e allowlist de endpoints no service worker; tarefas enviadas ao worker só depois do commit (`dispatch_after_commit`); `claude -p` com ambiente mínimo e PDF extraído localmente; Next 15.5.28.
**Consequências:** acessar o app por outro host exige `ALLOWED_HOSTS`; API fora de localhost pede permissão de host na extensão.
