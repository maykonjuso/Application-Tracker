# 09 · Assistente de candidatura (extensão do navegador)

> Substitui o antigo robô local (Playwright), removido na F10 (ADR-020). Nada clica em "enviar" por você.

## 1. Ideia

Você se candidata na **sua sessão normal do navegador**, no site da vaga. A extensão (Chrome/Edge, MV3, `apps/extension`) faz o trabalho chato: avalia a vaga, preenche o formulário, escreve as perguntas abertas quando você pede e registra a candidatura quando você envia. Sem automação de cliques, sem risco de bloqueio por "bot".

## 2. Fluxo

1. **Página da vaga**: o painel mostra logo, Chance, Atratividade, reputação e se a empresa costuma responder. Vaga desconhecida → "Avaliar e salvar vaga" (uma chamada de IA, cache de 90 dias). Vaga de alerta do Gmail sem descrição pode ser completada com o texto da página e reavaliada.
2. **Formulário**: assim que aparece, o assistente **preenche sozinho** o que sabe com segurança (perfil → banco de respostas → regras), sem sobrescrever o que você digitou. Formulários em etapas: campos novos são preenchidos quando surgem.
3. **Perguntas abertas**: "Completar com IA" faz **uma** chamada para todas as pendentes e preenche (contorno azul, para revisar).
4. **Perguntas sensíveis** (salário, visto, dados pessoais protegidos): nunca vão para a IA; ficam para você (contorno laranja).
5. **Clique num campo vazio**: sugestão 💡 com "Usar" e "✨ Gerar com IA".
6. **Envio**: você clica em enviar no site. A extensão detecta a página de confirmação e registra a candidatura (canal `assisted`) com as respostas usadas, que também viram aprendizado no banco de respostas. Há também o botão "Já me candidatei".

## 3. Preenchimento robusto

| Tipo | Como |
|---|---|
| Texto, e-mail, telefone, número | simula digitação (`insertText`, o mesmo caminho de colar), aceito por React/Vue/Angular e campos com máscara; fallback no setter nativo + eventos |
| Lista (`<select>`) e opções (rádio) | equivalências PT/EN ("Advanced" = "Avançado", "Sim" = "Yes"), "contém" e faixas numéricas ("3–5 anos") |
| Dropdown customizado (`role=combobox`) | digita e escolhe a opção mais próxima na lista que abrir |
| Arquivo | anexa o PDF do CV escolhido para a vaga |
| Data | converte dd/mm/aaaa para o formato do campo |

## 4. Quando age (contexto)

- Só em **páginas de vaga ou candidatura**: vaga já conhecida, etapa de candidatura vinda de uma vaga (mesma aba, mesmo em outro domínio), site de vagas conhecido, página que a API classifica como vaga, ou formulário que pede currículo. Em qualquer outra página com formulário não preenche nem anexa nada.
- Em iframes, só formulários de ATS conhecidos (Greenhouse/Lever embutidos).
- Ignora: senhas, cartões, códigos de verificação/OTP, CPF/RG, tokens e busca do site.
- Aprende só com eventos reais do usuário (`isTrusted`) fora do preenchimento automático. Cada resposta enviada leva a origem (`user`, `profile`, `bank`, `rule`, `ai`); só `user` vira banco de respostas.
- Registro automático do envio só se você clicou em enviar há menos de 15 minutos (formulário ou botão "Submit/Enviar/Candidatar").
- Botões do painel aceitam só cliques reais: scripts da página não acionam o assistente.
- Estado da aba (vaga de origem, respostas, PDF anexado) fica no service worker (`chrome.storage.session`), não no `sessionStorage` da página.
- O token fica em `chrome.storage.local` e só o service worker o usa; as páginas só recebem a configuração sem o token. O service worker aceita das páginas apenas os endpoints do assistente.

## 5. Painel e opções

- Lista do que falta (clique para ir ao campo), barra de progresso, "Completar com IA", "Preencher de novo", "Desfazer", "Revisar preenchidos" (com a origem de cada resposta), "Já me candidatei".
- Cabeçalho: desligar neste site, opções, minimizar, fechar nesta página.
- **Currículo para esta vaga** (sempre em vaga conhecida): gerar com IA (`POST /assistant/tailor`, acompanha em `GET /assistant/tasks/{id}`), ver o PDF, aprovar (`POST /assistant/tailored/{id}/approve`, só se o validador passou) e anexar no formulário. O aprovado passa a ser o PDF anexado nesta vaga.
- **Modais do site:** formulário em `<dialog>` modal (top layer) leva o painel para dentro do modal e o mantém como popover por cima, clicável.
- Popup: liga/desliga geral, por site, pausa de 1 hora, preencher sozinho, anexar CV, contadores do dia.
- Opções (`defaults.js` é a fonte única): API e dashboard, token, preencher sozinho, anexar CV, contornos, sugestão ao clicar, avaliar vagas sozinho, registrar ao enviar, quando mostrar o painel, lado da tela, começar minimizado, sites ignorados.
- Não cobre o campo em que você está: troca de lado; dá para arrastar pelo título (posição lembrada por site) ou minimizar num botão flutuante.

## 6. Vagas do LinkedIn via alertas do Gmail

- **Alertas de vaga** do LinkedIn que chegam no Gmail viram vagas automaticamente (`modules/inbox/linkedin_alerts.py`), sem IA para classificar.
- A descoberta não coleta nem automatiza resultados de busca do LinkedIn. Para uma vaga encontrada manualmente, abra a página e use o assistente para avaliá-la (`/assistant/analyze`).

## 7. API (token próprio da extensão)

`POST /assistant/page` · `POST /assistant/analyze` · `POST /assistant/fields` (devolve `values`, `suggestions`, `sensitive`, `open`) · `POST /assistant/suggest` (só leitura: não mexe no banco de respostas) · `POST /assistant/learn` · `POST /assistant/submitted` (respostas com origem + `render_id` do PDF anexado) · `GET /assistant/resume` (header `x-render-id`) · `GET /assistant/logo`

Autenticação: `Authorization: Extension <token>`. O cookie do dashboard só vale fora de origens de extensão e com o header CSRF nas escritas.

## 8. Testes

`backend/tests/e2e/test_extension.py` carrega a extensão real no Chromium contra a API real e páginas locais:
- formulário de vaga: preenchimento automático, equivalência de opções, campo de etapa seguinte, "Completar com IA", "Usar" da sugestão, pergunta sensível intocada, registro do envio com o PDF e a origem de cada resposta;
- página que não é de vaga: nada preenchido nem anexado, sem painel;
- interruptores: desligado não age, religar volta sem recarregar, site ignorado some na hora, clique forjado pela página não registra candidatura.
