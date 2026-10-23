"use client";

import { Archive, ArrowRight, Ban, BarChart3, Check, Flame, Laptop, Layers, MapPin, Target, Wallet, X } from "lucide-react";
import { CompanyLogo, TechIcons, Tooltip } from "@/components/brand";
import { Card } from "@/components/ui";
import { toast } from "sonner";
import { useSend } from "@/lib/hooks";
import { isApplied } from "@/lib/job-actions";
import { QUADRANT_LABEL, QUADRANT_TONE, SENIORITY_LABEL, STATUS_LABEL, WORK_MODEL_LABEL } from "@/lib/labels";
import { cn, relativeTime } from "@/lib/utils";

export type Score = {
  chance: number;
  attractiveness: number;
  priority: number;
  quadrant: string;
  suggested_action: string;
  explanation: {
    chance?: { positives: string[]; negatives: string[] };
    attractiveness?: { positives: string[]; negatives: string[] };
    knockout?: string[];
    missing_must?: string[];
  };
};

export type JobRow = {
  id: string;
  company_id?: string;
  company_name: string | null;
  title: string;
  location: string | null;
  work_model: string | null;
  regime: string | null;
  seniority?: string | null;
  salary?: { min?: number | null; max?: number | null; currency?: string | null; period?: string | null } | null;
  requirements?: { must?: string[]; nice?: string[] } | null;
  apply_kind: string;
  apply_url?: string | null;
  canonical_url?: string | null;
  posted_at: string | null;
  first_seen_at: string;
  applicants_count: number | null;
  status: string;
  triage: string;
  score: Score | null;
  application_id: string | null;
  application_status: string | null;
};

const PERIOD: Record<string, string> = { month: "/mês", year: "/ano", hour: "/h" };

export function formatSalary(s: JobRow["salary"]): string | null {
  if (!s || (s.min == null && s.max == null)) return null;
  const cur = s.currency || "BRL";
  const fmt = (n: number) =>
    n.toLocaleString("pt-BR", { style: "currency", currency: cur, maximumFractionDigits: 0, notation: n >= 100000 ? "compact" : "standard" });
  const range = s.min != null && s.max != null && s.min !== s.max ? `${fmt(s.min)} – ${fmt(s.max)}` : fmt((s.max ?? s.min) as number);
  return `${range}${PERIOD[s.period ?? "month"] ?? ""}`;
}

const QUADRANT_ICON: Record<string, React.ComponentType<{ size?: number }>> = { top: Flame, bet: Target, volume: Layers, discard: Archive, blocked: Ban };

/** Cartão enxuto da vaga: o essencial com ícones. Clicar abre o painel com detalhes e o próximo passo. */
export function JobCard({
  job,
  onOpen,
  onTech,
  selected = false,
  onSelect,
}: {
  job: JobRow;
  onOpen?: (job: JobRow) => void;
  onTech?: (tech: string) => void;
  selected?: boolean;
  onSelect?: (job: JobRow) => void;
}) {
  const inv = { invalidate: [["jobs"], ["dashboard"], ["facets"]] };
  const skip = useSend("POST", `/api/v1/jobs/${job.id}/skip`, inv);
  const restore = useSend("POST", `/api/v1/jobs/${job.id}/restore`, { success: "Vaga de volta em Novas", ...inv });
  const s = job.score;
  const salary = formatSalary(job.salary);
  const QIcon = s ? QUADRANT_ICON[s.quadrant] ?? Target : null;
  const applied = isApplied(job.application_status);
  const open = () => onOpen?.(job);
  return (
    <Card
      interactive
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => e.target === e.currentTarget && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open())}
      className="group flex h-full cursor-pointer flex-col gap-3"
      aria-label={`${job.title} na ${job.company_name}. Abrir detalhes`}
    >
      <div className="flex items-start gap-3">
        <CompanyLogo id={job.company_id} name={job.company_name} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 font-semibold leading-snug tracking-tight group-hover:text-primary">{job.title}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {job.company_name} · {relativeTime(job.posted_at ?? job.first_seen_at)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {onSelect && (
            <Tooltip label={selected ? "Remover da Fila" : "Selecionar para a Fila"}>
              <button
                type="button"
                aria-label={selected ? "Remover da Fila" : "Selecionar para a Fila"}
                aria-pressed={selected}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(job);
                }}
                className={cn(
                  "inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-semibold transition-colors",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:bg-accent hover:text-primary",
                )}
              >
                <Check size={14} /> {selected ? "Selecionada" : "Selecionar"}
              </button>
            </Tooltip>
          )}
          {s && (
            <Tooltip label={`Match ${Math.round(s.priority)} · ${QUADRANT_LABEL[s.quadrant]}. Combina chance de ser chamado e quanto a vaga vale a pena.`}>
              <span
                className={cn(
                  "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-xs font-semibold",
                  { success: "bg-success/12 text-success", primary: "bg-accent text-primary", warning: "bg-warning/12 text-warning", danger: "bg-danger/12 text-danger", neutral: "bg-muted text-muted-foreground" }[QUADRANT_TONE[s.quadrant] ?? "neutral"],
                )}
              >
                {QIcon && <QIcon size={13} />} {Math.round(s.priority)}
              </span>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {job.location && <span className="inline-flex max-w-44 items-center gap-1 truncate"><MapPin size={13} className="shrink-0" /> {job.location}</span>}
        {job.work_model && <span className="inline-flex items-center gap-1"><Laptop size={13} /> {WORK_MODEL_LABEL[job.work_model] ?? job.work_model}</span>}
        {job.seniority && <span className="inline-flex items-center gap-1"><BarChart3 size={13} /> {SENIORITY_LABEL[job.seniority] ?? job.seniority}</span>}
        {salary && <span className="inline-flex items-center gap-1 font-medium text-success"><Wallet size={13} /> {salary}</span>}
      </div>

      <TechIcons techs={job.requirements?.must ?? []} max={7} onPick={onTech} />

      <div className="mt-auto flex items-center gap-2 border-t pt-3">
        {applied ? (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success">
            <Check size={14} /> {STATUS_LABEL[job.application_status!]}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
            Ver detalhes e candidatar <ArrowRight size={13} className="transition-transform group-hover:translate-x-0.5" />
          </span>
        )}
        <div className="flex-1" />
        {!applied && job.triage !== "skipped" && (
          <Tooltip label="Não tenho interesse (some da lista)">
            <button
              type="button"
              aria-label="Não tenho interesse"
              disabled={skip.isPending}
              onClick={(e) => {
                e.stopPropagation();
                skip.mutate(undefined, {
                  onSuccess: () => toast("Vaga descartada", { action: { label: "Desfazer", onClick: () => restore.mutate(undefined) } }),
                });
              }}
              onKeyDown={(e) => e.stopPropagation()}
              className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-danger/10 hover:text-danger"
            >
              <X size={15} />
            </button>
          </Tooltip>
        )}
      </div>
    </Card>
  );
}
