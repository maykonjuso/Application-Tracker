# 06 · API

> **Nota (F10, ADR-020):** o robô local (`tracker-agent`, Playwright) foi removido. Trechos que citam robô, agente local, Easy Apply automático ou kill switch são históricos. A candidatura é feita pelo usuário com o assistente do navegador ([09](09-motor-candidatura.md)); vagas do LinkedIn chegam por alertas no Gmail e pela importação na extensão.

REST JSON em `/api/v1`. Autenticação por cookie de sessão HTTP-only (dashboard) ou token de dispositivo (agente). OpenAPI gerado pelo FastAPI é a fonte dos tipos do frontend.

Convenções:
- Paginação por cursor: `?cursor=...&limit=50` → `{items, next_cursor}`
- Erros no formato `{"error": {"code": "...", "message": "...", "details": {...}}}`
- Mutações que disparam trabalho assíncrono retornam `202` com `{task_id}`; o progresso chega pelo WebSocket
- Header `Idempotency-Key` aceito em todos os POST que criam recursos

## Autenticação
| Método | Rota | Descrição |
|---|---|---|
| GET | `/auth/google/login` | Inicia OAuth (login) |
| GET | `/auth/google/callback` | Callback; cria sessão se o e-mail = `AUTH_ALLOWED_EMAIL` |
| POST | `/auth/logout` | |
| GET | `/me` | Sessão atual |

## Perfil
| Método | Rota | Descrição |
|---|---|---|
| GET | `/profile/master` | Versão atual |
| GET | `/profile/master/versions` · `/profile/master/versions/{v}` · `/diff?from=&to=` | Histórico e diff |
| PUT | `/profile/master` | Nova versão |
| POST | `/profile/import` | Upload de PDF/DOCX → extração (202) |
| POST | `/profile/import/{id}/confirm` | Aplica a extração revisada |
| GET/POST/PUT/DELETE | `/profile/star-stories[/{id}]` | |
| GET/PUT | `/profile/preferences` | |
| GET/POST/PUT/DELETE | `/answer-bank[/{id}]` | `?q=` busca semântica |
| GET/POST/DELETE | `/blocklist[/{id}]` | |
| GET/POST/DELETE | `/documents[/{id}]` | |

## Currículos
| Método | Rota | Descrição |
|---|---|---|
| GET | `/resumes/templates` | |
| GET | `/resumes/variants?kind=` | |
| POST | `/resumes/variants/wildcards/generate` | Gera/regenera coringas (202) |
| POST | `/resumes/variants/tailored` | `{job_id}` → CV específico (202) |
| GET/PUT | `/resumes/variants/{id}` | |
| POST | `/resumes/variants/{id}/approve` | |
| POST | `/resumes/variants/{id}/render` | Gera PDF (202) |
| GET | `/resumes/renders/{id}/pdf` | Download |
| GET | `/resumes/variants/{id}/validation` | Validador de fatos + ATS |

## Vagas e empresas
| Método | Rota | Descrição |
|---|---|---|
| GET | `/jobs?quadrant=&status=&q=&sort=priority` | |
| GET | `/jobs/{id}` | Inclui score, fontes, snapshots e checkup de completude/consistência; não atesta legitimidade |
| POST | `/jobs/import` | `{url}` ou `{text}` (202) |
| POST | `/jobs/{id}/feedback` | `{verdict, reason}` |
| POST | `/jobs/{id}/rescore` | |
| POST | `/jobs/{id}/approve` | Cria candidatura na fila |
| POST | `/jobs/{id}/skip` | |
| GET/POST/PUT/DELETE | `/saved-searches[/{id}]` · POST `/saved-searches/{id}/run` | |
| GET | `/companies?watchlisted=` · `/companies/{id}` | Perfil, sinais, candidaturas |
| POST | `/companies/{id}/refresh-profile` | (202) |
| PUT | `/companies/{id}` | Watchlist, bloqueio, token do ATS |

## Avaliação
| Método | Rota | Descrição |
|---|---|---|
| GET/PUT | `/scoring/weights` | Pesos atuais / nova versão |
| POST | `/scoring/calibrate` | Propõe pesos calibrados (202) |
| POST | `/scoring/weights/{version}/activate` | |

## Candidaturas
| Método | Rota | Descrição |
|---|---|---|
| GET | `/applications?status=&q=` | |
| GET | `/applications/{id}` | Com linha do tempo, respostas, CV, e-mails, eventos |
| PATCH | `/applications/{id}` | Status (validado pela máquina de estados), notas, next_action |
| POST | `/applications/manual` | Registrar candidatura feita fora do sistema ("enviei") |
| GET | `/review-queue` | Candidaturas em `awaiting_review` |
| POST | `/review-queue/approve` | `{ids: []}` aprovação em lote |
| GET | `/pending-questions` | |
| POST | `/pending-questions/{id}/answer` | `{value, save_to_bank, scope}` |
| GET | `/applications/{id}/package` | Pacote para candidatura externa |

