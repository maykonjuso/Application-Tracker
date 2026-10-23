"use client";
/**
 * Painel da vaga: tudo o que é preciso para decidir e agir, com UM próximo passo claro.
 * Fluxo: abrir no site → candidatar-se lá (a extensão ajuda) → "Já me candidatei" → acompanhar em Candidaturas.
 */
import {
  ArrowRight,
  AlertTriangle,
  BarChart3,
  Check,
  ChevronDown,
  ExternalLink,
  FileText,
  Laptop,
  MapPin,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  Wallet,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useRef, useState } from "react";
import { CompanyLogo, TechIcons } from "@/components/brand";
import { formatSalary, type JobRow } from "@/components/job-card";
import { Badge, Button, buttonVariants, ScoreRing, useDialog } from "@/components/ui";
import { useGet, useSend, useTask } from "@/lib/hooks";
import { QUADRANT_LABEL, QUADRANT_TONE, SENIORITY_LABEL, STATUS_LABEL, WORK_MODEL_LABEL } from "@/lib/labels";
import { isApplied } from "@/lib/job-actions";
import { cn, formatDate, relativeTime } from "@/lib/utils";

type QualityCheck = {
  key: string;
  label: string;
  status: "ok" | "warning" | "risk" | "unknown";
  detail: string;
  scoreable: boolean;
};
type Quality = { score: number; summary: string; score_explanation: string; checks: QualityCheck[] };
type Detail = JobRow & { description_text: string; benefits: string[]; quality?: Quality };


