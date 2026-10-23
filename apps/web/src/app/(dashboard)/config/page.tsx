"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, Settings } from "lucide-react";
import { AiGuide, ExtensionGuide, GoogleGuide, LatexGuide, LinkedinGuide, TelegramGuide } from "@/components/setup-guides";
import { Badge, Button, Card, CardTitle, Field, Input, PageHeader, Skeleton, Tabs, buttonVariants } from "@/components/ui";
import { rawFetch } from "@/lib/api";
import { useGet, useSend, useTask } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";

type Integrations = {
  google: { configured: boolean; connected: boolean; status: string | null; account: string; push: boolean; push_endpoint: string; last_sync: string | null; last_error: string | null };
  anthropic: { configured: boolean; fake: boolean; model_default: string; budget_usd: number };
  telegram: { bot_configured: boolean };
};
type Weights = { version: number; chance_weights: Record<string, number>; attractiveness_weights: Record<string, number>; thresholds: Record<string, number>; source: string; is_current: boolean; metrics: Record<string, number> };

const TABS = [
  { value: "integracoes", label: "Integrações" },
  { value: "automacao", label: "Automação e limites" },
  { value: "avaliacao", label: "Avaliação" },
  { value: "notificacoes", label: "Notificações" },
  { value: "sistema", label: "Sistema e dados" },
] as const;
type Tab = (typeof TABS)[number]["value"];

export default function ConfigPage() {
  return <Suspense><Config /></Suspense>;
}

function Config() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  // a aba vem da URL: o sino de notificações e o voltar do navegador funcionam mesmo já estando aqui
  const tab = (TABS.find((t) => t.value === sp.get("tab"))?.value ?? "integracoes") as Tab;
  const setTab = (v: Tab) => router.replace(`${pathname}?tab=${v}`, { scroll: false });
  useEffect(() => { if (sp.get("google") === "ok") toast.success("Google conectado"); }, [sp]);
  return (
    <div>
      <PageHeader icon={<Settings size={20} />} title="Configurações" description="Integrações com guias passo a passo, automação, avaliação e dados." />
      <Tabs value={tab} onChange={setTab} items={[...TABS]} />
      {tab === "integracoes" && <IntegrationsTab />}
      {tab === "automacao" && <AutomationTab />}
      {tab === "avaliacao" && <ScoringTab />}
      {tab === "notificacoes" && <NotificationsTab />}
      {tab === "sistema" && <SystemTab />}
    </div>
  );
}

type OnbStatus = {
  checks: Record<string, { ok: boolean; detail?: string; configured?: boolean; account?: string | null; redirect_uris?: string[] }>;
};

