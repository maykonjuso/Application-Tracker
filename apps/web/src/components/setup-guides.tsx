"use client";
/**
 * Guias passo a passo das integrações (usados no onboarding e em Configurações). Escritos a partir das dúvidas
 * reais de configuração: cada armadilha conhecida aparece no passo em que ela acontece.
 */
import { AlertTriangle, Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button, Input, buttonVariants } from "@/components/ui";
import { useSend } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export function CopyBox({ value, label }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-lg border bg-muted/60 px-3 py-1.5 font-mono text-xs">
      <span className="min-w-0 flex-1 truncate" title={value}>
        {value}
      </span>
      <button
        type="button"
        aria-label={`Copiar ${label ?? value}`}
        onClick={async () => {
          await navigator.clipboard.writeText(value);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }}
        className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-card hover:text-foreground"
      >
        {done ? <Check size={13} className="text-success" /> : <Copy size={13} />}
      </button>
    </div>
  );
}

export function Steps({ children }: { children: React.ReactNode }) {
  return <ol className="space-y-3">{children}</ol>;
}

export function Step({ n, title, children }: { n: number; title: React.ReactNode; children?: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent text-xs font-semibold text-primary">{n}</span>
      <div className="min-w-0 flex-1 space-y-1.5 pt-0.5 text-sm">
        <p className="font-medium">{title}</p>
        {children && <div className="space-y-1.5 text-muted-foreground">{children}</div>}
      </div>
    </li>
  );
}

export function Tip({ children, tone = "warning" }: { children: React.ReactNode; tone?: "warning" | "info" }) {
  return (
    <div
      className={cn(
        "flex gap-2 rounded-lg border px-3 py-2 text-xs",
        tone === "warning" ? "border-warning/30 bg-warning/8 text-foreground" : "border-primary/20 bg-accent text-foreground",
      )}
    >
      <AlertTriangle size={14} className={cn("mt-0.5 shrink-0", tone === "warning" ? "text-warning" : "text-primary")} />
      <div>{children}</div>
    </div>
  );
}

function Ext({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline">
      {children} <ExternalLink size={11} />
    </a>
  );
}

const C = ({ children }: { children: React.ReactNode }) => (
  <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px] text-foreground">{children}</code>
);

type OS = "windows" | "mac" | "linux" | "wsl";
const OS_LABEL: Record<OS, string> = { windows: "Windows", mac: "macOS", linux: "Linux", wsl: "WSL" };

