"use client";

import { Calendar } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardTitle, EmptyState, Field, Input, PageHeader, Select, SkeletonList, buttonVariants } from "@/components/ui";
import { useGet, useSend } from "@/lib/hooks";

type Event = { id: string; application_id: string | null; kind: string; title: string; description: string | null; starts_at: string; ends_at: string; location_or_link: string | null; status: string; google_event_id: string | null; meta: { conflicts?: string[]; local_only?: boolean; sync_error?: string } };

const KIND: Record<string, string> = { interview: "Entrevista", assessment_deadline: "Prazo de teste", study_block: "Estudo", followup: "Follow-up" };
const TZ = "America/Sao_Paulo";

function dayKey(iso: string) {
  return new Date(iso).toLocaleDateString("pt-BR", { timeZone: TZ, weekday: "long", day: "2-digit", month: "2-digit" });
}

export default function AgendaPage() {
  const { data, isLoading } = useGet<Event[]>(["calendar"], "/api/v1/calendar/events");
  const inv = { invalidate: [["calendar"], ["dashboard"]] };
  const confirm = useSend<string>("POST", (id) => `/api/v1/calendar/events/${id}/confirm`, { success: "Confirmado e enviado ao Google Agenda", ...inv });
  const cancel = useSend<string>("POST", (id) => `/api/v1/calendar/events/${id}/cancel`, { success: "Cancelado", ...inv });
  const groups = new Map<string, Event[]>();
  (data ?? []).filter((e) => e.status !== "cancelled").forEach((e) => groups.set(dayKey(e.starts_at), [...(groups.get(dayKey(e.starts_at)) ?? []), e]));
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="xl:col-span-2">
        <PageHeader icon={<Calendar size={20} />} title="Agenda" description="Eventos do processo seletivo (sincronizados com o Google Agenda quando conectado)" />
        {isLoading ? <SkeletonList count={4} /> : groups.size === 0 ? (
          <EmptyState title="Nada agendado" />
        ) : (
          [...groups.entries()].map(([day, evs]) => (
            <div key={day} className="mb-4">
              <p className="mb-2 text-sm font-semibold capitalize">{day}</p>
              <div className="space-y-2">
                {evs.map((e) => (
                  <Card key={e.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                    <div>
                      <p className="font-medium">
                        {new Date(e.starts_at).toLocaleTimeString("pt-BR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" })}–
                        {new Date(e.ends_at).toLocaleTimeString("pt-BR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" })} · {e.title}
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <Badge>{KIND[e.kind] ?? e.kind}</Badge>
                        {e.status === "draft" && <Badge tone="warning">rascunho: confirme</Badge>}
                        {e.meta?.local_only && <Badge>só local</Badge>}
                        {e.meta?.conflicts?.length ? <Badge tone="danger">conflito: {e.meta.conflicts.join(", ")}</Badge> : null}
                        {e.meta?.sync_error && <Badge tone="danger">erro de sincronização</Badge>}
                      </div>
                    </div>
                    <div className="flex gap-2">
                      {e.location_or_link && <a href={e.location_or_link} target="_blank" rel="noreferrer" className={buttonVariants({ size: "sm", variant: "outline" })}>Link</a>}
                      {e.application_id && <Link href={`/candidaturas/${e.application_id}`} className={buttonVariants({ size: "sm", variant: "ghost" })}>Candidatura</Link>}
                      {e.status === "draft" && <Button size="sm" onClick={() => confirm.mutate(e.id)}>Confirmar</Button>}
                      <Button size="sm" variant="ghost" onClick={() => window.confirm("Cancelar este evento na sua agenda?") && cancel.mutate(e.id)}>Cancelar evento</Button>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
      <Availability />
    </div>
  );
}

function Availability() {
  const [duration, setDuration] = useState(60);
  const [days, setDays] = useState(7);
  const { data, refetch, isFetching } = useGet<{ start: string; end: string }[]>(["availability", duration, days], `/api/v1/calendar/availability?duration=${duration}&days=${days}&count=5`);
  const text = (data ?? [])
    .map((s) => `- ${new Date(s.start).toLocaleString("pt-BR", { timeZone: TZ, weekday: "long", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} (horário de Brasília)`)
    .join("\n");
  return (
    <Card className="h-fit space-y-3">
      <CardTitle>Meus horários livres</CardTitle>
      <div className="flex gap-2">
        <Field label="Duração"><Select value={duration} onChange={(e) => setDuration(Number(e.target.value))}><option value={30}>30 min</option><option value={45}>45 min</option><option value={60}>1 h</option><option value={90}>1h30</option></Select></Field>
        <Field label="Próximos dias"><Input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} /></Field>
      </div>
      <pre className="whitespace-pre-wrap text-sm">{isFetching ? "Consultando…" : text || "Sem horários livres na janela configurada."}</pre>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => refetch()}>Atualizar</Button>
        <Button size="sm" onClick={() => navigator.clipboard.writeText(text)}>Copiar</Button>
      </div>
    </Card>
  );
}
