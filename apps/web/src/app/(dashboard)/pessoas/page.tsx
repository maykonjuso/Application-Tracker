"use client";

import { CompanyLogo } from "@/components/brand";
import { Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, SkeletonList, Textarea } from "@/components/ui";
import { useGet, useSend } from "@/lib/hooks";
import { formatDate } from "@/lib/utils";

type Person = {
  id: string;
  full_name: string;
  company_id: string | null;
  company_name: string | null;
  role: string | null;
  linkedin_url: string | null;
  email: string | null;
  kind: string;
  notes: string | null;
  referral_status: string | null;
  last_contact_at: string | null;
  applications: { application_id: string; job_title: string; status: string; relation: string }[];
};
type Company = { id: string; name: string };

const KIND: Record<string, string> = { recruiter: "Recrutador(a)", hiring_manager: "Gestor(a)", interviewer: "Entrevistador(a)", connection: "Conexão", referrer: "Pode indicar" };
const empty = { full_name: "", company_id: "", role: "", linkedin_url: "", email: "", kind: "recruiter", notes: "", referral_status: "" };

export default function PeoplePage() {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const params = new URLSearchParams({ ...(q && { q }), ...(kind && { kind }) });
  const { data, isLoading } = useGet<Person[]>(["people", params.toString()], `/api/v1/people?${params}`);
  const { data: companies } = useGet<Company[]>(["companies"], "/api/v1/companies");
  const [edit, setEdit] = useState<typeof empty & { id?: string } | null>(null);
  const inv = { invalidate: [["people"]] };
  const create = useSend("POST", "/api/v1/people", { success: "Salvo", ...inv });
  const update = useSend<typeof empty & { id?: string }>("PUT", (p) => `/api/v1/people/${p.id}`, { success: "Salvo", ...inv });
  const del = useSend<string>("DELETE", (id) => `/api/v1/people/${id}`, { success: "Removido", ...inv });
  const save = async () => {
    if (!edit) return;
    const body = { ...edit, company_id: edit.company_id || null, referral_status: edit.referral_status || null };
    if (edit.id) await update.mutateAsync(body as typeof edit);
    else await create.mutateAsync(body);
    setEdit(null);
  };
  return (
    <div>
      <PageHeader icon={<Users size={20} />} title="Pessoas" description="Recrutadores, gestores, entrevistadores e conexões (indicações aumentam a nota de Chance)" actions={<Button onClick={() => setEdit(empty)}>Adicionar pessoa</Button>} />
      <div className="mb-3 flex gap-2">
        <Input placeholder="Buscar" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="max-w-48">
          <option value="">Todos</option>
          {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      </div>
      {isLoading ? <SkeletonList count={6} className="md:grid-cols-2 xl:grid-cols-3" /> : !data?.length ? (
        <EmptyState title="Ninguém ainda">Pessoas citadas em e-mails e mensagens entram aqui automaticamente.</EmptyState>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.map((p) => (
            <Card key={p.id} className="space-y-1 text-sm">
              <div className="flex items-start justify-between gap-2">
                {p.company_name && <CompanyLogo name={p.company_name} className="h-9 w-9" />}
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{p.full_name}</p>
                  <p className="text-xs text-muted-foreground">{p.role ?? "—"} · {p.company_name ?? "sem empresa"}</p>
                </div>
                <Badge tone={p.kind === "referrer" ? "success" : "neutral"}>{KIND[p.kind] ?? p.kind}</Badge>
              </div>
              {p.referral_status && <p className="text-xs">Indicação: {p.referral_status}</p>}
              {p.email && <p className="text-xs">{p.email}</p>}
              {p.linkedin_url && <a className="text-xs text-primary underline" href={p.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>}
              <p className="text-xs text-muted-foreground">Último contato: {formatDate(p.last_contact_at)}</p>
              {p.applications.map((a) => <Link key={a.application_id + a.relation} href={`/candidaturas/${a.application_id}`} className="block text-xs text-primary underline">{a.job_title} ({a.relation})</Link>)}
              <div className="flex gap-2 pt-1">
                <Button size="sm" variant="outline" onClick={() => setEdit({ ...empty, ...Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v ?? ""])), id: p.id } as typeof empty & { id: string })}>Editar</Button>
                <Button size="sm" variant="ghost" onClick={() => del.mutate(p.id)}>Remover</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "Editar pessoa" : "Nova pessoa"}>
        {edit && (
          <div className="space-y-2">
            <Field label="Nome"><Input value={edit.full_name} onChange={(e) => setEdit({ ...edit, full_name: e.target.value })} /></Field>
            <Field label="Empresa">
              <Select value={edit.company_id} onChange={(e) => setEdit({ ...edit, company_id: e.target.value })}>
                <option value="">—</option>
                {companies?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
            <Field label="Cargo"><Input value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })} /></Field>
            <Field label="Tipo"><Select value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="Indicação"><Select value={edit.referral_status} onChange={(e) => setEdit({ ...edit, referral_status: e.target.value })}><option value="">—</option><option value="asked">Pedi</option><option value="accepted">Aceitou indicar</option><option value="declined">Não pode</option></Select></Field>
            <Field label="E-mail"><Input value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="LinkedIn"><Input value={edit.linkedin_url} onChange={(e) => setEdit({ ...edit, linkedin_url: e.target.value })} /></Field>
            <Field label="Notas"><Textarea value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
            <Button disabled={!edit.full_name} onClick={save}>Salvar</Button>
          </div>
        )}
      </Modal>
    </div>
  );
}