function Section({ title, children, defaultOpen = true }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t pt-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between text-left text-sm font-semibold" aria-expanded={open}>
        {title}
        <ChevronDown size={15} className={cn("text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  );
}

function QualityCheckup({ quality }: { quality: Quality }) {
  const attentionCount = quality.checks.filter((check) => check.status === "warning" || check.status === "risk").length;
  return (
    <Section title="Qualidade e integridade da informação">
      <div className="rounded-xl border bg-muted/30 p-3">
        <div className="flex items-center gap-3">
          <div className={cn(
            "grid h-11 w-11 shrink-0 place-items-center rounded-full text-xs font-bold",
            quality.score >= 80 ? "bg-success/10 text-success" : quality.score >= 55 ? "bg-warning/10 text-warning" : "bg-danger/10 text-danger",
          )}>
            {quality.score}/100
          </div>
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-sm font-semibold">
              {attentionCount > 0 ? <AlertTriangle size={15} className="text-warning" /> : <ShieldCheck size={15} className="text-success" />}
              {quality.summary}
            </p>
            <p className="text-xs text-muted-foreground">{quality.score_explanation}</p>
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs">
          <p className="font-semibold">Como interpretar o sinal de vaga fantasma</p>
          <p className="mt-1 text-muted-foreground">
            “Vaga fantasma” é um anúncio que permanece publicado sem uma contratação real/ativa. O indicador abaixo não confirma
            isso: ele só procura indícios indiretos. Considera 2 ou mais repostagens, anúncio com mais de 60 dias, descrição
            marcada como genérica pela análise automática e informação de demissões recentes da empresa.
          </p>
          <p className="mt-1 text-muted-foreground">
            “Nenhum fator conhecido reduziu a pontuação” quer dizer apenas que os dados disponíveis não cruzaram esses critérios.
            Não é uma probabilidade nem comprova que a empresa esteja contratando; dados ausentes são mostrados como desconhecidos.
          </p>
        </div>
        <ul className="mt-3 space-y-2">
          {quality.checks.map((check) => (
            <li key={check.key} className="flex items-start gap-2 text-xs">
              {check.status === "ok" ? (
                <Check size={14} className="mt-0.5 shrink-0 text-success" />
              ) : (
                <AlertTriangle size={14} className={cn(
                  "mt-0.5 shrink-0",
                  check.status === "risk" ? "text-danger" : check.status === "warning" ? "text-warning" : "text-muted-foreground",
                )} />
              )}
              <span><b>{check.label}:</b> {check.detail}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 border-t pt-2 text-xs text-muted-foreground">
          Confira a vaga no site oficial antes de compartilhar dados pessoais, documentos ou informações bancárias.
        </p>
      </div>
    </Section>
  );
}

export function JobDrawer({ job, onClose }: { job: JobRow | null; onClose: () => void }) {
  const box = useRef<HTMLElement>(null);
  useDialog(box, !!job, onClose);
  return (
    <AnimatePresence>
      {job && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            ref={box}
            tabIndex={-1}
            aria-modal="true"
            role="dialog"
            aria-label={`Vaga ${job.title}`}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l bg-card shadow-2xl"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 40 }}
          >
            <DrawerBody key={job.id} job={job} onClose={onClose} />
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function DrawerBody({ job, onClose }: { job: JobRow; onClose: () => void }) {
  const { data: detail } = useGet<Detail>(["job", job.id], `/api/v1/jobs/${job.id}`);
  const j = { ...job, ...(detail ?? {}) } as Detail;
  const inv = { invalidate: [["jobs"], ["dashboard"], ["facets"], ["applications"], ["job", job.id]] };
  const applied = useSend<{ job_id: string; channel: string }>("POST", "/api/v1/applications/manual", {
    success: "Candidatura registrada: acompanhe em Candidaturas",
    ...inv,
  });
  const skip = useSend("POST", `/api/v1/jobs/${job.id}/skip`, { success: "Vaga descartada", ...inv });
  const tailor = useSend<{ job_id: string }, { task_id: string }>("POST", "/api/v1/resumes/variants/tailored");
  const tailorTask = useTask();
  const fb = useSend<{ verdict: string }>("POST", `/api/v1/jobs/${job.id}/feedback`, { success: "Obrigado: isso ajusta as próximas notas" });
  const [opened, setOpened] = useState(false);
  const rawLink = j.apply_url || j.canonical_url;
  const link = (() => {
    if (!rawLink) return null;
    try {
      const parsed = new URL(rawLink);
      return !parsed.username && !parsed.password && (parsed.protocol === "https:" || parsed.protocol === "http:") ? rawLink : null;
    } catch {
      return null;
    }
  })();
  const s = j.score;
  const status = j.application_status;
  const done = isApplied(status);
  const salary = formatSalary(j.salary);
  const pos = [...(s?.explanation.chance?.positives ?? []), ...(s?.explanation.attractiveness?.positives ?? [])];
  const neg = [...(s?.explanation.knockout ?? []), ...(s?.explanation.chance?.negatives ?? []), ...(s?.explanation.attractiveness?.negatives ?? [])];
  const techs = [...(j.requirements?.must ?? []), ...(j.requirements?.nice ?? [])];

  return (
    <>
      <div className="flex items-start gap-3 border-b p-5">
        <CompanyLogo id={j.company_id} name={j.company_name} className="h-12 w-12" />
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold leading-snug tracking-tight">{j.title}</h2>
          <p className="text-sm text-muted-foreground">{j.company_name}</p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {j.location && <span className="inline-flex items-center gap-1"><MapPin size={13} /> {j.location}</span>}
            {j.work_model && <span className="inline-flex items-center gap-1"><Laptop size={13} /> {WORK_MODEL_LABEL[j.work_model] ?? j.work_model}</span>}
            {j.seniority && <span className="inline-flex items-center gap-1"><BarChart3 size={13} /> {SENIORITY_LABEL[j.seniority] ?? j.seniority}</span>}
            {salary && <span className="inline-flex items-center gap-1 font-medium text-success"><Wallet size={13} /> {salary}</span>}
          </div>
        </div>
        <button onClick={onClose} aria-label="Fechar" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground">
          <X size={17} />
        </button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-5">
        {/* Próximo passo: o bloco mais importante da tela */}
        <div className={cn("rounded-2xl border p-4", done ? "border-success/30 bg-success/5" : "border-primary/25 bg-accent/60")}>
          {done ? (
            <>
              <p className="flex items-center gap-2 font-semibold text-success"><Check size={17} /> Você já se candidatou</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Status: <b>{STATUS_LABEL[status!]}</b>. As respostas da empresa no Gmail atualizam o status sozinhas.
              </p>
              <Link href="/candidaturas" className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
                Acompanhar em Candidaturas <ArrowRight size={14} />
              </Link>
            </>
          ) : j.triage === "skipped" ? (
            <>
              <p className="font-semibold">Você marcou esta vaga como sem interesse</p>
              <p className="mt-1 text-sm text-muted-foreground">Ela não aparece mais na lista de novas. Mudou de ideia? Abra o site e candidate-se normalmente.</p>
            </>
          ) : (
            <>
              <p className="text-sm font-semibold">Como se candidatar</p>
              <ol className="mt-3 space-y-3">
                <li className="flex gap-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">1</span>
                  <div className="flex-1">
                    <p className="text-sm">Abra a vaga no site da empresa e candidate-se por lá.</p>
                    {link ? (
                      <a href={link} target="_blank" rel="noreferrer" onClick={() => setOpened(true)} className={buttonVariants({ size: "lg", className: "mt-2 w-full" })}>
                        <ExternalLink size={16} /> Abrir a vaga no site
                      </a>
                    ) : (
                      <p className="mt-1 text-xs text-warning">
                        {rawLink ? "Link bloqueado por segurança: use apenas um endereço HTTP ou HTTPS válido." : "Esta vaga não tem link. Procure pelo título no site da empresa."}
                      </p>
                    )}
                    <p className="mt-1.5 text-xs text-muted-foreground">Com a extensão instalada, ela preenche o formulário para você. Quem clica em enviar é você.</p>
                  </div>
                </li>
                <li className="flex gap-3">
                  <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold", opened ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>2</span>
                  <div className="flex-1">
                    <p className="text-sm">Enviou? Registre aqui para acompanhar as respostas.</p>
                    <Button
                      variant={opened ? "default" : "outline"}
                      className="mt-2 w-full"
                      disabled={applied.isPending}
                      onClick={async () => {
                        await applied.mutateAsync({ job_id: job.id, channel: "external" });
                        onClose();
                      }}
                    >
                      <Check size={16} /> Já me candidatei
                    </Button>
                    <p className="mt-1.5 text-xs text-muted-foreground">Se usou a extensão, isso acontece sozinho ao enviar.</p>
                  </div>
                </li>
              </ol>
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    await skip.mutateAsync(undefined);
                    onClose();
                  }}
                >
                  <X size={14} /> Não tenho interesse
                </Button>
              </div>
            </>
          )}
        </div>

        {/* Currículo para a vaga: sempre disponível (antes ou depois de se candidatar) */}
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border p-4">
          <FileText size={18} className="text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">Currículo para esta vaga</p>
            <p className="text-xs text-muted-foreground">
              {tailorTask.running
                ? `${tailorTask.progress?.message ?? "Gerando"}… (cerca de 30 a 60 segundos)`
                : "A IA adapta o seu CV a esta vaga, só com fatos do seu currículo. Você revisa e aprova em Currículos; a extensão anexa o aprovado."}
            </p>
          </div>
          <Button
            size="sm"
            disabled={tailorTask.running || tailor.isPending}
            onClick={async () => {
              try {
                tailorTask.start((await tailor.mutateAsync({ job_id: job.id })).task_id);
              } catch {}
            }}
          >
            {tailorTask.running ? "Gerando…" : "Gerar currículo"}
          </Button>
          <Link href="/curriculos" className="text-xs font-medium text-primary hover:underline">Ver em Currículos</Link>
        </div>

        {s && (
          <Section title="Combina com você?">
            <div className="flex items-center gap-5">
              <ScoreRing value={s.chance} label="Chance" size={54} />
              <ScoreRing value={s.attractiveness} label="Atratividade" size={54} />
              <div className="text-xs text-muted-foreground">
                <p><b className="text-foreground">Chance</b>: quão provável é você ser chamado (requisitos, senioridade, concorrência).</p>
                <p className="mt-1"><b className="text-foreground">Atratividade</b>: quanto a vaga vale a pena (empresa, salário, modelo de trabalho).</p>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2">
              <Badge tone={QUADRANT_TONE[s.quadrant]}>{QUADRANT_LABEL[s.quadrant]}</Badge>
              <span className="text-xs text-muted-foreground">{s.suggested_action}</span>
            </div>
            {(pos.length > 0 || neg.length > 0) && (
              <ul className="mt-3 space-y-1 text-sm">
                {pos.map((r) => <li key={r} className="flex gap-2 text-success"><Check size={15} className="mt-0.5 shrink-0" />{r}</li>)}
                {neg.map((r) => <li key={r} className="flex gap-2 text-danger"><X size={15} className="mt-0.5 shrink-0" />{r}</li>)}
              </ul>
            )}
            <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
              As notas fazem sentido?
              <Button size="sm" variant="ghost" className="h-7" onClick={() => fb.mutate({ verdict: "good" })}><ThumbsUp size={13} /> Sim</Button>
              <Button size="sm" variant="ghost" className="h-7" onClick={() => fb.mutate({ verdict: "not_interested" })}><ThumbsDown size={13} /> Não me interessa</Button>
            </div>
          </Section>
        )}

        {techs.length > 0 && (
          <Section title="Tecnologias pedidas">
            <TechIcons techs={j.requirements?.must ?? []} max={20} size={22} />
            {(j.requirements?.nice ?? []).length > 0 && (
              <>
                <p className="mb-1.5 mt-3 text-xs font-medium text-muted-foreground">Desejáveis</p>
                <TechIcons techs={j.requirements?.nice ?? []} max={20} size={18} />
              </>
            )}
          </Section>
        )}

        {detail?.benefits && detail.benefits.length > 0 && (
          <Section title="Benefícios" defaultOpen={false}>
            <div className="flex flex-wrap gap-1.5">{detail.benefits.map((b) => <Badge key={b}>{b}</Badge>)}</div>
          </Section>
        )}

        <Section title="Descrição da vaga" defaultOpen={false}>
          {detail ? (
            <p className="max-h-96 overflow-y-auto whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{detail.description_text || "Sem descrição."}</p>
          ) : (
            <p className="text-sm text-muted-foreground">Carregando…</p>
          )}
        </Section>

        <p className="pt-2 text-xs text-muted-foreground">
          Encontrada {relativeTime(j.first_seen_at)}
          {j.posted_at ? ` · publicada em ${formatDate(j.posted_at)}` : ""} ·{" "}
          <Link href={`/vagas/${j.id}`} className="text-primary hover:underline">página completa da vaga</Link>
        </p>
      </div>
    </>
  );
}
