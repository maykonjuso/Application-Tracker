# 07 · Integrações

> **Nota (F10, ADR-020):** o robô local (`tracker-agent`, Playwright) foi removido. Trechos que citam robô, agente local, Easy Apply automático ou kill switch são históricos. A candidatura é feita pelo usuário com o assistente do navegador ([09](09-motor-candidatura.md)); vagas do LinkedIn chegam por alertas no Gmail e pela importação na extensão.

Toda integração implementa uma interface em `integrations/<nome>/` com uma implementação real e uma fake (testes). Falhas são registradas no painel de saúde (RF-PLA-05).

## 1. Google (Gmail + Calendar)

### Projeto no Google Cloud
1. Criar o projeto, ativar **Gmail API**, **Google Calendar API** e **Cloud Pub/Sub API**
2. Tela de consentimento OAuth: tipo **External**, usuário de teste = conta do Gmail
3. **Publicar o app ("In production")** sem verificação. Em modo *Testing*, o refresh token expira em **7 dias** e o Gmail desconecta toda semana. Em produção não verificado, aparece a tela "app não verificado" no consentimento, o que é aceitável para uso pessoal
4. Credencial OAuth "Web application" com redirect `API_BASE_URL/api/v1/integrations/google/callback`

### Escopos (mínimos)
| Escopo | Uso |
|---|---|
| `openid email profile` | Login |
| `https://www.googleapis.com/auth/gmail.readonly` | Ler e-mails |
| `https://www.googleapis.com/auth/gmail.compose` | **Criar rascunhos** (nunca enviar: o código não chama `messages.send`) |
| `https://www.googleapis.com/auth/calendar.events` | Criar e editar eventos |
| `https://www.googleapis.com/auth/calendar.freebusy` | Consultar disponibilidade |

Autorização incremental: login primeiro; Gmail e Calendar quando o usuário conectar na tela de integrações.

### Gmail: ingestão
- **Push:** `users.watch` com o tópico Pub/Sub (`GOOGLE_PUBSUB_TOPIC`), dando ao `gmail-api-push@system.gserviceaccount.com` o papel de publisher. A inscrição push aponta para `/webhooks/gmail`. O `watch` expira em 7 dias e é **renovado a cada 6 dias** pelo Beat.
- **Processamento:** `users.history.list(startHistoryId=último)` → novos `messageId` → `messages.get(format=full)`.
- **Fallback:** polling a cada 5 min (modo 100% local ou falha no push). Se o `historyId` for inválido (404), ressincroniza os últimos 7 dias via `messages.list(q="newer_than:7d")`.
- **Filtro inicial:** ignorar `CATEGORY_PROMOTIONS` e `CATEGORY_SOCIAL`, exceto remetentes de ATS e do LinkedIn (`jobs-noreply@linkedin.com`, `messages-noreply@linkedin.com`...).
- **Remetentes de ATS conhecidos** (pré-filtro): `greenhouse.io`, `lever.co`, `ashbyhq.com`, `gupy.io`, `myworkday.com`, `smartrecruiters.com`, `inhire.com.br`, `solides.com.br`, `kenoby.com`, `recrutei.com.br`, `breezy.hr`, `workable.com`, `teamtailor.com`. A lista fica em configuração.

### Calendar
- `freebusy.query` nas janelas configuradas (padrão: dias úteis, 09h–18h, fuso do usuário), com folga de 30 min antes e depois de compromissos
- Eventos criados no calendário primário com `extendedProperties.private.tracker_application_id` para reconciliar
- Descrição do evento: empresa, cargo, etapa, link da call, link para o dossiê no dashboard
- Lembretes: popup 1 dia antes e 1 h antes

## 2. LinkedIn (agente local)

**Não existe API oficial para busca de vagas ou candidatura.** A extensão local do navegador executa buscas agendadas em abas não ativas, usando a sessão do LinkedIn já autenticada pelo usuário. Ela lê apenas os cartões visíveis na página de resultados e importa as vagas no tracker; não envia candidaturas, não contorna login, CAPTCHA ou bloqueios, e não tenta ocultar a automação. O navegador precisa permanecer aberto e conectado.

| Função | Como |
|---|---|
| Login | Manual pelo usuário no navegador com a extensão instalada; a extensão reutiliza a sessão normal do LinkedIn |
| Busca de vagas | Agenda configurável em **Vagas → Buscar vagas**; a extensão verifica buscas devidas e abre uma aba em segundo plano. País e modalidade remota são filtros separados |
| Detalhe da vaga | Descrição, nº de candidatos, data, recrutador ("Conheça a equipe de contratação"), tipo de candidatura |
| Candidaturas | A descoberta não envia candidaturas; vagas encontradas aparecem como sugestões em Vagas para revisão manual |
| Mensagens | Somente leitura das conversas novas, disparada por notificação no Gmail |
| Perfil do usuário | Leitura para a otimização (RF-ANA-06) |