## Comunicação
| Método | Rota | Descrição |
|---|---|---|
| GET | `/inbox?classification=&link_status=` | E-mails e mensagens classificados |
| POST | `/inbox/{id}/link` | `{application_id}` resolve ambiguidade |
| POST | `/inbox/{id}/reclassify` | Correção manual (vira exemplo para eval) |
| GET | `/drafts` · POST `/drafts/{id}/push-to-gmail` · POST `/drafts/{id}/discard` | |

## Agenda e entrevistas
| Método | Rota | Descrição |
|---|---|---|
| GET | `/calendar/events?from=&to=` | |
| POST | `/calendar/events/{id}/confirm` | Confirma rascunho → cria no Google |
| GET | `/calendar/availability?from=&to=&duration=` | Horários livres |
| GET/POST | `/interviews` · `/interviews/{id}` | |
| POST | `/interviews/{id}/dossier` | Gerar/regenerar (202) |
| GET | `/interviews/{id}/dossier` · `/cheatsheet` | |
| POST | `/interviews/{id}/post-notes` | |
| POST | `/mock-sessions` · POST `/mock-sessions/{id}/turn` | Simulação (streaming SSE) |
| GET/POST/PUT | `/assessments[/{id}]` | |

## Ofertas, pessoas, insights
| Método | Rota | Descrição |
|---|---|---|
| GET/POST/PUT | `/offers[/{id}]` · POST `/offers/compare` | |
| GET/POST/PUT | `/people[/{id}]` | |
| GET | `/insights/funnel?from=&to=` · `/insights/resume-performance` · `/insights/skill-gaps` · `/insights/weekly-report` | |

## Plataforma
| Método | Rota | Descrição |
|---|---|---|
| GET/PUT | `/settings/{key}` | |
| GET | `/integrations` | Saúde das integrações |
| GET | `/integrations/google/connect` · callback | OAuth Gmail + Calendar (incremental) |
| POST | `/integrations/telegram/link` | Gera código de vínculo |
| GET | `/ai/usage?from=&to=` | Custo por tarefa |
| GET | `/audit?entity=&from=` | |
| POST | `/agent/kill` | Kill switch |
| POST | `/export` · DELETE `/data` | Exportação / exclusão total |
| GET | `/health` | |

## Extensão do navegador
| Método | Rota | Descrição |
|---|---|---|
| POST | `/assistant/token` | Gera o token de instalação da extensão (sessão do dashboard) |

A extensão não automatiza nem coleta resultados de busca do LinkedIn. Descoberta de vagas usa boards públicos, fontes ATS configuradas pelo usuário e empresas monitoradas.

## Webhooks
| Rota | Origem |
|---|---|
| POST `/webhooks/gmail` | Google Pub/Sub push (verifica o JWT do Pub/Sub) |
| POST `/webhooks/telegram/{secret}` | Telegram (verifica o secret token header) |

## WebSocket do dashboard: `WS /ws`
Mensagens servidor → cliente: `{channel, type, data}`
- `agent.status` · `agent.step` (etapa, screenshot_url) · `agent.stopped` (motivo)
- `task.progress` (`task_id`, pct, mensagem) · `task.done` · `task.failed`
- `application.updated` · `job.scored` · `pending_question.created` · `notification`

## Protocolo do agente local
Canal `WS /agent/ws` com header `Authorization: Device <token>`. O agente **só executa**; toda decisão é da API.

| Direção | Mensagem | Conteúdo |
|---|---|---|
| agente → API | `hello` | versão, sites suportados, estado do navegador |
| API → agente | `job.assign` | `agent_job` (kind, payload, checkpoint) |
| agente → API | `job.step` | etapa, screenshot (upload por URL assinada), log |
| agente → API | `fields.resolve` | campos extraídos da etapa (label, tipo, opções, obrigatório, valor atual) |
| API → agente | `fields.resolved` | valores por campo **ou** `pause` (pergunta pendente) |
| API → agente | `job.resume` | após resposta do usuário |
| agente → API | `review.ready` | tela final pronta para revisão |
| API → agente | `review.approved` | autoriza o envio |
| agente → API | `job.submitted` | evidência de sucesso (texto/screenshot) |
| agente → API | `safety.stop` | motivo (captcha, aviso, logout, layout desconhecido) + artefatos |
| API → agente | `kill` | parar imediatamente |
| agente → API | `heartbeat` | a cada 10 s |

Fallback HTTP equivalente (`/agent/jobs/next`, `/agent/jobs/{id}/...`) para ambientes sem WebSocket.