function GuideCard({ title, ok, detail, children, defaultOpen }: { title: string; ok: boolean; detail?: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <Card className="p-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left" aria-expanded={open}>
        <div>
          <p className="font-semibold tracking-tight">{title}</p>
          {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={ok ? "success" : "warning"}>{ok ? "ativo" : "pendente"}</Badge>
          <ChevronDown size={16} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
        </div>
      </button>
      {open && <div className="border-t px-5 py-5">{children}</div>}
    </Card>
  );
}

function IntegrationsTab() {
  const qc = useQueryClient();
  const { data } = useGet<Integrations>(["integrations"], "/api/v1/integrations");
  const { data: st } = useGet<OnbStatus>(["onboarding"], "/api/v1/onboarding/status");
  const disconnect = useSend("POST", "/api/v1/integrations/google/disconnect", { success: "Google desconectado", invalidate: [["integrations"], ["onboarding"]] });
  if (!data || !st) return <Skeleton className="h-64" />;
  const g = data.google;
  const c = st.checks;
  return (
    <div className="space-y-3">
      <GuideCard title="IA (Claude)" ok={c.ai.ok} detail={c.ai.detail} defaultOpen={!c.ai.ok}>
        <AiGuide onTested={() => qc.invalidateQueries({ queryKey: ["onboarding"] })} />
        <p className="mt-4 text-xs text-muted-foreground">Orçamento mensal (só para API paga): US$ {data.anthropic.budget_usd} · modelo padrão <code>{data.anthropic.model_default}</code></p>
      </GuideCard>
      <GuideCard title="Gmail e Agenda (Google)" ok={c.google.ok} detail={g.connected ? `${c.google.account} · última leitura ${formatDateTime(g.last_sync)}` : "não conectado"}>
        <GoogleGuide redirectUris={c.google.redirect_uris ?? []} configured={!!c.google.configured} connected={c.google.ok} />
        {g.last_error && <p className="mt-3 text-xs text-danger">Último erro: {g.last_error}</p>}
        {g.connected && <Button variant="ghost" size="sm" className="mt-2" onClick={() => disconnect.mutate(undefined)}>Desconectar</Button>}
      </GuideCard>
      <GuideCard title="Telegram" ok={c.telegram.ok} detail={c.telegram.configured ? "bot configurado" : "sem bot"}>
        <TelegramGuide configured={!!c.telegram.configured} linked={c.telegram.ok} />
      </GuideCard>
      <GuideCard title="Extensão do navegador" ok={c.extension.ok} detail="painel da vaga, preenchimento e registro da candidatura">
        <ExtensionGuide />
      </GuideCard>
      <GuideCard title="Vagas do LinkedIn" ok={c.google.ok} detail="alertas pelo Gmail e análise manual de vagas individuais">
        <LinkedinGuide gmailConnected={c.google.ok} />
      </GuideCard>
      <GuideCard title="PDF dos currículos (tectonic)" ok={c.latex.ok} detail={c.latex.detail}>
        <LatexGuide />
      </GuideCard>
    </div>
  );
}

function useSetting<T>(key: string) {
  const q = useGet<T>(["settings", key], `/api/v1/settings/${key}`);
  const save = useSend<T>("PUT", `/api/v1/settings/${key}`, { success: "Configuração salva", invalidate: [["settings", key]] });
  return { data: q.data, save };
}


function AutomationTab() {
  const flags = useSetting<Record<string, boolean>>("flags");
  const resume = useSetting<{ match_threshold: number; tailored_requires_approval: boolean }>("resume");
  const tracking = useSetting<{ ghost_after_days: number; followup_after_days: number }>("tracking");
  const FLAG_LABEL: Record<string, string> = { gmail: "Ler Gmail", gmail_labels: "Marcar e-mails de vaga no Gmail (Vagas/…)", calendar: "Google Agenda", telegram: "Telegram", job_sources: "Coletar vagas de sites de empresas", company_research: "Pesquisa de empresas" };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-2 text-sm">
        <CardTitle>Módulos</CardTitle>
        {flags.data && Object.entries(flags.data).map(([k, v]) => (
          <label key={k} className="flex items-center gap-2">
            <input type="checkbox" checked={v} onChange={(e) => flags.save.mutate({ ...flags.data!, [k]: e.target.checked })} /> {FLAG_LABEL[k] ?? k}
          </label>
        ))}
      </Card>
      <Card className="space-y-2 text-sm">
        <CardTitle>Envios</CardTitle>
        <p className="text-muted-foreground">Nada é enviado em seu nome: você se candidata no site da vaga (o assistente do navegador preenche) e e-mails ficam como rascunho para você revisar e enviar.</p>
      </Card>
      <Card className="space-y-2 text-sm">
        <CardTitle>Currículos e acompanhamento</CardTitle>
        {resume.data && (
          <>
            <Field label="Limiar para usar coringa (0–1)"><Input type="number" step="0.05" defaultValue={resume.data.match_threshold} onBlur={(e) => resume.save.mutate({ ...resume.data!, match_threshold: Number(e.target.value) })} /></Field>
            <label className="flex gap-2"><input type="checkbox" checked={resume.data.tailored_requires_approval} onChange={(e) => resume.save.mutate({ ...resume.data!, tailored_requires_approval: e.target.checked })} /> CV específico exige minha aprovação</label>
          </>
        )}
        {tracking.data && (
          <div className="grid grid-cols-2 gap-2">
            <Field label="Sem retorno após (dias)"><Input type="number" defaultValue={tracking.data.ghost_after_days} onBlur={(e) => tracking.save.mutate({ ...tracking.data!, ghost_after_days: Number(e.target.value) })} /></Field>
            <Field label="Follow-up após (dias)"><Input type="number" defaultValue={tracking.data.followup_after_days} onBlur={(e) => tracking.save.mutate({ ...tracking.data!, followup_after_days: Number(e.target.value) })} /></Field>
          </div>
        )}
      </Card>
    </div>
  );
}