Verificações da extensão ocorrem a cada 30 segundos, com no máximo uma busca ativa por vez, e a agenda da busca define quando uma consulta pode ser executada. Ao alterar o endereço da API, o token ou o estado ativo nas opções, a extensão consulta a API imediatamente. A aba de descoberta mostra se a extensão foi vista nos últimos 10 minutos e o resultado da última coleta. Só os cartões presentes no carregamento inicial são importados; a extensão não pagina resultados automaticamente. Login vencido, redirecionamento para outra página, aba fechada, timeout ou erro de importação são reportados ao dashboard e indicados no ícone da extensão. O usuário deve respeitar os termos do LinkedIn e pode desabilitar a fonte ou extensão a qualquer momento.

Limites de candidatura (não aplicados à descoberta):
| Item | Padrão |
|---|---|
| Candidaturas Easy Apply por dia | 20 |
| Páginas de vaga abertas por dia | 150 |
| Buscas por dia | 10 |
| Intervalo entre ações | 2–7 s (distribuição log-normal) |
| Intervalo entre candidaturas | 45–180 s |
| Pausa longa | 10–20 min a cada 5 candidaturas |
| Janela | dias úteis, 09h–19h |

Seletores em `agent/sites/linkedin/selectors.yaml`, versionados. Use preferencialmente seletores por **papel e texto acessível** (`get_by_role`, `get_by_label`), que mudam menos que classes CSS.

## 3. ATS com API pública (vagas)

Endpoints públicos de job boards, sem autenticação. Para cada empresa monitorada guardamos o `ats_board_token`.

| ATS | Endpoint de listagem | Observações |
|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Detalhe: `/jobs/{id}?questions=true` traz as perguntas do formulário |
| Lever | `GET https://api.lever.co/v0/postings/{company}?mode=json` | Campos `categories`, `workplaceType`, `salaryRange` |
| Ashby | `GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | Inclui compensação quando publicada |
| Gupy | Páginas públicas `https://{empresa}.gupy.io/` | **A validar na F6:** confirmar se há endpoint JSON público estável; caso contrário, parse do HTML público com ritmo baixo |

Descoberta do token: o usuário informa ao adicionar a empresa, ou o sistema detecta pelo link "candidatar" de uma vaga importada.

Frequência: 1x a cada 6 h por empresa, com `ETag`/`If-Modified-Since` quando suportado.

Em **Vagas → Buscar vagas**, o usuário também pode adicionar boards próprios colando a URL pública. A busca reconhece boards Greenhouse (`boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`) e Gupy (`*.gupy.io`), extrai o identificador da empresa e usa as APIs públicas acima. Páginas de carreira genéricas e URLs de ATS não suportados são rejeitadas; não fazemos scraping arbitrário de sites.

## 4. Claude API
Ver `08-ia-claude.md`. SDK oficial `anthropic` (Python), sempre via `integrations/claude/client.py`.

## 5. Embeddings
Modelo local multilíngue (`sentence-transformers/paraphrase-multilingual-mpnet-base-v2`, 768 dim) via `fastembed`, rodando no worker. Alternativa hospedada configurável (Voyage AI). Ver ADR-006.

## 6. Telegram
- Bot criado no @BotFather; webhook em `/webhooks/telegram/{secret}` com `secret_token`
- Vínculo: o dashboard gera um código; o usuário manda `/start <código>`; o `chat_id` é salvo e só ele é aceito
- Mensagens com `inline_keyboard` para ações (aprovar, responder opção, abrir no dashboard)
- Comandos: `/status`, `/hoje`, `/fila`, `/stop`, `/retomar`, `/pendentes`
- Mensagens nunca contêm tokens nem dados sensíveis além do necessário (empresa, cargo, horário)

## 7. Object storage
S3-compatível (Cloudflare R2, Backblaze B2 ou MinIO local). Buckets/prefixos: `resumes/`, `snapshots/`, `agent-artifacts/`, `documents/`, `exports/`. URLs assinadas com validade curta para download.

## 8. Pesquisa de empresa
Via ferramenta de pesquisa web do Claude (server-side), não por scraping direto de Glassdoor, Indeed ou similares (que proíbem scraping). Domínios preferidos e bloqueados são configuráveis.
