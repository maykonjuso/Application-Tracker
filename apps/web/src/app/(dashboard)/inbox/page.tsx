"use client";

import { Inbox as InboxIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Badge, Button, Card, EmptyState, Input, Modal, PageHeader, Select, Tabs, Textarea, Skeleton } from "@/components/ui";
import { useGet, useSend } from "@/lib/hooks";
import { EMAIL_LABEL } from "@/lib/labels";
import { formatDateTime } from "@/lib/utils";

type Email = {
  id: string;
  from_addr: string;
  from_name: string | null;
  subject: string;
  snippet: string;
  received_at: string;
  classification: string | null;
  classification_confidence: number | null;
  extracted: { summary?: string; company_name?: string | null; job_title?: string | null; suggestion?: Suggestion | null };
  application_id: string | null;
  link_status: string;
};
type Draft = { id: string; kind: string; application_id: string | null; to: string | null; subject: string | null; body: string; status: string; gmail_draft_id: string | null; created_at: string };
type Suggestion = { kind: "link" | "create"; application_id?: string; company: string; job_title: string | null; confidence: number };
type AppLite = { id: string; job_title: string; company_name: string };

export default function InboxPage() {
  return (
    <Suspense>
      <Inbox />
    </Suspense>
  );
}

function Inbox() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab: "emails" | "rascunhos" = sp.get("tab") === "rascunhos" ? "rascunhos" : "emails";
  const setTab = (v: "emails" | "rascunhos") => router.replace(`${pathname}?tab=${v}`, { scroll: false });
  const sync = useSend("POST", "/api/v1/inbox/sync", { success: "Sincronização do Gmail iniciada" });
  return (
    <div>
      <PageHeader icon={<InboxIcon size={20} />} title="Inbox" description="E-mails e mensagens classificados e ligados às candidaturas" actions={<Button variant="outline" onClick={() => sync.mutate(undefined)}>Sincronizar Gmail</Button>} />
      <Tabs value={tab} onChange={setTab} items={[{ value: "emails", label: "E-mails" }, { value: "rascunhos", label: "Rascunhos" }]} />
      {tab === "emails" && <Emails />}
      {tab === "rascunhos" && <Drafts />}
    </div>
  );
}