export function OsTabs({ render, oses = ["windows", "mac", "linux", "wsl"] }: { render: (os: OS) => React.ReactNode; oses?: OS[] }) {
  const guess: OS =
    typeof navigator === "undefined" ? "windows" : /Mac/i.test(navigator.userAgent) ? "mac" : /Win/i.test(navigator.userAgent) ? "windows" : "linux";
  const [os, setOs] = useState<OS>(oses.includes(guess) ? guess : oses[0]);
  return (
    <div className="space-y-2">
      <div className="inline-flex gap-1 rounded-lg border bg-muted/50 p-0.5">
        {oses.map((o) => (
          <button
            key={o}
            type="button"
            onClick={() => setOs(o)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              os === o ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {OS_LABEL[o]}
          </button>
        ))}
      </div>
      <div>{render(os)}</div>
    </div>
  );
}

const RESTART = (
  <Tip tone="info">
    Depois de mexer no <C>.env</C>, reinicie a API e o worker (Ctrl+C no terminal do <C>tracker-dev</C> e rode de novo).
  </Tip>
);

export function AiGuide({ onTested }: { onTested?: () => void }) {
  const test = useSend<void, { message: string; model: string }>("POST", "/api/v1/onboarding/test/ai");
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        A IA lê as vagas, monta os currículos e classifica os e-mails. Há dois caminhos: escolha <b>um</b>.
      </p>
      <div className="rounded-xl border p-4">
        <p className="mb-3 text-sm font-semibold">Opção A · Claude Code com a sua assinatura (recomendado, sem custo extra)</p>
        <Steps>
          <Step n={1} title="Instale o Claude Code na máquina onde a API roda">
            <OsTabs
              render={(os) =>
                os === "windows" ? (
                  <>
                    <p>No PowerShell:</p>
                    <CopyBox value="irm https://claude.ai/install.ps1 | iex" />
                  </>
                ) : (
                  <>
                    <p>No terminal{os === "wsl" ? " do WSL (não no Windows: a API roda dentro do WSL)" : ""}:</p>
                    <CopyBox value="curl -fsSL https://claude.ai/install.sh | bash" />
                  </>
                )
              }
            />
          </Step>
          <Step n={2} title="Faça login com a sua conta Claude (Pro ou Max)">
            <p>
              Rode <C>claude</C> uma vez e siga o login no navegador. Depois feche com <C>/exit</C>.
            </p>
          </Step>
          <Step n={3} title="Deixe o CLI atualizado">
            <CopyBox value="claude update" />
            <p>Versões antigas não aceitam os modelos novos (precisa da 2.1.280 ou mais recente).</p>
          </Step>
        </Steps>
        <div className="mt-3">
          <Tip tone="info">Cada uso conta no limite da sua assinatura. Para poupar, a leitura em massa de vagas usa um modelo mais leve.</Tip>
        </div>
      </div>
      <div className="rounded-xl border p-4">
        <p className="mb-3 text-sm font-semibold">Opção B · Chave da API da Anthropic (pago por uso)</p>
        <Steps>
          <Step n={1} title={<>Entre em <Ext href="https://console.anthropic.com">console.anthropic.com</Ext></>}>
            <p>Em <b>Billing</b>, coloque créditos (US$ 5–10 bastam para começar) e defina um limite mensal.</p>
          </Step>
          <Step n={2} title="Crie a chave em API Keys → Create Key">
            <p>
              Ela começa com <C>sk-ant-</C> e só aparece uma vez. Cole no <C>.env</C>: <C>ANTHROPIC_API_KEY=sk-ant-…</C>
            </p>
          </Step>
        </Steps>
        <div className="mt-3">
          <Tip>A assinatura do claude.ai não vale para a API: são cobranças separadas.</Tip>
        </div>
      </div>
      {RESTART}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="soft"
          disabled={test.isPending}
          onClick={async () => {
            const r = await test.mutateAsync();
            toast.success(`IA funcionando (${r.model}): ${r.message}`);
            onTested?.();
          }}
        >
          {test.isPending ? "Testando… (até ~30 s)" : "Testar a IA agora"}
        </Button>
      </div>
    </div>
  );
}

