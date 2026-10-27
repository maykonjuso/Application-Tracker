"use client";

import { CompanyLogo } from "@/components/brand";
import { Briefcase, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { JobCard, type JobRow } from "@/components/job-card";
import { JobDrawer } from "@/components/job-drawer";
import {
  Badge,
  Button,
  Card,
  Chip,
  EmptyState,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Skeleton,
  Stagger,
  StaggerItem,
  Tabs,
  Textarea,
  useDialog,
} from "@/components/ui";
import { ActiveFilters, EMPTY, FilterPanel, activeCount, fromParams, toParams, type Facets, type Filters } from "./job-filters";
import { toast } from "sonner";
import { useGet, useSend, useTask } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";

type Company = {
  id: string;
  name: string;
  ats_kind: string | null;
  ats_board_token: string | null;
  is_monitored: boolean;
  is_watchlisted: boolean;
  is_blocked: boolean;
  last_polled_at: string | null;
};
type Search = { id: string; name: string; query: string; sources: string[]; schedule_cron: string; is_active: boolean; last_run_at: string | null; filters: Record<string, unknown> };

export default function JobsPage() {
  const [tab, setTab] = useState<"vagas" | "empresas" | "buscas">("vagas");
  return (
    <div>
      <PageHeader
        icon={<Briefcase size={20} />}
        title="Vagas"
        description="Ordenadas por prioridade (Chance × Atratividade). Filtre por tecnologia, empresa, salário e mais."
        actions={<div className="flex flex-wrap gap-2"><DiscoverButton /><span data-tour="import"><ImportButton /></span></div>}
      />
      <div data-tour="tabs" className="inline-block">
        <Tabs value={tab} onChange={setTab} items={[{ value: "vagas", label: "Vagas" }, { value: "empresas", label: "Empresas monitoradas" }, { value: "buscas", label: "Buscas salvas" }]} />
      </div>
      {tab === "vagas" && (
        <Suspense fallback={<Skeleton className="h-96" />}>
          <JobsList />
        </Suspense>
      )}
      {tab === "empresas" && <Companies />}
      {tab === "buscas" && <Searches />}
    </div>
  );
}

const PAGE = 60;

function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function JobsList() {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [f, setF] = useState<Filters>(() => fromParams(new URLSearchParams(sp.toString())));
  // voltar/avançar do navegador muda a URL: os filtros acompanham
  const spKey = sp.toString();
  useEffect(() => {
    const fromUrl = fromParams(new URLSearchParams(spKey));
    setF((cur) => (toParams(cur).toString() === toParams(fromUrl).toString() ? cur : fromUrl));
  }, [spKey]);
  const [limit, setLimit] = useState(PAGE);
  const [drawer, setDrawer] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  useDialog(drawerRef, drawer, () => setDrawer(false));
  const [openJob, setOpenJob] = useState<JobRow | null>(null);
  const debounced = useDebounced(f);
  useEffect(() => {
    const qs = toParams(debounced).toString();
    if (qs !== new URLSearchParams(window.location.search).toString()) router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    setLimit(PAGE);
  }, [debounced, pathname, router]);
  const set = (p: Partial<Filters>) => setF((cur) => ({ ...cur, ...p }));
  const apiQs = toParams(debounced, true);
  apiQs.set("limit", String(limit));
  const { data, isLoading, isFetching } = useGet<JobRow[]>(["jobs", apiQs.toString()], `/api/v1/jobs?${apiQs}`, { keepPrevious: true });
  const { data: facets } = useGet<Facets>(["facets"], "/api/v1/jobs/facets");
  const jobs = data ?? [];
  const n = activeCount(f);
  const panel = <FilterPanel f={f} set={set} facets={facets} />;
  return (
    <div className="grid gap-6 lg:grid-cols-[290px_minmax(0,1fr)]">
      <aside className="hidden lg:block" data-tour="filters">
        <div className="sticky top-[4.5rem] max-h-[calc(100vh-5.5rem)] overflow-y-auto overscroll-contain rounded-xl border bg-card px-4 py-1 shadow-[var(--shadow-soft)] [scrollbar-width:none] hover:[scrollbar-width:thin]">
          <div className="flex items-center justify-between border-b py-3">
            <p className="flex items-center gap-2 text-sm font-semibold">
              <SlidersHorizontal size={15} /> Filtros
            </p>
            {n > 0 && (
              <button onClick={() => setF({ ...EMPTY })} className="text-xs font-medium text-muted-foreground hover:text-danger">
                Limpar ({n})
              </button>
            )}
          </div>
          {panel}
        </div>
      </aside>

      <div className="min-w-0 space-y-4">
        <div className="sticky top-14 z-20 -mx-2 space-y-2 rounded-b-xl bg-background/85 px-2 pb-3 pt-2 backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-2" data-tour="search">
            <div className="relative min-w-56 flex-1">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Buscar por título, empresa ou descrição" value={f.q} onChange={(e) => set({ q: e.target.value })} className="h-10 pl-9" />
            </div>
            <Button variant="outline" className="h-10 lg:hidden" onClick={() => setDrawer(true)}>
              <SlidersHorizontal size={15} /> Filtros {n > 0 && <Badge tone="primary">{n}</Badge>}
            </Button>
            <Select value={f.triage} onChange={(e) => set({ triage: e.target.value })} className="h-10 w-36" aria-label="Triagem">
              <option value="new">Novas</option>
              <option value="approved">Aprovadas</option>
              <option value="skipped">Descartadas</option>
              <option value="">Todas</option>
            </Select>
            <Select value={f.sort} onChange={(e) => set({ sort: e.target.value })} className="h-10 w-44" aria-label="Ordenar por">
              <option value="priority">Prioridade</option>
              <option value="recent">Mais recentes</option>
              <option value="chance">Chance</option>
              <option value="attractiveness">Atratividade</option>
              <option value="salary">Maior salário</option>
              <option value="company">Empresa (A–Z)</option>
            </Select>
          </div>

          <ActiveFilters f={f} set={set} facets={facets} onClear={() => setF({ ...EMPTY })} />
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {isLoading ? "Carregando…" : (
              <>
                <span className="font-semibold text-foreground">{jobs.length}{jobs.length === limit ? "+" : ""}</span> vaga(s)
                {facets ? ` de ${facets.total} abertas` : ""}
              </>
            )}
          </span>
          {isFetching && !isLoading && <span className="text-xs">atualizando…</span>}
        </div>

        {isLoading ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-56" />
            ))}
          </div>
        ) : !jobs.length ? (
          <EmptyState
            icon={<Briefcase size={22} />}
            title={n ? "Nenhuma vaga com esses filtros" : "Nenhuma vaga aqui ainda"}
            action={n ? <Button variant="outline" onClick={() => setF({ ...EMPTY })}>Limpar filtros</Button> : undefined}
          >
            {n ? "Tente remover algum filtro acima." : "Importe uma vaga, adicione empresas monitoradas ou crie uma busca salva."}
          </EmptyState>
        ) : (
          <>
            <Stagger key={toParams(debounced, true).toString()} className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,360px),1fr))] gap-3">
              {jobs.map((j, idx) => (
                <StaggerItem key={j.id} data-tour={idx === 0 ? "job-card" : undefined}>
                  <JobCard job={j} onOpen={setOpenJob} />
                </StaggerItem>
              ))}
            </Stagger>
            {(data?.length ?? 0) >= limit && limit < 200 && (
              <div className="flex justify-center pt-2">
                <Button variant="outline" onClick={() => setLimit((l) => Math.min(200, l + PAGE))}>
                  Mostrar mais vagas
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      <JobDrawer job={openJob} onClose={() => setOpenJob(null)} />
      <AnimatePresence>
        {drawer && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setDrawer(false)}
            />
            <motion.div
              ref={drawerRef}
              tabIndex={-1}
              aria-modal="true"
              role="dialog"
              aria-label="Filtros"
              className="fixed inset-y-0 right-0 z-50 w-[88vw] max-w-sm overflow-y-auto border-l bg-card px-4 pb-6 shadow-2xl lg:hidden"
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", stiffness: 380, damping: 38 }}
            >
              <div className="sticky top-0 flex items-center justify-between border-b bg-card py-3">
                <p className="font-semibold">Filtros</p>
                <Button size="sm" onClick={() => setDrawer(false)}>Ver {jobs.length} vaga(s)</Button>
              </div>
              {panel}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

type DiscoverResult = { companies: number; created: number; errors: string[] };

/** Busca agora as vagas de todas as empresas monitoradas (o agendador também faz isso sozinho). */
function DiscoverButton() {
  const task = useTask((r) => {
    const res = r as DiscoverResult | null;
    if (!res) return;
    const novas = res.created === 1 ? "1 vaga nova" : `${res.created} vagas novas`;
    toast.success(`${novas} em ${res.companies} empresas`, {
      description: res.errors.length ? `Não respondeu: ${res.errors.slice(0, 5).join(", ")}${res.errors.length > 5 ? "…" : ""}` : undefined,
    });
  });
  const send = useSend<void, { task_id: string }>("POST", "/api/v1/jobs/discover");
  const busy = task.running || send.isPending;
  return (
    <Button
      variant="outline"
      disabled={busy}
      title="Busca agora as vagas de todas as empresas monitoradas"
      onClick={async () => {
        try {
          const r = await send.mutateAsync(undefined);
          task.start(r.task_id);
        } catch {} // o erro já aparece no aviso
      }}
    >
      <RefreshCw size={15} className={busy ? "animate-spin" : undefined} />
      {busy ? (task.progress?.message && task.progress.pct > 1 ? task.progress.message : "Buscando…") : "Buscar vagas"}
    </Button>
  );
}

function ImportButton() {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  const task = useTask(() => setOpen(false));
  const send = useSend<Record<string, string>, { task_id: string }>("POST", "/api/v1/jobs/import");
  return (
    <>
      <Button onClick={() => setOpen(true)}>Importar vaga</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Importar vaga">
        <div className="space-y-3">
          <Field label="URL da vaga" hint="LinkedIn, Greenhouse, Lever, Ashby, Gupy ou qualquer página">
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
          </Field>
          <Field label="…ou cole o texto do anúncio">
            <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          <Field label="Empresa (opcional)">
            <Input value={company} onChange={(e) => setCompany(e.target.value)} />
          </Field>
          {task.progress && <p className="text-sm text-muted-foreground">{task.progress.message} ({task.progress.pct}%)</p>}
          <Button
            disabled={(!url && !text) || task.running || send.isPending}
            onClick={async () => {
              try {
                const r = await send.mutateAsync({ ...(url && { url }), ...(text && { text }), ...(company && { company_name: company }) });
                task.start(r.task_id);
              } catch {} // o erro já aparece no aviso
            }}
          >
            Importar e avaliar
          </Button>
        </div>
      </Modal>
    </>
  );
}

function Companies() {
  const { data } = useGet<Company[]>(["companies"], "/api/v1/companies");
  const [form, setForm] = useState({ name: "", ats_kind: "greenhouse", ats_board_token: "", is_monitored: true, is_watchlisted: false });
  const create = useSend("POST", "/api/v1/companies", { success: "Empresa adicionada", invalidate: [["companies"]] });
  const update = useSend<{ id: string; body: Partial<Company> }>("PUT", (v) => `/api/v1/companies/${v.id}`, { invalidate: [["companies"]] });
  const poll = useSend<string>("POST", (id) => `/api/v1/companies/${id}/poll`, { success: "Coleta agendada" });
  const seed = useSend<void, { added: number }>("POST", "/api/v1/companies/seed-defaults", { invalidate: [["companies"]] });
  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>100 grandes contratantes de tecnologia (48 no Brasil, 52 fora) com site de vagas público verificado. Só entram vagas dos seus cargos-alvo e que dá para fazer do Brasil.</span>
        <Button variant="outline" disabled={seed.isPending} onClick={async () => { try { const r = await seed.mutateAsync(); toast.success(`${r.added} empresas adicionadas`); } catch {} }}>Importar empresas verificadas</Button>
      </Card>
      <Card>
        <div className="grid gap-2 md:grid-cols-5">
          <Input placeholder="Empresa" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Select value={form.ats_kind} onChange={(e) => setForm({ ...form, ats_kind: e.target.value })}>
            <option value="greenhouse">Greenhouse</option>
            <option value="lever">Lever</option>
            <option value="ashby">Ashby</option>
            <option value="gupy">Gupy</option>
          </Select>
          <Input placeholder="token do board (ex.: nubank)" value={form.ats_board_token} onChange={(e) => setForm({ ...form, ats_board_token: e.target.value })} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_watchlisted} onChange={(e) => setForm({ ...form, is_watchlisted: e.target.checked })} />
            Empresa dos sonhos
          </label>
          <Button disabled={!form.name} onClick={() => create.mutate(form)}>Adicionar</Button>
        </div>
      </Card>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr><th className="p-2">Empresa</th><th>ATS</th><th>Monitorar</th><th>Watchlist</th><th>Bloquear</th><th>Última coleta</th><th /></tr>
          </thead>
          <tbody>
            {data?.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="p-2"><span className="flex items-center gap-2.5"><CompanyLogo id={c.id} name={c.name} className="h-8 w-8" /> {c.name}</span></td>
                <td>{c.ats_kind ? `${c.ats_kind}/${c.ats_board_token}` : "—"}</td>
                {(["is_monitored", "is_watchlisted", "is_blocked"] as const).map((k) => (
                  <td key={k}>
                    <input type="checkbox" checked={c[k]} onChange={(e) => update.mutate({ id: c.id, body: { [k]: e.target.checked } })} aria-label={k} />
                  </td>
                ))}
                <td className="text-xs">{formatDateTime(c.last_polled_at)}</td>
                <td>{c.ats_board_token && <Button size="sm" variant="ghost" onClick={() => poll.mutate(c.id)}>Coletar</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const SCHEDULES: Record<string, string> = {
  "0 9,15 * * 1-5": "Dias úteis, 9h e 15h",
  "0 9 * * 1-5": "Dias úteis, 9h",
  "0 */4 * * *": "A cada 4 horas",
  "0 9 * * *": "Todo dia, 9h",
  "0 9 * * 1": "Toda segunda, 9h",
};

function Searches() {
  const { data } = useGet<Search[]>(["searches"], "/api/v1/saved-searches");
  const [form, setForm] = useState({ name: "", query: "", sources: ["monitored", "boards"], schedule_cron: "0 9,15 * * 1-5", work_models: ["remote"] as string[] });
  const create = useSend("POST", "/api/v1/saved-searches", { success: "Busca salva", invalidate: [["searches"]] });
  const run = useSend<string>("POST", (id) => `/api/v1/saved-searches/${id}/run`, { success: "Busca iniciada" });
  const del = useSend<string>("DELETE", (id) => `/api/v1/saved-searches/${id}`, { invalidate: [["searches"]] });
  const toggleSource = (s: string) =>
    setForm({ ...form, sources: form.sources.includes(s) ? form.sources.filter((x) => x !== s) : [...form.sources, s] });
  return (
    <div className="space-y-4">
      <Card className="space-y-2">
        <div className="grid gap-2 md:grid-cols-3">
          <Input placeholder="Nome" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Input placeholder="Termos (use | para OU): data engineer | engenheiro de dados" value={form.query} onChange={(e) => setForm({ ...form, query: e.target.value })} />
          <Select value={form.schedule_cron} onChange={(e) => setForm({ ...form, schedule_cron: e.target.value })} aria-label="Quando rodar">
            {Object.entries(SCHEDULES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted-foreground">Modelo de trabalho:</span>
          {(["remote", "hybrid", "onsite"] as const).map((w) => (
            <Chip
              key={w}
              active={form.work_models.includes(w)}
              onClick={() => setForm({ ...form, work_models: form.work_models.includes(w) ? form.work_models.filter((x) => x !== w) : [...form.work_models, w] })}
            >
              {{ remote: "Remoto", hybrid: "Híbrido", onsite: "Presencial" }[w]}
            </Chip>
          ))}
          <span className="text-xs text-muted-foreground">nenhum marcado = todos</span>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex gap-1"><input type="checkbox" checked={form.sources.includes("monitored")} onChange={() => toggleSource("monitored")} /> Empresas monitoradas</label>
          <label className="flex gap-1"><input type="checkbox" checked={form.sources.includes("boards")} onChange={() => toggleSource("boards")} /> Sites de vagas (Remotive, RemoteOK, Himalayas, Jobicy, Arbeitnow, WWR, Working Nomads)</label>
          <span className="text-xs text-muted-foreground">Vagas do LinkedIn: crie alertas de vaga no LinkedIn (chegam pelo Gmail) ou use “Importar para o tracker” na extensão.</span>
          <Button
            disabled={!form.name || !form.query || create.isPending}
            onClick={() =>
              create.mutate({
                name: form.name,
                query: form.query,
                sources: form.sources,
                schedule_cron: form.schedule_cron,
                filters: form.work_models.length ? { work_models: form.work_models } : {},
              })
            }
          >
            Salvar busca
          </Button>
        </div>
      </Card>
      {data?.map((s) => (
        <Card key={s.id} className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="font-medium">{s.name}</p>
            <p className="text-xs text-muted-foreground">
              “{s.query}” · {s.sources.map((x) => ({ monitored: "empresas monitoradas", boards: "sites de vagas" })[x] ?? x).join(", ")} · {SCHEDULES[s.schedule_cron] ?? s.schedule_cron} · última: {formatDateTime(s.last_run_at)}
            </p>
          </div>
          <div className="flex gap-2">
            {!s.is_active && <Badge>pausada</Badge>}
            <Button size="sm" variant="outline" onClick={() => run.mutate(s.id)}>Rodar agora</Button>
            <Button size="sm" variant="ghost" onClick={() => window.confirm(`Excluir a busca “${s.name}”?`) && del.mutate(s.id)}>Excluir</Button>
          </div>
        </Card>
      ))}
    </div>
  );
}
