# Application Tracker

Assistente de busca de emprego com IA, para rodar no seu próprio computador. Encontra vagas, avalia quais valem a pena, prepara o currículo certo para cada uma, ajuda a preencher candidaturas sem enviar nada por você, acompanha as respostas no Gmail, marca entrevistas na Agenda e prepara um dossiê para cada entrevista.

Funciona para **qualquer candidato**: no primeiro acesso, o onboarding pede o seu currículo (o PDF do LinkedIn serve), os cargos que você busca e, se quiser, a pretensão salarial. Roda em **Windows, macOS, Linux e WSL**.

## Começando

```bash
git clone https://github.com/<seu-usuario>/application-tracker.git && cd application-tracker
docker compose -f infra/docker-compose.yml up -d     # banco e fila
cd backend
uv sync
uv run tracker-init --email=voce@gmail.com           # cria o .env com segredos aleatórios
uv run alembic upgrade head && uv run tracker-seed
uv run tracker-dev                                   # API + worker + agendador
# em outro terminal:
cd apps/web && pnpm install && pnpm build && pnpm start
```

Abra **http://localhost:3100**, clique em **Entrar** e siga o onboarding. O **[guia de configuração](docs/guia-configuracao.md)** explica cada passo em detalhe: o que instalar em cada sistema, como ligar a IA (com a sua assinatura do Claude Code, sem chave de API), como criar a credencial do Google sem cair nos erros comuns, como vincular o Telegram e como instalar a extensão do navegador. Os mesmos guias aparecem dentro do app.

## O que o sistema faz

| Etapa | O que acontece |
|---|---|
| **Onboarding** | Lê o seu CV ou o PDF do LinkedIn com IA, monta o **currículo mestre** e pergunta o que você procura. Nada pessoal fica no código |
| **Descoberta** | Busca vagas em 100 empresas com site de vagas público (48 no Brasil, 52 fora), em 7 sites de vagas remotas e no LinkedIn (alertas no Gmail e importação pela extensão). Só entram vagas dos seus cargos que dá para fazer do Brasil |
| **Avaliação** | Filtros eliminatórios e duas notas: **Chance** (probabilidade de resposta) e **Atratividade** (reputação, salário, cultura), cruzadas numa **prioridade** |
| **Vagas** | Lista com filtros por tecnologia, empresa, salário, modelo de trabalho, senioridade, regime, local, data e notas |
| **Currículos** | Coringas por perfil (dados, IA, backend, frontend, full stack, mobile, DevOps) e CV específico por vaga, em LaTeX, sempre validados contra o mestre (nada inventado) |
| **Candidatura** | Extensão do navegador que preenche o formulário sozinha (qualquer site de vaga, inclusive o Easy Apply do LinkedIn), anexa o CV certo, completa perguntas abertas com IA e registra o envio. Liga/desliga geral ou por site, pausa de 1 hora, desfazer, revisão da origem de cada resposta e atalhos de teclado. Quem envia é você |
| **Acompanhamento** | Lê o Gmail, classifica as respostas, separa em marcadores `Vagas/*`, atualiza o kanban e sugere follow-ups (só rascunhos) |
| **Entrevista** | Dossiê com história, valores e notícias da empresa (com fontes), seus pontos de match, perguntas prováveis e simulação |
| **Oferta** | Comparador CLT x PJ contra o seu emprego atual, referência salarial e roteiro de negociação |
| **Notificações** | Telegram com comandos (`/hoje`, `/pendentes`, `/stop`), funcionando localmente |

## Princípios

1. **Você no controle.** Nada é enviado em seu nome sem a política que você definiu. E-mails e mensagens nunca: só rascunhos.
2. **Nunca inventar.** Currículos e respostas só usam fatos do seu currículo mestre.
3. **Proteger sua conta.** Ritmo humano, limites diários e parada imediata em captcha ou aviso. Nunca contornar proteções.
4. **Tudo explicável.** Cada nota e decisão mostra o motivo e a fonte.
5. **Seus dados são seus.** Rodam no seu computador, criptografados, exportáveis. Nada pessoal vai para o git (`data/` e `.env` estão no `.gitignore`).

