"use client";

import { CompanyLogo } from "@/components/brand";
import { Mic } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, PageHeader, Select, SkeletonList, Tabs } from "@/components/ui";
import { useGet, useSend } from "@/lib/hooks";
import { INTERVIEW_KIND } from "@/lib/labels";
import { formatDateTime } from "@/lib/utils";

type Interview = { id: string; application_id: string; round: number; kind: string; starts_at: string | null; company_name: string | null; job_title: string | null; dossier_status: string | null; link: string | null; self_rating: number | null };
type Assessment = { id: string; application_id: string; kind: string; instructions: string | null; deadline_at: string | null; repo_url: string | null; status: string };
type AppLite = { id: string; job_title: string; company_name: string };

export default function InterviewsPage() {
  const [tab, setTab] = useState<"proximas" | "passadas" | "testes">("proximas");
  return (
    <div>
      <PageHeader icon={<Mic size={20} />} title="Entrevistas" description="Dossiê, simulação e registro de cada etapa" actions={<NewInterview />} />
      <Tabs value={tab} onChange={setTab} items={[{ value: "proximas", label: "Próximas" }, { value: "passadas", label: "Passadas" }, { value: "testes", label: "Testes técnicos" }]} />
      {tab === "testes" ? <Assessments /> : <List upcoming={tab === "proximas"} />}
    </div>
  );
}

function List({ upcoming }: { upcoming: boolean }) {
  const { data, isLoading } = useGet<Interview[]>(["interviews", upcoming], `/api/v1/interviews?upcoming=${upcoming}`);
  if (isLoading) return <SkeletonList count={4} className="md:grid-cols-2" />;
  if (!data?.length) return <EmptyState title="Nenhuma entrevista">Convites detectados no Gmail aparecem aqui automaticamente.</EmptyState>;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {data.map((i) => (
        <Link key={i.id} href={`/entrevistas/${i.id}`}>
          <Card interactive>
            <div className="flex items-center gap-3">
              <CompanyLogo name={i.company_name} />
              <div className="min-w-0">
                <p className="truncate font-medium">{i.job_title}</p>
                <p className="truncate text-xs text-muted-foreground">{i.company_name}</p>
              </div>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{i.round}ª etapa · {INTERVIEW_KIND[i.kind] ?? i.kind} · {formatDateTime(i.starts_at)}</p>
            <div className="mt-2 flex gap-1">
              <Badge tone={i.dossier_status === "ready" ? "success" : "warning"}>dossiê {i.dossier_status ?? "pendente"}</Badge>
              {i.self_rating && <Badge>nota {i.self_rating}/5</Badge>}
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}

function NewInterview() {
  const [open, setOpen] = useState(false);
  const { data: apps } = useGet<AppLite[]>(["applications", "lite"], open ? "/api/v1/applications?include_closed=false" : null);
  const [f, setF] = useState({ application_id: "", kind: "hr", starts_at: "", duration_min: 60, link: "" });
  const create = useSend("POST", "/api/v1/interviews", { success: "Entrevista registrada: dossiê em preparação", invalidate: [["interviews"], ["calendar"], ["dashboard"], ["applications"]] });
  return (
    <>
      <Button onClick={() => setOpen(true)}>Registrar entrevista</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Registrar entrevista">
        <div className="space-y-3">
          <Field label="Candidatura">
            <Select value={f.application_id} onChange={(e) => setF({ ...f, application_id: e.target.value })}>
              <option value="">Escolha…</option>
              {apps?.map((a) => <option key={a.id} value={a.id}>{a.company_name} · {a.job_title}</option>)}
            </Select>
          </Field>
          <Field label="Etapa">
            <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              {Object.entries(INTERVIEW_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </Field>
          <Field label="Quando"><Input type="datetime-local" value={f.starts_at} onChange={(e) => setF({ ...f, starts_at: e.target.value })} /></Field>
          <Field label="Duração (min)"><Input type="number" value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: Number(e.target.value) })} /></Field>
          <Field label="Link da chamada"><Input value={f.link} onChange={(e) => setF({ ...f, link: e.target.value })} /></Field>
          <Button
            disabled={!f.application_id || !f.starts_at}
            onClick={async () => {
              await create.mutateAsync({ ...f, starts_at: new Date(f.starts_at).toISOString(), link: f.link || null });
              setOpen(false);
            }}
          >Salvar</Button>
        </div>
      </Modal>
    </>
  );
}

function Assessments() {
  const { data, isLoading } = useGet<Assessment[]>(["assessments"], "/api/v1/assessments");
  const update = useSend<Assessment>("PUT", (a) => `/api/v1/assessments/${a.id}`, { success: "Atualizado", invalidate: [["assessments"]] });
  if (isLoading) return <SkeletonList count={3} />;
  if (!data?.length) return <EmptyState title="Nenhum teste técnico" />;
  return (
    <div className="space-y-2">
      {data.map((a) => (
        <Card key={a.id} className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link href={`/candidaturas/${a.application_id}`} className="font-medium hover:underline">{a.kind} · prazo {formatDateTime(a.deadline_at)}</Link>
            <Select value={a.status} onChange={(e) => update.mutate({ ...a, status: e.target.value })} className="w-40">
              <option value="pending">Pendente</option><option value="in_progress">Fazendo</option><option value="submitted">Entregue</option>
            </Select>
          </div>
          {a.instructions && <p className="text-muted-foreground">{a.instructions}</p>}
          <Input placeholder="Link do repositório entregue" defaultValue={a.repo_url ?? ""} onBlur={(e) => e.target.value !== (a.repo_url ?? "") && update.mutate({ ...a, repo_url: e.target.value })} />
        </Card>
      ))}
    </div>
  );
}
