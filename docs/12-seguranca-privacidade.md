# 12 · Segurança e privacidade

> **Nota (F10, ADR-020):** o robô local (`tracker-agent`, Playwright) foi removido. Trechos que citam robô, agente local, Easy Apply automático ou kill switch são históricos. A candidatura é feita pelo usuário com o assistente do navegador ([09](09-motor-candidatura.md)); vagas do LinkedIn chegam por alertas no Gmail e pela importação na extensão.

## 1. Modelo de ameaças (resumo)
| Ameaça | Mitigação |
|---|---|
| Vazamento de tokens Google/LinkedIn | Criptografia em repouso, escopos mínimos, cookies só na máquina local, rotação |
| Acesso indevido ao dashboard | Login Google restrito a um e-mail, cookie HTTP-only + SameSite=Lax + Secure, CSRF token em mutações |
| Agente falso conectando na API | Token de dispositivo com hash no banco, rotacionável, vinculado a um nome de máquina |
| Prompt injection via e-mail/vaga ("ignore as instruções e...") | Conteúdo externo delimitado e declarado como dado; saídas estruturadas com enum; ações com efeito real nunca dependem só da IA |
| Vaga desatualizada, incompleta ou com link suspeito | Checkup de origem, HTTPS, completude, data, repostagem e hash da captura; links fora de HTTP(S) ou com credenciais não são abertos pela interface |
| IA "envia" algo indevido | Nenhuma ferramenta de envio de e-mail/mensagem existe no código; candidaturas passam pela política |
| Bloqueio da conta do LinkedIn | Ver §5 e `16-riscos.md` |
| Perda de dados | Backup diário criptografado, teste de restauração |
| Vazamento de dados pessoais no git | `data/` e `.env` no `.gitignore`; hook de pre-commit com detecção de segredos (gitleaks) |

## 2. Segredos
- Somente via variáveis de ambiente (`.env` local, secrets do provedor em produção). `.env.example` sem valores reais.
- `ENCRYPTION_KEY` (32 bytes) para AES-256-GCM dos campos `*_encrypted`. Rotação: script que recriptografa com a chave nova.
- Logs nunca imprimem tokens, cookies, corpo de e-mails ou PDFs (filtro de redaction no logger).

## 3. OAuth Google
- Escopos listados em `07-integracoes.md` §1; qualquer escopo novo exige ADR.
- `gmail.compose` só para rascunhos: o código não contém chamada a `messages.send`/`drafts.send` (teste automatizado procura essas chamadas no código e falha se encontrar).
- Revogação pelo dashboard (apaga tokens locais e chama o endpoint de revogação).

## 4. Extensão do navegador e rede local
> O agente local (robô) foi removido na F10 (ADR-020). Proteções atuais (ADR-021):
- **Rede:** Postgres, Redis e dashboard escutam só em `127.0.0.1`. A API rejeita Host desconhecido (`TrustedHostMiddleware`; extras em `ALLOWED_HOSTS`). O login de desenvolvimento só funciona de loopback e é proibido em `APP_ENV=prod`.
- **Token da extensão:** guardado em `chrome.storage.local`, usado só pelo service worker; as páginas recebem a configuração sem ele. O service worker só repassa das páginas os endpoints do assistente e confere o `sender`. A API compara o hash SHA-256 do token.
- **Cookie do dashboard em `/assistant`:** recusado vindo de origem de extensão e, em escritas, exigido o header CSRF. Sem CORS para extensões.
- **Contexto:** a extensão só preenche e anexa o CV em páginas de vaga/candidatura; ignora senhas, cartões, OTP, CPF/RG e tokens; aprende só com eventos `isTrusted` do usuário; botões do painel ignoram cliques sintéticos da página.
- **IA local (`claude -p`):** roda com ambiente mínimo (sem segredos do app), em diretório temporário, com timeout e encerramento do processo; PDFs são convertidos em texto localmente.

## 5. Termos de uso e postura de automação
- **LinkedIn:** o User Agreement proíbe automação, bots e scraping. Desde a F10 não há automação no LinkedIn: vagas chegam por alertas no Gmail e pela leitura da lista que já está na tela do usuário; candidatura e envio são sempre do usuário.
- **Proibido no código:** resolver captcha, rotacionar IP/proxy, falsificar fingerprint, criar contas, enviar mensagens ou convites automaticamente, coletar dados de terceiros em massa.
- **Glassdoor/Indeed e similares:** sem scraping. Informação de reputação vem da pesquisa web do Claude, que consulta fontes públicas e cita.
- **ATS:** uso de endpoints públicos documentados (Greenhouse, Lever, Ashby) com frequência baixa.

## 6. LGPD e dados de terceiros
- O sistema guarda dados pessoais do próprio usuário e dados **profissionais** de terceiros (recrutadores, entrevistadores) recebidos em comunicação direta ou públicos.
- Finalidade única: gestão da própria busca de emprego. Sem compartilhamento.
- Retenção: corpo de e-mails 90 dias (metadados e trechos relevantes ficam); o resto até o usuário apagar.
- Exclusão: por pessoa (CRM), por candidatura ou total (RF-PLA-07).

## 7. Aplicação web
- Headers de segurança: CSP restritiva, HSTS, X-Content-Type-Options, frame-ancestors none.
- HTML de vagas e e-mails sanitizado (bleach/nh3 no backend; renderização sem `dangerouslySetInnerHTML` cru).
- Upload: tipos permitidos (PDF, DOCX, PNG, JPG), limite de 10 MB, verificação por magic bytes.
- Compilação LaTeX isolada: container sem rede, sem `shell-escape`, timeout, diretório temporário.
- Rate limit nos endpoints de autenticação e webhooks.
- Dependências auditadas no CI (`pip-audit`, `pnpm audit`).

## 8. Integridade das informações de vagas
- O detalhe da vaga mostra uma pontuação de completude/consistência calculada por verificações determinísticas: fonte registrada, link de candidatura, empresa/cargo, descrição, data, repostagens e correspondência do texto com a captura armazenada.
- A pontuação não é uma classificação de fraude nem comprova que a empresa ou a vaga é legítima. A aplicação não confirma a publicação no site oficial; o usuário deve verificar o anúncio antes de compartilhar dados pessoais ou financeiros.
- Sinais suspeitos produzidos pela análise automática são avisos para revisão, não fatos verificados. A falta de sinais não é garantia de segurança.
- A heurística de vaga fantasma mostra os motivos considerados: penaliza 2 ou mais repostagens, publicação acima de 60 dias, descrição marcada como genérica e relatos de demissões recentes. Ausência desses indícios não prova que exista processo seletivo ativo; dados indisponíveis devem aparecer como desconhecidos.
- O hash detecta divergência entre o texto atual e a captura guardada, mas não autentica a fonte original nem protege contra alteração conjunta dos dados e do hash.