## Stack

- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind v4 + Motion
- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2 + Pydantic v2 (gerenciado com `uv`)
- **Workers:** Celery (threads) + Redis, com agendador
- **Banco:** PostgreSQL 16 + pgvector
- **IA:** Claude, via Claude Code local (sua assinatura) ou API da Anthropic; embeddings locais multilíngues
- **Integrações:** Gmail, Google Calendar, Telegram, extensão Chrome/Edge (MV3)
- **PDF:** LaTeX (Jinja2 + tectonic), estilo `classic` em `templates/resumes/classic/`

## Desenvolvimento

```bash
cd backend && uv run pytest              # banco de teste tracker_test (criado pelo compose)
uv run ruff check . && uv run pyright
cd ../apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

Produção/VPS: `docker compose -f infra/docker-compose.prod.yml --env-file .env up -d --build`. CI em `.github/workflows/ci.yml`.

## Documentação

| Documento | Conteúdo |
|---|---|
| **[Guia de configuração](docs/guia-configuracao.md)** | **Instalação no Windows/macOS/Linux/WSL, IA, Google, Telegram, extensão e problemas comuns** |
| [CLAUDE.md](CLAUDE.md) | Instruções para a IA que mexer no código |
| [01 · Visão do produto](docs/01-visao-produto.md) | Problema, objetivos, escopo, jornada, métricas de sucesso |
| [02 · Requisitos funcionais](docs/02-requisitos-funcionais.md) | Todos os requisitos numerados (RF-xxx) com critérios de aceite |
| [03 · Requisitos não funcionais](docs/03-requisitos-nao-funcionais.md) | Desempenho, confiabilidade, segurança, custo, usabilidade (RNF-xxx) |
| [04 · Arquitetura](docs/04-arquitetura.md) | Componentes, deploy híbrido, estrutura do repositório, fluxos |
| [05 · Modelo de dados](docs/05-modelo-de-dados.md) | Tabelas, campos, enums, máquina de estados |
| [06 · API](docs/06-api.md) | Endpoints REST e eventos WebSocket |
| [07 · Integrações](docs/07-integracoes.md) | LinkedIn, Gmail, Calendar, ATS, Telegram |
| [08 · IA com Claude](docs/08-ia-claude.md) | Tarefas de IA, modelos, schemas, guardrails, custo, avaliação |
| [09 · Assistente de candidatura](docs/09-motor-candidatura.md) | A extensão do navegador em detalhe |
| [10 · Motor de avaliação](docs/10-motor-avaliacao.md) | Filtros, notas de Chance e Atratividade, calibração |
| [11 · Currículos](docs/11-curriculos.md) | Currículo mestre, coringas, específicos, templates, checagem ATS |
| [12 · Segurança e privacidade](docs/12-seguranca-privacidade.md) | Segredos, OAuth, criptografia, LGPD, termos de uso |
| [13 · Testes e qualidade](docs/13-testes-qualidade.md) | Estratégia de testes, fixtures, evals de IA, CI |
| [14 · Roadmap](docs/14-roadmap.md) | Fases, tarefas e definição de pronto |
| [15 · Decisões (ADRs)](docs/15-decisoes.md) | Decisões de arquitetura e o porquê |
| [16 · Riscos](docs/16-riscos.md) | Riscos e mitigações |
| [17 · Questões em aberto](docs/17-questoes-abertas.md) | Decisões pendentes e o padrão assumido |
| [18 · Glossário](docs/18-glossario.md) | Termos usados no projeto |

## Sem robô

O app não automatiza cliques em nenhum site nem coleta resultados de busca do LinkedIn: as vagas chegam pelos alertas no seu Gmail, e você também pode analisar manualmente uma vaga individual aberta no navegador. A candidatura é feita por você, com a extensão preenchendo o formulário. Veja [docs/15-decisoes.md](docs/15-decisoes.md) (ADR-020).
