# Guia de configuração

Passo a passo para rodar o Application Tracker no seu computador (Windows, macOS, Linux ou WSL) e ligar cada integração. Os mesmos guias aparecem dentro do app, no **onboarding** e em **Configurações → Integrações**.

> Tempo total: uns 20 minutos para o essencial (instalar, IA, currículo). Gmail, Telegram e extensão são opcionais e podem ficar para depois.

- [1. Instalar o que o app precisa](#1-instalar-o-que-o-app-precisa)
- [2. Baixar e configurar o projeto](#2-baixar-e-configurar-o-projeto)
- [3. Subir o app](#3-subir-o-app)
- [4. IA (Claude)](#4-ia-claude)
- [5. Google: Gmail e Agenda](#google)
- [6. Telegram](#telegram)
- [7. Extensão do navegador](#extensao)
- [8. Vagas do LinkedIn](#8-vagas-do-linkedin)
- [9. Problemas comuns](#9-problemas-comuns)

---

## 1. Instalar o que o app precisa

| Ferramenta | Para quê | Windows | macOS | Linux / WSL |
|---|---|---|---|---|
| **Docker Desktop** (ou Docker Engine) | banco de dados e fila | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (use o backend WSL 2) | [Docker Desktop](https://www.docker.com/products/docker-desktop/) | `sudo apt install docker.io docker-compose-v2` ou Docker Desktop com integração WSL |
| **uv** | Python e dependências do backend | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | igual ao macOS |
| **Node.js 20+** e **pnpm** | dashboard | [nodejs.org](https://nodejs.org) (LTS), depois `corepack enable` | `brew install node`, depois `corepack enable` | [nvm](https://github.com/nvm-sh/nvm) ou pacote da distro, depois `corepack enable` |
| **tectonic** | gerar os PDFs dos currículos | `scoop install tectonic` (ou o .zip das [releases](https://github.com/tectonic-typesetting/tectonic/releases) no PATH) | `brew install tectonic` | `curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net \| sh && mkdir -p ~/.local/bin && mv tectonic ~/.local/bin/` |
| **Git** | baixar o projeto | [git-scm.com](https://git-scm.com) | já vem (ou `brew install git`) | `sudo apt install git` |

O Python certo (3.12) é baixado pelo `uv` sozinho. Não precisa instalar Python à parte.

**WSL:** instale tudo **dentro** do WSL (é lá que a API roda). O navegador fica no Windows e acessa `http://localhost:3100` normalmente.

---

## 2. Baixar e configurar o projeto

```bash
git clone https://github.com/<seu-usuario>/application-tracker.git
cd application-tracker/backend
uv sync                                   # instala o backend
uv run tracker-init --email=voce@gmail.com   # cria o .env com segredos aleatórios
```

O `tracker-init` cria o arquivo `.env` na raiz do projeto. O e-mail é só o que pode entrar no dashboard. Abra o `.env` num editor quando um guia abaixo pedir para colar alguma chave.

> Depois de mudar o `.env`, sempre reinicie o app (Ctrl+C no `tracker-dev` e rode de novo).

---

## 3. Subir o app

Três terminais (no Windows: PowerShell; no WSL/macOS/Linux: terminal comum), todos a partir da pasta do projeto:

```bash
# 1) banco e fila (uma vez por boot do computador)
docker compose -f infra/docker-compose.yml up -d

# 2) backend: prepara o banco (só na primeira vez) e sobe API + worker + agendador
cd backend
uv run alembic upgrade head
uv run tracker-seed
uv run tracker-dev

# 3) dashboard
cd apps/web
pnpm install
pnpm build && pnpm start      # ou "pnpm dev" se for mexer no código
```

Abra **http://localhost:3100**, clique em **Entrar** e siga o onboarding:

1. **IA**: confirme que está verde (seção 4).
2. **Currículo**: envie o PDF do seu LinkedIn (perfil → **Recursos/Mais** → **Salvar como PDF**) ou o seu CV (PDF/DOCX). A IA extrai tudo; você confere e salva.
3. **O que você busca**: cargos-alvo, senioridade, modelo de trabalho e, se quiser, pretensão salarial.
4. **Emprego atual** (opcional): base para comparar ofertas.
5. **Integrações** (opcionais): Gmail/Agenda, Telegram, extensão.
6. **Concluir**: o app passa a monitorar 100 empresas com site de vagas público e cria uma busca com os seus cargos (roda às 9h e 15h nos dias úteis).

| Porta | O quê |
|---|---|
| 3100 | dashboard |
| 8000 | API (docs em http://localhost:8000/api/docs) |
| 5433 | PostgreSQL (5433 para não brigar com outro Postgres na 5432) |
| 6379 | Redis |

---

## 4. IA (Claude)

O app usa IA para ler vagas, montar currículos e classificar e-mails. **Escolha um** dos caminhos.

### Opção A: Ollama local (Qwen, sem custo por chamada)

Com o Ollama acessível em `http://localhost:11434`, baixe o modelo:

```bash
ollama pull qwen2.5-coder:3b
```

Se estiver usando o container Podman `ollama` no Fedora WSL:

```powershell
wsl -d FedoraLinux-43 -- podman start ollama
wsl -d FedoraLinux-43 -- podman exec ollama ollama pull qwen2.5-coder:3b
```

No `.env`, selecione o backend local:

```dotenv
AI_BACKEND=ollama
AI_MODEL_DEFAULT=qwen2.5-coder:3b
AI_MODEL_JOB_EXTRACT=qwen2.5-coder:3b
OLLAMA_BASE_URL=http://localhost:11434
```

Reinicie o `tracker-dev`. O teste da IA no onboarding confirma se o Ollama e o modelo estão acessíveis. PDFs com texto selecionável são convertidos localmente em texto; PDFs digitalizados e imagens não têm OCR. Tarefas que exigem busca na web precisam de um backend compatível com as ferramentas de pesquisa.

### Tectonic instalado no WSL2

Se a API/worker roda no Windows e o Tectonic está instalado apenas no WSL2, o app tenta localizá-lo automaticamente na distro WSL padrão. Confirme no terminal:

```powershell
wsl --exec zsh -ic 'tectonic --version'
```

O comando deve retornar a versão do Tectonic. Instale-o na distro padrão (ou selecione como padrão a distro onde já está instalado) e reinicie o `tracker-dev` para o worker detectar a integração.

### Opção B: Claude Code com a sua assinatura

Sem custo além da assinatura Claude Pro/Max. Nada a colocar no `.env`.

1. Instale o Claude Code **na máquina onde a API roda** (no WSL, dentro do WSL):
   - Windows (PowerShell): `irm https://claude.ai/install.ps1 | iex`
   - macOS / Linux / WSL: `curl -fsSL https://claude.ai/install.sh | bash`
2. Rode `claude` uma vez, faça login no navegador e saia com `/exit`.
3. Mantenha atualizado: `claude update`. Versões antigas recusam os modelos novos com o erro *"does not support this model; version 2.1.280 or newer is required"*.

Cada uso conta no limite da sua assinatura. Para poupar, a leitura em massa de vagas usa o Sonnet (`AI_MODEL_JOB_EXTRACT` no `.env`).

### Opção C: chave da API da Anthropic (pago por uso)

1. Em [console.anthropic.com](https://console.anthropic.com), vá em **Billing** e coloque créditos (US$ 5–10 bastam para começar). Defina um limite mensal ali também.
2. **API Keys → Create Key**. A chave começa com `sk-ant-` e só aparece uma vez.
3. No `.env`: `ANTHROPIC_API_KEY=sk-ant-...`

A assinatura do claude.ai **não** cobre a API: são cobranças separadas. `AI_MONTHLY_BUDGET_USD` é o teto do próprio app.

No onboarding, o botão **Testar a IA agora** confirma que está funcionando.

---

<a id="google"></a>
## 5. Google: Gmail e Agenda

Opcional. Faz o app ler as respostas das empresas, separar os e-mails em marcadores `Vagas/*`, sugerir follow-ups e marcar entrevistas na Agenda. **Ele nunca envia e-mails**: só cria rascunhos.

Uma única credencial serve para Gmail **e** Agenda.

1. **Crie um projeto** em [console.cloud.google.com](https://console.cloud.google.com/projectcreate) (qualquer nome, sem cartão).
2. **Ative as APIs**: em **APIs e serviços → Biblioteca**, ative a **Gmail API** e a **Google Calendar API**.
3. **Tela de consentimento** (no menu novo: **Google Auth Platform**): tipo **Externo**, nome do app e seu e-mail. Se perguntar "Quais dados você vai acessar?", escolha **Dados do usuário**. A tela de escopos pode ser pulada.
4. **Usuários de teste**: em **Público-alvo → Usuários de teste → Add users**, adicione o e-mail da conta que vai conectar. Sem isso aparece **"Erro 403: access_denied"**.
5. **Credencial**: **Credenciais → Criar credenciais → ID do cliente OAuth**.
   - Tipo de aplicativo: **Aplicativo da Web**. Não escolha "Extensão do Chrome" e não crie uma "Chave de API": nenhuma das duas acessa o Gmail.
   - **Origens JavaScript autorizadas**: deixe vazio.
   - **URIs de redirecionamento autorizados**: adicione as duas, exatamente assim:
     ```
     http://localhost:8000/api/v1/auth/google/callback
     http://localhost:8000/api/v1/integrations/google/callback
     ```
6. **Copie o ID e a chave secreta** para o `.env` e reinicie o app:
   ```
   GOOGLE_CLIENT_ID=....apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
7. No dashboard: **Configurações → Integrações → Gmail e Agenda → Conectar Google**.
   - Vai aparecer **"O Google não verificou este app"**. É normal para um app pessoal: **Avançado → Acessar (não seguro)**.
   - Marque **todas** as permissões e continue.

Em modo de teste, o Google pede para reconectar a cada ~7 dias. Para não expirar, em **Público-alvo** clique em **Publicar app**. Ele continua sendo só seu, e a tela de "não verificado" segue aparecendo no login.

Sem URL pública o Gmail é lido a cada 5 minutos (os campos `GOOGLE_PUBSUB_*` ficam vazios).

---

<a id="telegram"></a>
## 6. Telegram

Opcional. Avisa de entrevistas, respostas e perguntas pendentes, e aceita comandos: `/hoje`, `/status`, `/fila`, `/pendentes`, `/stop`, `/retomar`. Funciona com o app rodando só no seu PC (o app busca as mensagens por *polling*; não precisa de URL pública).

1. No Telegram, abra o **[@BotFather](https://t.me/BotFather)** e mande `/newbot`. Escolha um nome e um usuário terminado em `bot`. Ele responde com o **token** (`123456:ABC...`).
2. No `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...` e reinicie o app.
3. **Vincule o seu chat**: em **Configurações → Integrações → Telegram**, clique em **Gerar código** e mande `/start CÓDIGO` para o seu bot.
   - Alternativa: mande qualquer mensagem ao bot, abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador, copie o número em `"chat":{"id":...}` e coloque em `TELEGRAM_ALLOWED_CHAT_ID=` no `.env`.
4. Clique em **Enviar mensagem de teste**.

---

<a id="extensao"></a>
## 7. Extensão do navegador

Opcional e muito útil. Enquanto você navega numa vaga (LinkedIn, Gupy, Greenhouse, Lever…), a extensão:
- mostra as notas, a reputação da empresa e se ela costuma chamar para entrevista;
- preenche o formulário com o seu perfil e anexa o CV (perguntas sensíveis, como salário e visto, ficam para você);
- sugere uma resposta 💡 ao clicar num campo vazio;
- registra a candidatura quando **você** clica em enviar.

Ela nunca envia nada sozinha.

1. **Gere o token**: **Configurações → Integrações → Extensão do navegador → Gerar token**. Ele aparece uma vez só.
2. **Carregue a extensão** no Chrome ou Edge:
   - **Windows, macOS, Linux**: abra `chrome://extensions` (Edge: `edge://extensions`), ative o **Modo do desenvolvedor**, clique em **Carregar sem compactação** e escolha a pasta `apps/extension`.
   - **WSL**: o Chrome do Windows não carrega bem pastas de dentro do WSL. Copie para uma pasta do Windows (troque `SEU_USUARIO`):
     ```bash
     mkdir -p /mnt/c/Users/SEU_USUARIO/tracker-extension
     cp -r apps/extension/. /mnt/c/Users/SEU_USUARIO/tracker-extension/
     ```
     e carregue `C:\Users\SEU_USUARIO\tracker-extension`. Quando a extensão for atualizada, repita o `cp` e clique em **↻ Recarregar** no card dela.
3. Clique no ícone 🧩 e **fixe** o Application Tracker.
4. A página de **Opções** abre sozinha na primeira vez (ou botão direito no ícone → **Opções**): cole o token, confira o endereço da API (`http://localhost:8000`) e clique em **Testar conexão**. O token fica só neste navegador e nunca é mostrado às páginas que você visita.

**Ligar, desligar e ajustar**

| Onde | O que dá para fazer |
|---|---|
| Ícone da extensão (popup) | Ligar/desligar tudo · desligar só no site atual · **Pausar 1h** · ligar/desligar "Preencher sozinho" e "Anexar meu CV" · mostrar o painel, preencher de novo ou completar com IA · contadores do dia |
| Painel na página | ⏸ desliga neste site · ⚙ opções · — minimiza · ✕ fecha nesta página · ↻ preencher de novo · ↶ desfazer o que o assistente preencheu · "Revisar preenchidos" mostra a origem de cada resposta (perfil, banco, IA, você) |
| Opções | Tudo acima, mais: quando mostrar o painel (vagas e formulários, só formulários, só quando eu pedir), lado da tela, começar minimizado, contornos coloridos, sugestão 💡 ao clicar, avaliar vagas novas sozinho, registrar ao enviar, lista de sites ignorados |
| Teclado | `Alt+Shift+P` painel · `Alt+Shift+F` preencher · `Alt+Shift+I` IA (troque em `chrome://extensions/shortcuts`) |

**O que ela nunca faz:** clicar em enviar; ler ou preencher senhas, cartões, códigos de verificação, CPF/RG e campos de busca; mandar perguntas sensíveis para a IA; aprender com o que ela mesma preencheu (só com o que **você** digitou); agir em páginas que não são de vaga ou candidatura.

API fora do seu PC (ex.: numa VPS)? Ao salvar outro endereço nas Opções, o navegador pede permissão para a extensão falar com ele.

Para buscar vagas por palavras-chave, abra **Vagas → Descobrir**, digite o cargo/tecnologia, escolha país, modalidade e fontes públicas, e clique em **Buscar vagas agora**. Você também pode adicionar seus próprios boards públicos Greenhouse, Lever, Ashby e Gupy pelo link do quadro de vagas; páginas genéricas ainda não são suportadas. A busca fica salva para execução automática e os resultados são exibidos em **Vagas para revisar**: selecione as vagas que deseja e clique em **Enviar selecionadas para a Fila**. Nenhuma vaga coletada entra na Fila sem essa seleção. A descoberta não automatiza nem coleta resultados do LinkedIn. Para editar uma busca salva, selecione-a no campo **Editar uma busca salva**, altere os filtros/fontes e execute novamente. A aba **Empresas (ATS)** continua disponível para cadastrar fontes de empresas específicas.

---

## 8. Vagas do LinkedIn

Sem robô e sem coleta automatizada dos resultados de busca. As vagas chegam por alertas no Gmail; você também pode analisar manualmente uma vaga individual.

**Automático (recomendado): alertas de vaga pelo Gmail**
1. No LinkedIn, em **Vagas**, faça a busca que quer acompanhar (cargo, local, remoto, Easy Apply…).
2. Ligue **"Ativar alerta"** no topo dos resultados. Em **Gerenciar alertas**, frequência **diária** e notificação por **e-mail**.
3. Com o Gmail conectado (seção 5), cada e-mail de alerta vira vagas na aba **Vagas**, com nota, e o e-mail ganha o marcador `Vagas/LinkedIn`.

**Na hora: analisar uma vaga específica**
1. Com a extensão instalada, abra manualmente a página de uma vaga individual no LinkedIn.
2. No painel do assistente, escolha **"Avaliar e salvar vaga"**. A extensão analisa a vaga aberta, não os resultados de busca.

Para se candidatar (inclusive no Easy Apply), a extensão preenche o formulário; quem clica em enviar é você.

---

## 9. Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| **"Erro 403: access_denied · app em teste"** ao conectar o Google | Seu e-mail não está em **Público-alvo → Usuários de teste**. Adicione e tente de novo. |
| **"redirect_uri_mismatch"** | As URIs cadastradas no Google não estão idênticas às da seção 5 (http, porta 8000, sem barra no fim). |
| Botão **Conectar Google** desativado | `GOOGLE_CLIENT_ID`/`SECRET` vazios no `.env`, ou a API não foi reiniciada depois de preencher. |
| IA: *"does not support this model; version 2.1.280 or newer is required"* | Rode `claude update`. |
| IA: *"Claude Code não encontrado"* | Instale o Claude Code na mesma máquina da API (no WSL, dentro do WSL) e faça login com `claude`. |
| Mudei o `.env` e nada mudou | Reinicie: Ctrl+C no `tracker-dev` e `uv run tracker-dev` de novo. |
| **WSL travou/caiu** durante uma busca grande | Falta de memória. O worker já usa uma cópia só do modelo de embeddings (~2 GB). Se ainda faltar, aumente a RAM do WSL em `C:\Users\SEU_USUARIO\.wslconfig` (`[wsl2]` `memory=10GB`) e rode `wsl --shutdown`. Também ajuda reduzir `WORKER_CONCURRENCY=2` no `.env`. |
| "localhost recusou a conexão" ao abrir um PDF | O app não estava rodando (API na 8000, dashboard na 3100). Confira os terminais do passo 3. |
| Porta 5432/5433 ocupada | O compose usa a 5433. Se ela também estiver em uso, mude o mapeamento em `infra/docker-compose.yml` e a `DATABASE_URL` no `.env`. |
| PDFs dos currículos não geram | Instale o `tectonic` (seção 1) e reinicie o worker. |
| Vagas do LinkedIn não aparecem | Confira se o Gmail está conectado e se os alertas oficiais de vagas do LinkedIn chegam por e-mail. A descoberta não coleta resultados do site. |
| Extensão não aparece na página da vaga | Confira o token e o endereço da API nas Opções, se a API está rodando, e recarregue a página. Em WSL, confirme que copiou a versão mais nova (seção 7). |
| Telegram não responde aos comandos | Só uma instância da API pode estar rodando; confira `TELEGRAM_BOT_TOKEN` e reinicie. |