function Emails() {
  const [filter, setFilter] = useState("");
  const { data, isLoading } = useGet<Email[]>(["inbox", filter], `/api/v1/inbox${filter ? `?link_status=${filter}` : ""}`);
  const { data: apps } = useGet<AppLite[]>(["applications", "lite"], "/api/v1/applications?include_closed=false");
  const link = useSend<{ id: string; application_id: string }>("POST", (v) => `/api/v1/inbox/${v.id}/link`, { success: "Associado", invalidate: [["inbox"], ["applications"]] });
  const reclass = useSend<{ id: string; label: string }>("POST", (v) => `/api/v1/inbox/${v.id}/reclassify`, { success: "Rótulo corrigido", invalidate: [["inbox"]] });
  const createApp = useSend<string>("POST", (id) => `/api/v1/inbox/${id}/create-application`, { success: "Candidatura criada e e-mail associado", invalidate: [["inbox"], ["applications"]] });
  return (
    <div className="space-y-3">
      <Select value={filter} onChange={(e) => setFilter(e.target.value)} className="max-w-56">
        <option value="">Todos os relevantes</option>
        <option value="ambiguous">Precisam de associação</option>
        <option value="auto">Associados automaticamente</option>
      </Select>
      {isLoading ? (
        <Skeleton className="h-40" />
      ) : !data?.length ? (
        filter ? (
          <EmptyState title="Nada com esse filtro">Troque o filtro acima para ver os outros e-mails.</EmptyState>
        ) : (
          <EmptyState title="Nenhum e-mail ainda">Conecte o Google em Configurações → Integrações.</EmptyState>
        )
      ) : (
        data.map((e) => (
          <Card key={e.id} className="space-y-1 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-medium">{e.subject}</p>
              <div className="flex gap-1">
                {e.classification && <Badge tone={e.classification === "rejeicao" ? "danger" : e.classification === "irrelevante" ? "neutral" : "primary"}>{EMAIL_LABEL[e.classification]}</Badge>}
                {e.link_status === "ambiguous" && <Badge tone="warning">associar</Badge>}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{e.from_name ?? ""} &lt;{e.from_addr}&gt; · {formatDateTime(e.received_at)}{e.classification_confidence != null ? ` · confiança ${Math.round(e.classification_confidence * 100)}%` : ""}</p>
            <p>{e.extracted.summary ?? e.snippet}</p>
            {!e.application_id && <SuggestionBox e={e} onLink={(id) => link.mutate({ id: e.id, application_id: id })} onCreate={() => createApp.mutate(e.id)} busy={link.isPending || createApp.isPending} />}
            <div className="flex flex-wrap items-center gap-2">
              {e.application_id ? (
                <Link className="text-xs text-primary underline" href={`/candidaturas/${e.application_id}`}>ver candidatura</Link>
              ) : (
                <Select defaultValue="" onChange={(ev) => ev.target.value && link.mutate({ id: e.id, application_id: ev.target.value })} className="max-w-xs" aria-label="associar">
                  <option value="">Associar a uma candidatura…</option>
                  {apps?.map((a) => <option key={a.id} value={a.id}>{a.company_name} · {a.job_title}</option>)}
                </Select>
              )}
              <Select defaultValue="" onChange={(ev) => ev.target.value && reclass.mutate({ id: e.id, label: ev.target.value })} className="max-w-48" aria-label="corrigir rótulo">
                <option value="">Corrigir rótulo…</option>
                {Object.entries(EMAIL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}

/** Proposta da IA para um e-mail que não foi associado sozinho: você confirma com um clique. */
function SuggestionBox({ e, onLink, onCreate, busy }: { e: Email; onLink: (applicationId: string) => void; onCreate: () => void; busy: boolean }) {
  const s = e.extracted.suggestion;
  const company = s?.company ?? e.extracted.company_name;
  if (s?.kind === "link" && s.application_id) {
    const appId = s.application_id;
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2">
        <span>
          Parece ser da candidatura <strong>{s.company}</strong>{s.job_title ? ` · ${s.job_title}` : ""}
          <span className="text-xs text-muted-foreground"> ({Math.round(s.confidence * 100)}%)</span>
        </span>
        <Button size="sm" disabled={busy} onClick={() => onLink(appId)}>Confirmar</Button>
      </div>
    );
  }
  if (e.link_status !== "ambiguous" || !company) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed px-3 py-2">
      <span>
        Não achei esta candidatura no tracker: <strong>{company}</strong>{(s?.job_title ?? e.extracted.job_title) ? ` · ${s?.job_title ?? e.extracted.job_title}` : ""}
      </span>
      <Button size="sm" variant="outline" disabled={busy} onClick={onCreate}>Criar candidatura</Button>
    </div>
  );
}

function Drafts() {
  const { data } = useGet<Draft[]>(["drafts"], "/api/v1/drafts?status=proposed");
  const [edit, setEdit] = useState<Draft | null>(null);
  const inv = { invalidate: [["drafts"], ["dashboard"]] };
  const save = useSend<Draft>("PUT", (d) => `/api/v1/drafts/${d.id}`, { success: "Salvo", ...inv });
  const push = useSend<string>("POST", (id) => `/api/v1/drafts/${id}/push-to-gmail`, { success: "Rascunho criado no Gmail: revise e envie por lá", ...inv });
  const sent = useSend<string>("POST", (id) => `/api/v1/drafts/${id}/mark-sent`, { success: "Marcado como enviado", ...inv });
  const discard = useSend<string>("POST", (id) => `/api/v1/drafts/${id}/discard`, inv);
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">O sistema nunca envia e-mails nem mensagens por você: aqui ficam rascunhos para revisar.</p>
      {!data?.length ? (
        <EmptyState title="Nenhum rascunho pendente" />
      ) : (
        data.map((d) => (
          <Card key={d.id} className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-medium">{d.subject ?? "(sem assunto)"} <Badge>{d.kind}</Badge></p>
              <span className="text-xs text-muted-foreground">para {d.to ?? "—"} · {formatDateTime(d.created_at)}</span>
            </div>
            <p className="whitespace-pre-wrap">{d.body}</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setEdit(d)}>Editar</Button>
              {d.kind.startsWith("linkedin") ? (
                <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(d.body)}>Copiar texto</Button>
              ) : (
                <Button size="sm" onClick={() => push.mutate(d.id)}>Criar no Gmail</Button>
              )}
              <Button size="sm" variant="success" onClick={() => sent.mutate(d.id)}>Já enviei este e-mail</Button>
              <Button size="sm" variant="ghost" onClick={() => window.confirm("Descartar este rascunho?") && discard.mutate(d.id)}>Descartar</Button>
              {d.application_id && <Link className="self-center text-xs text-primary underline" href={`/candidaturas/${d.application_id}`}>candidatura</Link>}
            </div>
          </Card>
        ))
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title="Editar rascunho" wide>
        {edit && (
          <div className="space-y-2">
            <Input value={edit.to ?? ""} onChange={(e) => setEdit({ ...edit, to: e.target.value })} placeholder="Para" />
            <Input value={edit.subject ?? ""} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} placeholder="Assunto" />
            <Textarea rows={12} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
            <Button onClick={async () => { await save.mutateAsync(edit); setEdit(null); }}>Salvar</Button>
          </div>
        )}
      </Modal>
    </div>
  );
}