export function GoogleGuide({ redirectUris, configured, connected }: { redirectUris: string[]; configured: boolean; connected: boolean }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Opcional, mas é o que faz o app acompanhar respostas no Gmail, separar os e-mails em marcadores <C>Vagas/*</C> e marcar entrevistas
        na Agenda. Ele <b>nunca envia</b> e-mails: só cria rascunhos.
      </p>
      <Steps>
        <Step n={1} title={<>Crie um projeto no <Ext href="https://console.cloud.google.com/projectcreate">Google Cloud</Ext></>}>
          <p>Qualquer nome (ex.: “Application Tracker”). Não precisa de cartão de crédito.</p>
        </Step>
        <Step n={2} title="Ative as duas APIs (uma só credencial serve para as duas)">
          <p>
            Em <b>APIs e serviços → Biblioteca</b>: busque <Ext href="https://console.cloud.google.com/apis/library/gmail.googleapis.com">Gmail API</Ext> e clique em
            Ativar; depois <Ext href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com">Google Calendar API</Ext> e Ativar.
          </p>
        </Step>
        <Step n={3} title="Configure a tela de consentimento (Google Auth Platform)">
          <p>
            Tipo de usuário <b>Externo</b>, preencha nome do app e seu e-mail. Se perguntar “Quais dados você vai acessar?”, escolha <b>Dados do
            usuário</b>. A tela de escopos pode ser pulada.
          </p>
        </Step>
        <Step n={4} title={<>Adicione você como <b>usuário de teste</b></>}>
          <p>
            Em <b>Público-alvo → Usuários de teste → Add users</b>, coloque o e-mail da conta Google que vai conectar e salve.
          </p>
          <Tip>Sem isso o Google mostra “Erro 403: access_denied · app em teste”.</Tip>
        </Step>
        <Step n={5} title={<>Crie a credencial: <b>Credenciais → Criar credenciais → ID do cliente OAuth</b></>}>
          <p>
            Tipo de aplicativo: <b>Aplicativo da Web</b>. (Não escolha “Extensão do Chrome” nem crie uma “Chave de API”: elas não acessam o Gmail.)
          </p>
          <p>Deixe “Origens JavaScript” vazio. Em <b>URIs de redirecionamento autorizados</b>, adicione as duas:</p>
          {redirectUris.map((u) => (
            <CopyBox key={u} value={u} label="URI de redirecionamento" />
          ))}
          <Tip>Se aparecer “redirect_uri_mismatch”, a URI cadastrada não está idêntica a essas (http, porta 8000, sem barra no fim).</Tip>
        </Step>
        <Step n={6} title="Copie o ID e a chave secreta para o .env">
          <CopyBox value="GOOGLE_CLIENT_ID=....apps.googleusercontent.com" />
          <CopyBox value="GOOGLE_CLIENT_SECRET=GOCSPX-..." />
          {RESTART}
        </Step>
        <Step n={7} title={<>Clique em <b>Conectar Google</b> e autorize</>}>
          <p>
            Vai aparecer “O Google não verificou este app”. É normal para um app pessoal: clique em <b>Avançado → Acessar (não seguro)</b> e
            marque <b>todas</b> as permissões.
          </p>
          <Tip tone="info">
            Com o app em modo de teste o Google pede para reconectar a cada ~7 dias. Para não expirar, em <b>Público-alvo</b> clique em{" "}
            <b>Publicar app</b> (continua só para você; a tela de “não verificado” segue aparecendo no login).
          </Tip>
        </Step>
      </Steps>
      <div className="flex flex-wrap items-center gap-2">
        {configured ? (
          <a href="/api/v1/integrations/google/connect" className={buttonVariants()}>{connected ? "Reconectar Google" : "Conectar Google"}</a>
        ) : (
          <Button disabled>Conectar Google</Button>
        )}
        {!configured && <span className="text-xs text-muted-foreground">Preencha o .env e reinicie a API para liberar o botão.</span>}
      </div>
    </div>
  );
}

export function TelegramGuide({ configured, linked }: { configured: boolean; linked: boolean }) {
  const link = useSend<void, { code: string }>("POST", "/api/v1/integrations/telegram/link");
  const test = useSend("POST", "/api/v1/onboarding/test/telegram", { success: "Mensagem enviada: confira o Telegram" });
  const [code, setCode] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Opcional. Avisa de entrevistas, respostas e perguntas pendentes, e aceita comandos (<C>/hoje</C>, <C>/status</C>, <C>/pendentes</C>,{" "}
        <C>/stop</C>). Funciona com o app rodando só no seu PC.
      </p>
      <Steps>
        <Step n={1} title={<>No Telegram, abra o <Ext href="https://t.me/BotFather">@BotFather</Ext> e mande <C>/newbot</C></>}>
          <p>
            Escolha um nome e um usuário que termine em <C>bot</C>. Ele responde com o token (algo como <C>123456:ABC…</C>).
          </p>
        </Step>
        <Step n={2} title="Cole o token no .env e reinicie a API">
          <CopyBox value="TELEGRAM_BOT_TOKEN=123456:ABC..." />
        </Step>
        <Step n={3} title="Vincule o seu chat">
          <p>Gere um código e mande para o seu bot. Pronto: só esse chat recebe as mensagens.</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="soft" disabled={!configured} onClick={async () => setCode((await link.mutateAsync()).code)}>
              Gerar código
            </Button>
            {code && <CopyBox value={`/start ${code}`} label="comando de vínculo" />}
          </div>
          <p className="text-xs">
            Alternativa: mande qualquer mensagem ao bot, abra <C>https://api.telegram.org/bot&lt;SEU_TOKEN&gt;/getUpdates</C> no navegador, copie o
            número de <C>&quot;chat&quot;:{"{"}&quot;id&quot;</C> e coloque em <C>TELEGRAM_ALLOWED_CHAT_ID</C> no .env.
          </p>
        </Step>
      </Steps>
      <Button variant="outline" size="sm" disabled={!configured} onClick={() => test.mutate(undefined)}>
        {linked ? "Enviar mensagem de teste" : "Testar"}
      </Button>
    </div>
  );
}

export function ExtensionGuide() {
  const gen = useSend<void, { token: string }>("POST", "/api/v1/assistant/token", { success: "Token gerado", invalidate: [["onboarding"]] });
  const [token, setToken] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Opcional e muito útil: enquanto você navega numa vaga (LinkedIn, Gupy, Greenhouse…), mostra as notas, a reputação da empresa, preenche o
        formulário com seu perfil e CV e registra a candidatura quando <b>você</b> clica em enviar. Ela nunca envia nada sozinha.
      </p>
      <Steps>
        <Step n={1} title="Gere o token da extensão">
          <Button size="sm" variant="soft" onClick={async () => setToken((await gen.mutateAsync()).token)}>
            Gerar token
          </Button>
          {token && (
            <>
              <CopyBox value={token} label="token" />
              <p className="text-xs text-warning">Ele aparece só agora. Gerar outro invalida o anterior.</p>
            </>
          )}
        </Step>
        <Step n={2} title="Carregue a extensão no Chrome ou Edge">
          <OsTabs
            render={(os) =>
              os === "wsl" ? (
                <>
                  <p>O Chrome do Windows não carrega bem pastas de dentro do WSL. Copie para uma pasta do Windows (troque SEU_USUARIO):</p>
                  <CopyBox value="mkdir -p /mnt/c/Users/SEU_USUARIO/tracker-extension && cp -r apps/extension/. /mnt/c/Users/SEU_USUARIO/tracker-extension/" />
                  <p>
                    Abra <C>chrome://extensions</C>, ative o <b>Modo do desenvolvedor</b> (canto superior direito), clique em <b>Carregar sem
                    compactação</b> e escolha <C>C:\Users\SEU_USUARIO\tracker-extension</C>.
                  </p>
                  <p className="text-xs">Quando o projeto atualizar a extensão, rode o comando de cópia de novo e clique em ↻ Recarregar no card dela.</p>
                </>
              ) : (
                <p>
                  Abra <C>chrome://extensions</C> (no Edge: <C>edge://extensions</C>), ative o <b>Modo do desenvolvedor</b>, clique em{" "}
                  <b>Carregar sem compactação</b> e escolha a pasta <C>apps/extension</C> do projeto.
                </p>
              )
            }
          />
        </Step>
        <Step n={3} title="Fixe e configure">
          <p>
            Clique no ícone 🧩 e fixe o Application Tracker. Botão direito no ícone → <b>Opções</b>: endereço da API <C>http://localhost:8000</C>{" "}
            e o token. Salve.
          </p>
        </Step>
        <Step n={4} title="Use">
          <p>
            Abra uma vaga e o formulário de candidatura: o assistente <b>preenche sozinho</b> o que sabe com segurança (contorno verde), inclusive nas
            próximas etapas. <b>“Completar com IA”</b> escreve as perguntas abertas (contorno azul, revise). Perguntas sensíveis, como salário e visto,
            ficam para você. Ao clicar num campo vazio aparece uma sugestão 💡 com “Usar”. Se o painel cobrir um campo, ele muda de lado; dá para
            arrastar pelo título ou minimizar. A API precisa estar rodando.
          </p>
        </Step>
      </Steps>
    </div>
  );
}

export function LatexGuide() {
  return (
    <div className="space-y-2 text-sm">
      <p className="text-muted-foreground">
        Os PDFs dos currículos são gerados com o <b>tectonic</b> (LaTeX que baixa só o que precisa). Instale e reinicie o worker:
      </p>
      <OsTabs
        oses={["windows", "mac", "linux"]}
        render={(os) =>
          os === "mac" ? (
            <CopyBox value="brew install tectonic" />
          ) : os === "windows" ? (
            <>
              <CopyBox value="scoop install tectonic" />
              <p className="text-xs text-muted-foreground">
                Sem o Scoop? Baixe o .zip “x86_64-pc-windows-msvc” em <Ext href="https://github.com/tectonic-typesetting/tectonic/releases">releases do tectonic</Ext>{" "}
                e coloque o <C>tectonic.exe</C> numa pasta do PATH.
              </p>
              <p className="text-xs text-muted-foreground">
                Se o app roda no Windows e o Tectonic está instalado no WSL2, ele é detectado na distro padrão. Verifique com{" "}
                <C>wsl --exec zsh -ic &apos;tectonic --version&apos;</C> e reinicie o worker.
              </p>
            </>
          ) : (
            <>
              <CopyBox value="curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh && mkdir -p ~/.local/bin && mv tectonic ~/.local/bin/" />
              <p className="text-xs text-muted-foreground">Vale também para o WSL.</p>
            </>
          )
        }
      />
    </div>
  );
}

/** Campo de texto com chips (ex.: cargos-alvo). Enter ou vírgula adiciona. */
export function TagInput({
  value,
  onChange,
  placeholder,
  suggestions = [],
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [text, setText] = useState("");
  const add = (t: string) => {
    const v = t.trim();
    if (v && !value.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...value, v]);
    setText("");
  };
  const rest = suggestions.filter((s) => !value.some((x) => x.toLowerCase() === s.toLowerCase())).slice(0, 12);
  return (
    <div className="space-y-2">
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border bg-card px-2 py-1.5 focus-within:border-primary focus-within:ring-3 focus-within:ring-primary/15">
        {value.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-primary">
            {v}
            <button type="button" aria-label={`Remover ${v}`} onClick={() => onChange(value.filter((x) => x !== v))} className="hover:text-danger">
              ×
            </button>
          </span>
        ))}
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(text);
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          onBlur={() => text && add(text)}
          placeholder={value.length ? "" : placeholder}
          className="h-7 min-w-40 flex-1 border-0 bg-transparent px-1 shadow-none focus:ring-0"
        />
      </div>
      {rest.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {rest.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className="rounded-full border border-dashed px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary"
            >
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function LinkedinGuide({ gmailConnected }: { gmailConnected: boolean }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        As vagas do LinkedIn entram na aba <b>Vagas</b> por alertas enviados ao Gmail. A extensão não coleta resultados de busca: ela só analisa uma vaga
        quando você a abre e auxilia no formulário quando você decide se candidatar.
      </p>
      <div className="rounded-xl border p-4">
        <p className="mb-3 text-sm font-semibold">Automático · alertas de vaga pelo Gmail (recomendado)</p>
        <Steps>
          <Step n={1} title="No LinkedIn, faça a busca que você quer acompanhar">
            <p>
              Em <b>Vagas</b>, digite o cargo (ex.: “Data Engineer”), escolha o local e os filtros (remoto, Easy Apply, data…).
            </p>
          </Step>
          <Step n={2} title={<>Ative o <b>alerta de vaga</b> dessa busca</>}>
            <p>
              Ligue a chave <b>“Ativar alerta”</b> (ou “Set alert”) no topo dos resultados. Em <b>Gerenciar alertas</b>, deixe a frequência <b>diária</b> e a
              notificação por <b>e-mail</b> ligada. Crie um alerta por cargo/local que interessar.
            </p>
          </Step>
          <Step n={3} title="Pronto: cada e-mail de alerta vira vagas no tracker">
            <p>O app lê os alertas no Gmail, cria as vagas, dá nota e marca o e-mail como “Vagas/LinkedIn”. Não gasta IA para isso.</p>
            {!gmailConnected && <Tip>Precisa do Gmail conectado (aba Gmail e Agenda).</Tip>}
          </Step>
        </Steps>
      </div>
      <div className="rounded-xl border p-4">
        <p className="mb-3 text-sm font-semibold">Na hora · analisar uma vaga específica</p>
        <Steps>
          <Step n={1} title="Abra manualmente uma vaga individual no LinkedIn" />
          <Step n={2} title={<>No painel da extensão, escolha <b>“Avaliar e salvar vaga”</b></>}>
            <p>A avaliação usa os detalhes da vaga aberta; resultados de busca não são extraídos em lote.</p>
          </Step>
          <Step n={3} title="Candidate-se no site quando quiser">
            <p>A extensão pode preencher o formulário, mas não clica em enviar por você.</p>
          </Step>
        </Steps>
      </div>
    </div>
  );
}