const LABEL: Record<string, string> = {
  fit_tecnico: "Aderência técnica", senioridade: "Senioridade", concorrencia: "Concorrência", frescor: "Frescor", responsividade: "Responsividade",
  recrutador_ativo: "Recrutador ativo", nao_fantasma: "Sinais de vaga fantasma", conexoes: "Conexões", reputacao: "Reputação", cultura: "Cultura",
  saude: "Saúde", remuneracao: "Remuneração", carreira: "Carreira", condicoes: "Condições", alinhamento: "Alinhamento",
};

function ScoringTab() {
  const qc = useQueryClient();
  const { data } = useGet<Weights>(["weights"], "/api/v1/scoring/weights");
  const { data: history } = useGet<Weights[]>(["weights", "history"], "/api/v1/scoring/weights/history");
  const [w, setW] = useState<Weights | null>(null);
  useEffect(() => { if (data) setW(data); }, [data]);
  const save = useSend("PUT", "/api/v1/scoring/weights", { success: "Pesos salvos: reavaliando vagas", invalidate: [["weights"], ["jobs"]] });
  const activate = useSend<number>("POST", (v) => `/api/v1/scoring/weights/${v}/activate`, { success: "Versão ativada", invalidate: [["weights"], ["jobs"]] });
  const calibrate = useSend<void, { task_id: string }>("POST", "/api/v1/scoring/calibrate");
  const task = useTask((r) => {
    const res = r as { status?: string; n?: number; needed?: number } | undefined;
    if (res?.status === "insufficient_data") toast.info(`Ainda são ${res.n} candidaturas com desfecho; a calibração precisa de ${res.needed}.`);
    qc.invalidateQueries({ queryKey: ["weights"] });
  });
  if (!w) return null;
  const slider = (group: "chance_weights" | "attractiveness_weights", k: string) => (
    <div key={k} className="flex items-center gap-2 text-sm">
      <span className="w-40">{LABEL[k] ?? k}</span>
      <input type="range" min={0} max={0.5} step={0.01} value={w[group][k]} onChange={(e) => setW({ ...w, [group]: { ...w[group], [k]: Number(e.target.value) } })} className="flex-1" aria-label={LABEL[k]} />
      <span className="w-10 text-right">{Math.round(w[group][k] * 100)}</span>
    </div>
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-2">
        <CardTitle>Chance (versão {w.version}, {w.source})</CardTitle>
        {Object.keys(w.chance_weights).map((k) => slider("chance_weights", k))}
        <CardTitle className="mt-4">Atratividade</CardTitle>
        {Object.keys(w.attractiveness_weights).map((k) => slider("attractiveness_weights", k))}
        <div className="grid grid-cols-2 gap-2 pt-2">
          <Field label="Limiar de Chance"><Input type="number" value={w.thresholds.chance} onChange={(e) => setW({ ...w, thresholds: { ...w.thresholds, chance: Number(e.target.value) } })} /></Field>
          <Field label="Limiar de Atratividade"><Input type="number" value={w.thresholds.attractiveness} onChange={(e) => setW({ ...w, thresholds: { ...w.thresholds, attractiveness: Number(e.target.value) } })} /></Field>
        </div>
        <p className="text-xs text-muted-foreground">Os pesos são normalizados ao salvar.</p>
        <Button onClick={() => save.mutate({ chance_weights: w.chance_weights, attractiveness_weights: w.attractiveness_weights, thresholds: w.thresholds })}>Salvar nova versão</Button>
      </Card>
      <Card className="space-y-2 text-sm">
        <CardTitle>Calibração com seus resultados</CardTitle>
        <p className="text-xs text-muted-foreground">Com ~100 candidaturas com desfecho, uma regressão logística propõe pesos. Nada é ativado sem você.</p>
        <Button variant="outline" disabled={task.running} onClick={async () => task.start((await calibrate.mutateAsync()).task_id)}>{task.running ? "Calibrando…" : "Calibrar agora"}</Button>
        <CardTitle className="mt-4">Histórico</CardTitle>
        <ul className="space-y-1 text-xs">
          {history?.map((h) => (
            <li key={h.version} className="flex items-center justify-between">
              <span>v{h.version} · {h.source}{h.metrics?.cv_auc ? ` · AUC ${h.metrics.cv_auc} (atual ${h.metrics.current_auc}) · n=${h.metrics.n}` : ""}</span>
              {h.is_current ? <Badge tone="success">ativa</Badge> : <Button size="sm" variant="ghost" onClick={() => activate.mutate(h.version)}>Ativar</Button>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function NotificationsTab() {
  const { data, save } = useSetting<{ quiet_hours: { start: string; end: string }; daily_digest: string }>("notifications");
  const list = useGet<{ id: string; kind: string; payload: Record<string, unknown>; created_at: string; read_at: string | null }[]>(["notifications"], "/api/v1/notifications");
  const readAll = useSend("POST", "/api/v1/notifications/read-all", { invalidate: [["notifications"]] });
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {data && (
        <Card className="space-y-2 text-sm">
          <CardTitle>Silêncio</CardTitle>
          <Field label="Das"><Input defaultValue={data.quiet_hours.start} onBlur={(e) => save.mutate({ ...data, quiet_hours: { ...data.quiet_hours, start: e.target.value } })} /></Field>
          <Field label="Até"><Input defaultValue={data.quiet_hours.end} onBlur={(e) => save.mutate({ ...data, quiet_hours: { ...data.quiet_hours, end: e.target.value } })} /></Field>
          <p className="text-xs text-muted-foreground">Convites de entrevista avisam sempre.</p>
        </Card>
      )}
      <Card className="space-y-2 text-sm lg:col-span-2">
        <div className="flex items-center justify-between"><CardTitle className="mb-0">Notificações</CardTitle><Button size="sm" variant="ghost" onClick={() => readAll.mutate(undefined)}>Marcar todas como lidas</Button></div>
        <ul className="space-y-1">
          {list.data?.map((n) => (
            <li key={n.id} className={n.read_at ? "text-muted-foreground" : ""}>
              <Badge>{n.kind}</Badge> {String(n.payload.company ?? "")} {String(n.payload.title ?? "")} {String(n.payload.message ?? "")} <span className="text-xs">· {formatDateTime(n.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function SystemTab() {
  const tasks = useGet<{ id: string; kind: string; status: string; message: string | null; error: string | null; created_at: string }[]>(["tasks"], "/api/v1/tasks", { refetchInterval: 15000 });
  const audit = useGet<{ items: { id: string; actor: string; action: string; reason: string | null; occurred_at: string }[] }>(["audit"], "/api/v1/audit?limit=50");
  const [confirm, setConfirm] = useState("");
  const del = useSend<{ confirm: string }>("DELETE", "/api/v1/data", { success: "Todos os dados foram apagados" });
  const exportAll = async () => {
    const r = await rawFetch("/api/v1/export", { method: "POST" });
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "application-tracker-export.zip";
    a.click();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="text-sm">
        <CardTitle>Tarefas recentes</CardTitle>
        <ul className="space-y-1 text-xs">
          {tasks.data?.map((t) => (
            <li key={t.id}><Badge tone={t.status === "failed" ? "danger" : t.status === "done" ? "success" : "warning"}>{t.status}</Badge> {t.kind} · {formatDateTime(t.created_at)}{t.error ? ` · ${t.error.slice(0, 120)}` : ""}</li>
          ))}
        </ul>
      </Card>
      <Card className="text-sm">
        <CardTitle>Auditoria</CardTitle>
        <ul className="max-h-80 space-y-1 overflow-auto text-xs">
          {audit.data?.items.map((a) => <li key={a.id}>{formatDateTime(a.occurred_at)} · {a.actor} · {a.action}{a.reason ? ` · ${a.reason}` : ""}</li>)}
        </ul>
      </Card>
      <Card className="space-y-2 text-sm">
        <CardTitle>Configuração inicial</CardTitle>
        <p className="text-muted-foreground">Reenviar o currículo, mudar cargos-alvo ou rever os guias de integração.</p>
        <a href="/onboarding" className={buttonVariants({ variant: "soft" })}>Abrir o onboarding</a>
      </Card>
      <Card className="space-y-2 text-sm">
        <CardTitle>Seus dados</CardTitle>
        <Button variant="outline" onClick={exportAll}>Exportar tudo (ZIP: JSON + CSV + PDFs)</Button>
        <p className="pt-2 text-xs text-danger">Apagar tudo é irreversível (os backups diários continuam no storage).</p>
        <div className="flex gap-2">
          <Input placeholder="Digite APAGAR TUDO" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          <Button variant="danger" disabled={confirm !== "APAGAR TUDO"} onClick={() => del.mutate({ confirm })}>Apagar</Button>
        </div>
      </Card>
    </div>
  );
}
