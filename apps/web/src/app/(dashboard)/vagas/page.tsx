"use client";

import { CompanyLogo } from "@/components/brand";
import { Ban, Briefcase, Check, ChevronLeft, ChevronRight, Clock3, Eye, Globe2, Plus, Radio, Search, SlidersHorizontal, Sparkles, Star, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
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
type SearchFilters = Record<string, unknown> & {
  country?: string;
  work_models?: string[];
  boards?: string[];
  custom_sources?: { name: string; url: string }[];
};
type Search = { id: string; name: string; query: string; sources: string[]; schedule_cron: string; is_active: boolean; last_run_at: string | null; filters: SearchFilters };
type SearchInput = { name: string; query: string; sources: string[]; schedule_cron: string; filters: Record<string, unknown> };

const DISCOVERY_PAGE_SIZE = 30;

const COMPANY_FLAGS = [
  { key: "is_monitored", label: "Monitorar vagas", icon: Radio, active: "bg-primary" },
  { key: "is_watchlisted", label: "Empresa favorita", icon: Star, active: "bg-amber-500" },
  { key: "is_blocked", label: "Bloquear vagas", icon: Ban, active: "bg-danger" },
] as const;

const SCHEDULE_PRESETS = [
  { cron: "0 9,15 * * 1-5", label: "2 vezes por dia", detail: "Dias úteis, às 9h e 15h" },
  { cron: "0 9 * * 1-5", label: "1 vez por dia", detail: "Dias úteis, às 9h" },
  { cron: "0 9 * * *", label: "Todos os dias", detail: "Diariamente, às 9h" },
  { cron: "0 9 * * 1", label: "1 vez por semana", detail: "Segundas-feiras, às 9h" },
];

function scheduleLabel(cron: string) {
  return SCHEDULE_PRESETS.find((preset) => preset.cron === cron)?.label ?? "Agenda personalizada";
}

export default function JobsPage() {
  return <Suspense fallback={<Skeleton className="h-96" />}><JobsPageContent /></Suspense>;
}

type JobsTab = "vagas" | "empresas" | "buscas" | "buscar";

function JobsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tabParam = searchParams.get("view");
  const tab: JobsTab = tabParam === "buscar" || tabParam === "empresas" || tabParam === "buscas" ? tabParam : "vagas";
  const setTab = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value === "vagas") params.delete("view");
    else params.set("view", value);
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };
  return (
    <div>
      <PageHeader
        icon={<Briefcase size={20} />}
        title="Vagas"
        description="Ordenadas por prioridade (Chance × Atratividade). Filtre por tecnologia, empresa, salário e mais."
        actions={<span data-tour="import"><ImportButton /></span>}
      />
      <div data-tour="tabs" className="inline-block">
        <Tabs value={tab} onChange={setTab} items={[{ value: "vagas", label: "Fila" }, { value: "buscar", label: "Descobrir" }, { value: "empresas", label: "Empresas" }, { value: "buscas", label: "Buscas salvas" }]} />
      </div>
      {tab === "vagas" && (
        <Suspense fallback={<Skeleton className="h-96" />}>
          <JobsList />
        </Suspense>
      )}
      {tab === "empresas" && <Companies />}
      {tab === "buscas" && <Searches />}
      {tab === "buscar" && <Discover />}
    </div>
  );
}

function Discover() {
  const boards = [
    ["remotive", "Remotive"], ["remoteok", "RemoteOK"], ["himalayas", "Himalayas"], ["jobicy", "Jobicy"],
    ["arbeitnow", "Arbeitnow"], ["workingnomads", "Working Nomads"], ["weworkremotely", "We Work Remotely"],
  ];
  const [form, setForm] = useState({
    name: "",
    query: "",
    country: "",
    workModel: "remote",
    sources: ["boards"],
    boards: ["remotive", "remoteok", "himalayas", "jobicy", "arbeitnow", "workingnomads", "weworkremotely"],
    customSources: [] as { name: string; url: string }[],
    schedule_cron: "0 9,15 * * 1-5",
  });
  const [customSourceName, setCustomSourceName] = useState("");
  const [customSourceUrl, setCustomSourceUrl] = useState("");
  const [selectedSearchId, setSelectedSearchId] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [discoveryPage, setDiscoveryPage] = useState(0);
  const queryClient = useQueryClient();
  const [openJob, setOpenJob] = useState<JobRow | null>(null);
  const { data: savedSearches } = useGet<Search[]>(["searches"], "/api/v1/saved-searches");
  useEffect(() => {
    setDiscoveryPage(0);
  }, [form.query, form.country, form.workModel]);
  const create = useSend<SearchInput, Search>("POST", "/api/v1/saved-searches", { success: "Busca salva", invalidate: [["searches"]] });
  const update = useSend<{ id: string; body: SearchInput }, Search>("PUT", (value) => `/api/v1/saved-searches/${value.id}`, {
    success: "Busca atualizada",
    invalidate: [["searches"]],
    requestBody: (value) => value.body,
  });
  const run = useSend<string, { task_id: string }>("POST", (id) => `/api/v1/saved-searches/${id}/run`);
  const selectJobs = useSend<{ job_ids: string[] }, { selected: number }>("POST", "/api/v1/jobs/select", {
    success: "Vagas selecionadas enviadas para a fila",
    invalidate: [["jobs"], ["dashboard"], ["facets"], ["keyword-search-jobs"]],
  });
  const task = useTask(() => {
    void queryClient.invalidateQueries({ queryKey: ["keyword-search-jobs"] });
    void queryClient.invalidateQueries({ queryKey: ["discovery-suggestions"] });
  });
  const jobParams = new URLSearchParams({
    status: "open",
    triage: "discovered",
    sort: "recent",
    limit: String(DISCOVERY_PAGE_SIZE + 1),
    offset: String(discoveryPage * DISCOVERY_PAGE_SIZE),
  });
  if (form.query.trim()) jobParams.set("q", form.query.trim());
  if (form.country.trim()) jobParams.set("location", form.country.trim());
  if (form.workModel) jobParams.set("work_model", form.workModel);
  const matchingJobs = useGet<JobRow[]>(
    ["keyword-search-jobs", jobParams.toString()],
    `/api/v1/jobs?${jobParams}`,
  );
  const pageResults = matchingJobs.data ?? [];
  const visibleJobs = pageResults.slice(0, DISCOVERY_PAGE_SIZE);
  const hasNextPage = pageResults.length > DISCOVERY_PAGE_SIZE;
  const allVisibleSelected = visibleJobs.length > 0 && visibleJobs.every((job) => selectedJobIds.includes(job.id));
  const toggleJobSelection = (jobId: string) => {
    if (!selectedJobIds.includes(jobId) && selectedJobIds.length >= 200) {
      toast.error("Você pode enviar até 200 vagas por vez para a Fila.");
      return;
    }
    setSelectedJobIds((current) => current.includes(jobId) ? current.filter((id) => id !== jobId) : [...current, jobId]);
  };
  const toggleVisibleJobs = () => {
    if (allVisibleSelected) {
      setSelectedJobIds((current) => current.filter((id) => !visibleJobs.some((job) => job.id === id)));
      return;
    }
    const additions = visibleJobs.filter((job) => !selectedJobIds.includes(job.id));
    if (selectedJobIds.length + additions.length > 200) {
      toast.error("Você pode enviar até 200 vagas por vez para a Fila.");
      return;
    }
    setSelectedJobIds((current) => [...new Set([...current, ...additions.map((job) => job.id)])]);
  };
  const submitSelectedJobs = async () => {
    await selectJobs.mutateAsync({ job_ids: selectedJobIds });
    setSelectedJobIds([]);
  };
  const toggle = (items: string[], item: string) =>
    items.includes(item) ? items.filter((value) => value !== item) : [...items, item];
  const addCustomSource = () => {
    const name = customSourceName.trim();
    const url = customSourceUrl.trim();
    if (!name || !url) {
      toast.error("Informe o nome do site e a URL do board de vagas.");
      return;
    }
    setForm({
      ...form,
      customSources: [...form.customSources, { name, url }],
      sources: form.sources.includes("custom") ? form.sources : [...form.sources, "custom"],
    });
    setCustomSourceName("");
    setCustomSourceUrl("");
  };
  const searchNow = async () => {
    const query = form.query.trim();
    const payload: SearchInput = {
      name: form.name.trim() || query,
      query,
      sources: form.sources,
      schedule_cron: form.schedule_cron,
      filters: {
        ...(form.country.trim() && { country: form.country.trim() }),
        ...(form.workModel && { work_models: [form.workModel] }),
        ...(form.sources.includes("boards") && { boards: form.boards }),
        ...(form.customSources.length > 0 && { custom_sources: form.customSources }),
      },
    };
    const existing = savedSearches?.find((search) => search.id === selectedSearchId)
      ?? savedSearches?.find((search) => search.name === payload.name);
    const saved = existing
      ? await update.mutateAsync({ id: existing.id, body: payload })
      : await create.mutateAsync(payload);
    setSelectedSearchId(saved.id);
    const queued = await run.mutateAsync(saved.id);
    task.start(queued.task_id);
  };
  return (
    <div className="space-y-5">
      <Card className="space-y-4">
        <div>
          <h2 className="font-semibold">O que você está procurando?</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Digite palavras-chave para filtrar as sugestões existentes ou buscar novas vagas nas fontes escolhidas.
          </p>
        </div>
        <Field label="Carregar busca salva" hint="Edite os critérios e execute novamente.">
          <Select
            value={selectedSearchId}
            onChange={(e) => {
              const selected = savedSearches?.find((search) => search.id === e.target.value);
              setSelectedSearchId(e.target.value);
              if (!selected) return;
              setForm({
                name: selected.name,
                query: selected.query,
                country: selected.filters.country ?? "",
                workModel: selected.filters.work_models?.[0] ?? "",
                sources: selected.sources.filter((source) => source !== "linkedin").length
                  ? selected.sources.filter((source) => source !== "linkedin")
                  : ["boards"],
                boards: selected.filters.boards ?? [
                  "remotive", "remoteok", "himalayas", "jobicy", "arbeitnow", "workingnomads", "weworkremotely",
                ],
                customSources: selected.filters.custom_sources ?? [],
                schedule_cron: selected.schedule_cron,
              });
              setCustomSourceName("");
              setCustomSourceUrl("");
            }}
          >
            <option value="">Nova busca</option>
            {savedSearches?.map((search) => <option key={search.id} value={search.id}>{search.name}</option>)}
          </Select>
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Palavras-chave"><Input placeholder="Ex.: desenvolvedor backend Go" value={form.query} onChange={(e) => setForm({ ...form, query: e.target.value })} /></Field>
          <Field label="País ou localização"><Input placeholder="United States" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} /></Field>
          <Field label="Modalidade">
            <Select value={form.workModel} onChange={(e) => setForm({ ...form, workModel: e.target.value })}>
              <option value="">Qualquer modalidade</option><option value="remote">Remoto</option><option value="hybrid">Híbrido</option><option value="onsite">Presencial</option>
            </Select>
          </Field>
        </div>
        <details className="group overflow-hidden rounded-2xl border bg-card shadow-[var(--shadow-soft)]">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-4 transition-colors hover:bg-muted/40 [&::-webkit-details-marker]:hidden">
            <span className="flex min-w-0 items-center gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Sparkles size={18} /></span>
              <span className="min-w-0">
                <span className="block font-semibold">Fontes e recorrência</span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                  Escolha onde buscar e quando atualizar os resultados automaticamente.
                </span>
              </span>
            </span>
            <span className="hidden shrink-0 items-center gap-2 sm:flex">
              <Badge tone="primary">{form.sources.length} {form.sources.length === 1 ? "fonte" : "fontes"}</Badge>
              <Badge>{scheduleLabel(form.schedule_cron)}</Badge>
            </span>
          </summary>
          <div className="space-y-6 border-t p-4 md:p-5">
            <section className="space-y-3" aria-labelledby="search-sources-title">
              <div>
                <h3 id="search-sources-title" className="text-sm font-semibold">Onde procurar</h3>
                <p className="mt-1 text-xs text-muted-foreground">Selecione uma ou mais fontes para esta busca.</p>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                {[
                  { id: "boards", title: "Sites públicos", detail: "Agregadores de vagas remotas", icon: Globe2 },
                  { id: "custom", title: "Meus sites", detail: "Boards de empresas escolhidos por você", icon: Plus },
                ].map(({ id, title, detail, icon: Icon }) => {
                  const selected = form.sources.includes(id);
                  return (
                    <label
                      key={id}
                      className={`relative flex cursor-pointer items-start gap-3 rounded-xl border p-3.5 transition-all ${
                        selected ? "border-primary/50 bg-primary/5 shadow-sm" : "hover:border-primary/30 hover:bg-muted/30"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="peer sr-only"
                        checked={selected}
                        onChange={() => setForm({ ...form, sources: toggle(form.sources, id) })}
                      />
                      <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${selected ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
                        <Icon size={17} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{title}</span>
                        <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{detail}</span>
                      </span>
                      <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border ${selected ? "border-primary bg-primary text-primary-foreground" : "border-border text-transparent"} peer-focus-visible:ring-2 peer-focus-visible:ring-primary/40`}>
                        <Check size={13} strokeWidth={3} />
                      </span>
                    </label>
                  );
                })}
              </div>
              {form.sources.includes("boards") && (
                <div className="rounded-xl border bg-muted/20 p-3.5">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium">Agregadores públicos</p>
                    <span className="text-xs text-muted-foreground">{form.boards.length} de {boards.length} selecionados</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {boards.map(([id, label]) => {
                      const selected = form.boards.includes(id);
                      return (
                        <label
                          key={id}
                          className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                            selected ? "border-primary/40 bg-primary/10 text-primary" : "bg-card text-muted-foreground hover:border-primary/30 hover:text-foreground"
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={selected}
                            onChange={() => setForm({ ...form, boards: toggle(form.boards, id) })}
                          />
                          <span className={`grid h-4 w-4 place-items-center rounded border ${selected ? "border-primary bg-primary text-primary-foreground" : "border-border text-transparent"}`}>
                            <Check size={11} strokeWidth={3} />
                          </span>
                          {label}
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              {form.sources.includes("custom") && (
                <div className="space-y-3 rounded-xl border p-3.5">
                  <div>
                    <p className="text-sm font-medium">Seus boards de vagas</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                      Adicione um board público Greenhouse, Lever, Ashby ou Gupy. Sites genéricos ainda não são suportados.
                    </p>
                  </div>
                  <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]">
                    <Input aria-label="Nome do site ou empresa" placeholder="Empresa ou site" value={customSourceName} onChange={(e) => setCustomSourceName(e.target.value)} />
                    <Input aria-label="URL pública do board" type="url" placeholder="https://jobs.lever.co/empresa" value={customSourceUrl} onChange={(e) => setCustomSourceUrl(e.target.value)} />
                    <Button type="button" variant="outline" onClick={addCustomSource}><Plus size={15} /> Adicionar</Button>
                  </div>
                  {form.customSources.length > 0 && (
                    <ul className="divide-y rounded-lg border">
                      {form.customSources.map((source, index) => (
                        <li key={`${source.url}-${index}`} className="flex min-w-0 items-center gap-3 px-3 py-2.5">
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Globe2 size={15} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">{source.name}</span>
                            <span className="block truncate text-xs text-muted-foreground">{source.url}</span>
                          </span>
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            aria-label={`Remover fonte ${source.name}`}
                            onClick={() => {
                              const customSources = form.customSources.filter((_, i) => i !== index);
                              setForm({
                                ...form,
                                customSources,
                                sources: customSources.length ? form.sources : form.sources.filter((source) => source !== "custom"),
                              });
                            }}
                          >
                            <X size={15} />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </section>

            <section className="space-y-3 border-t pt-5" aria-labelledby="search-schedule-title">
              <div className="flex items-start gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent text-primary"><Clock3 size={17} /></span>
                <div>
                  <h3 id="search-schedule-title" className="text-sm font-semibold">Quando repetir</h3>
                  <p className="mt-1 text-xs text-muted-foreground">A busca é executada automaticamente enquanto estiver ativa.</p>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
                <Field label="Frequência">
                  <Select
                    value={SCHEDULE_PRESETS.some((preset) => preset.cron === form.schedule_cron) ? form.schedule_cron : "custom"}
                    onChange={(e) => {
                      const preset = SCHEDULE_PRESETS.find((option) => option.cron === e.target.value);
                      if (preset) setForm({ ...form, schedule_cron: preset.cron });
                    }}
                  >
                    {SCHEDULE_PRESETS.map((preset) => <option key={preset.cron} value={preset.cron}>{preset.label} · {preset.detail}</option>)}
                    <option value="custom">Personalizada</option>
                  </Select>
                </Field>
                <div className="flex items-end">
                  {SCHEDULE_PRESETS.find((preset) => preset.cron === form.schedule_cron) ? (
                    <p className="mb-2 text-xs text-muted-foreground">
                      {SCHEDULE_PRESETS.find((preset) => preset.cron === form.schedule_cron)?.detail}. Horários no fuso configurado para sua conta.
                    </p>
                  ) : (
                    <Field label="Expressão cron" hint="5 campos: minuto, hora, dia, mês e dia da semana.">
                      <Input aria-label="Expressão cron personalizada" value={form.schedule_cron} onChange={(e) => setForm({ ...form, schedule_cron: e.target.value })} placeholder="0 9,15 * * 1-5" />
                    </Field>
                  )}
                </div>
              </div>
              <Field label="Nome da busca" hint="Opcional. Se ficar vazio, usamos as palavras-chave.">
                <Input placeholder="Ex.: Backend remoto" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </Field>
            </section>
          </div>
        </details>
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={!form.query.trim() || !form.sources.length || (form.sources.includes("boards") && !form.boards.length) || (form.sources.includes("custom") && !form.customSources.length) || task.running || create.isPending || update.isPending || run.isPending} onClick={() => void searchNow()}>
            {task.running ? "Buscando vagas…" : "Buscar vagas agora"}
          </Button>
          {task.progress && <span className="text-sm text-muted-foreground">{task.progress.message} ({task.progress.pct}%)</span>}
        </div>
      </Card>
      <Card className="space-y-3 p-4">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-primary"><Globe2 size={16} /></span>
          <div>
            <p className="text-sm font-semibold">Execução da busca</p>
            <p className="text-xs text-muted-foreground">As vagas entram como sugestões. Nenhuma candidatura é enviada automaticamente.</p>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {form.sources.includes("boards") && (
            <div className="flex items-start gap-2 rounded-xl border p-3 text-xs">
              <Globe2 size={14} className="mt-0.5 shrink-0 text-primary" />
              <div><p className="font-medium">Sites públicos</p><p className="mt-0.5 text-muted-foreground">Consultados pelo servidor assim que você inicia a busca.</p></div>
            </div>
          )}
        </div>
      </Card>
      <div>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">{form.query.trim() ? `Vagas para “${form.query.trim()}”` : "Vagas para revisar"}</h2>
            <p className="mt-1 text-xs text-muted-foreground">As vagas coletadas ficam aqui. Só as que você selecionar entram na Fila.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {visibleJobs.length ? `Mostrando ${discoveryPage * DISCOVERY_PAGE_SIZE + 1}–${discoveryPage * DISCOVERY_PAGE_SIZE + visibleJobs.length}` : "0 nesta página"}
              {" · "}{selectedJobIds.length} selecionada(s)
            </span>
            {visibleJobs.length > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={toggleVisibleJobs}
              >
                {allVisibleSelected ? "Desmarcar esta página" : "Selecionar esta página"}
              </Button>
            )}
            <Button size="sm" disabled={!selectedJobIds.length || selectJobs.isPending} onClick={() => void submitSelectedJobs()}>
              <Check size={15} /> Enviar selecionadas para a Fila
            </Button>
          </div>
        </div>
        {visibleJobs.length ? (
          <Stagger className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,360px),1fr))] gap-3">
            {visibleJobs.map((job) => (
              <StaggerItem key={job.id}>
                <JobCard
                  job={job}
                  onOpen={setOpenJob}
                  selected={selectedJobIds.includes(job.id)}
                  onSelect={() => toggleJobSelection(job.id)}
                />
              </StaggerItem>
            ))}
          </Stagger>
        ) : <EmptyState icon={<Search size={22} />} title={form.query.trim() ? "Nenhuma vaga para revisar" : "Nenhuma vaga aguardando seleção"}>{form.query.trim() ? "Clique em “Buscar vagas agora” para consultar as fontes selecionadas. Os resultados ficam aqui para você escolher quais vão para a Fila." : "Inicie uma busca para encontrar vagas. Elas só entram na Fila quando você as selecionar aqui."}</EmptyState>}
        {(discoveryPage > 0 || hasNextPage) && (
          <nav aria-label="Paginação das vagas para revisar" className="mt-4 flex items-center justify-center gap-3">
            <Button
              variant="outline"
              size="sm"
              aria-label="Página anterior"
              disabled={discoveryPage === 0 || matchingJobs.isFetching}
              onClick={() => setDiscoveryPage((current) => Math.max(0, current - 1))}
            >
              <ChevronLeft size={15} /> Anterior
            </Button>
            <span className="text-sm text-muted-foreground">Página {discoveryPage + 1}</span>
            <Button
              variant="outline"
              size="sm"
              aria-label="Próxima página"
              disabled={!hasNextPage || matchingJobs.isFetching}
              onClick={() => setDiscoveryPage((current) => current + 1)}
            >
              Próxima <ChevronRight size={15} />
            </Button>
          </nav>
        )}
      </div>
      <JobDrawer job={openJob} onClose={() => setOpenJob(null)} />
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
  const activeView = sp.get("view");
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
  }, [activeView, debounced, pathname, router]);
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
                <Button size="sm" disabled={isLoading} onClick={() => setDrawer(false)}>
                  {isLoading ? "Carregando…" : `Ver ${jobs.length}${jobs.length === limit ? "+" : ""} resultados`}
                </Button>
              </div>
              {panel}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
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
  const { data, isLoading } = useGet<Company[]>(["companies"], "/api/v1/companies");
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyStatus, setCompanyStatus] = useState("all");
  const [companyAts, setCompanyAts] = useState("all");
  const [companySort, setCompanySort] = useState("name");
  const [form, setForm] = useState({ name: "", ats_kind: "greenhouse", ats_board_token: "", is_monitored: true, is_watchlisted: false });
  const create = useSend("POST", "/api/v1/companies", { success: "Empresa adicionada", invalidate: [["companies"]] });
  const update = useSend<{ id: string; body: Partial<Company> }>("PUT", (v) => `/api/v1/companies/${v.id}`, {
    invalidate: [["companies"]],
    requestBody: (value) => value.body,
  });
  const poll = useSend<string>("POST", (id) => `/api/v1/companies/${id}/poll`, { success: "Coleta agendada" });
  const seed = useSend<void, { added: number }>("POST", "/api/v1/companies/seed-defaults", { invalidate: [["companies"]] });
  const companies = data ?? [];
  const normalizedQuery = companyQuery.trim().toLocaleLowerCase();
  const counts = {
    all: companies.length,
    monitored: companies.filter((company) => company.is_monitored).length,
    watchlisted: companies.filter((company) => company.is_watchlisted).length,
    blocked: companies.filter((company) => company.is_blocked).length,
    unmonitored: companies.filter((company) => !company.is_monitored).length,
  };
  const atsOptions = [...new Set(companies.map((company) => company.ats_kind).filter((kind): kind is string => Boolean(kind)))].sort();
  const filteredCompanies = companies
    .filter((company) => {
      const matchesQuery = !normalizedQuery
        || `${company.name} ${company.ats_kind ?? ""} ${company.ats_board_token ?? ""}`.toLocaleLowerCase().includes(normalizedQuery);
      const matchesStatus = companyStatus === "all"
        || (companyStatus === "monitored" && company.is_monitored)
        || (companyStatus === "watchlisted" && company.is_watchlisted)
        || (companyStatus === "blocked" && company.is_blocked)
        || (companyStatus === "unmonitored" && !company.is_monitored);
      return matchesQuery && matchesStatus && (companyAts === "all" || company.ats_kind === companyAts);
    })
    .sort((a, b) => {
      if (companySort === "recent") {
        return (b.last_polled_at ? Date.parse(b.last_polled_at) : 0) - (a.last_polled_at ? Date.parse(a.last_polled_at) : 0)
          || a.name.localeCompare(b.name);
      }
      return a.name.localeCompare(b.name);
    });
  const hasCompanyFilters = Boolean(normalizedQuery || companyStatus !== "all" || companyAts !== "all" || companySort !== "name");
  const clearCompanyFilters = () => {
    setCompanyQuery("");
    setCompanyStatus("all");
    setCompanyAts("all");
    setCompanySort("name");
  };
  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>100 grandes contratantes de tecnologia (48 no Brasil, 52 fora) com site de vagas público verificado. Só entram vagas dos seus cargos-alvo e que dá para fazer do Brasil.</span>
        <Button variant="outline" disabled={seed.isPending} onClick={async () => { try { const r = await seed.mutateAsync(); toast.success(`${r.added} empresas adicionadas`); } catch {} }}>Importar empresas verificadas</Button>
      </Card>
      <Card className="space-y-4 p-4">
        <div>
          <h2 className="text-sm font-semibold">Adicionar empresa</h2>
          <p className="mt-1 text-xs text-muted-foreground">Conecte o board público da empresa para monitorar novas vagas.</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-[1.1fr_0.9fr_1.3fr_1fr_auto]">
          <Input aria-label="Nome da empresa" placeholder="Empresa" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Select value={form.ats_kind} onChange={(e) => setForm({ ...form, ats_kind: e.target.value })}>
            <option value="greenhouse">Greenhouse</option>
            <option value="lever">Lever</option>
            <option value="ashby">Ashby</option>
            <option value="gupy">Gupy</option>
          </Select>
          <Input aria-label="Identificador do board" placeholder="Token do board (ex.: nubank)" value={form.ats_board_token} onChange={(e) => setForm({ ...form, ats_board_token: e.target.value })} />
          <label className={`flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition-colors ${form.is_watchlisted ? "border-amber-500/40 bg-amber-500/10 text-amber-600" : "text-muted-foreground hover:bg-muted/40"}`}>
            <input
              type="checkbox"
              className="sr-only"
              checked={form.is_watchlisted}
              onChange={(e) => setForm({ ...form, is_watchlisted: e.target.checked })}
            />
            <Star size={16} className={form.is_watchlisted ? "fill-amber-500 text-amber-500" : ""} />
            Empresa favorita
            <span className={`ml-auto grid h-5 w-5 place-items-center rounded-md border ${form.is_watchlisted ? "border-amber-500 bg-amber-500 text-white" : "border-border"}`}>
              {form.is_watchlisted && <Check size={13} strokeWidth={3} />}
            </span>
          </label>
          <Button disabled={!form.name.trim() || create.isPending} onClick={() => create.mutate(form)}>
            <Plus size={15} /> Adicionar
          </Button>
        </div>
      </Card>
      <Card className="space-y-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative min-w-56 flex-1 sm:max-w-sm">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Buscar empresas"
              placeholder="Buscar empresa ou ATS"
              value={companyQuery}
              onChange={(e) => setCompanyQuery(e.target.value)}
              className="pl-9 pr-9"
            />
            {companyQuery && (
              <button
                type="button"
                aria-label="Limpar busca"
                onClick={() => setCompanyQuery("")}
                className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X size={14} />
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Filtrar por ATS" value={companyAts} onChange={(e) => setCompanyAts(e.target.value)} className="w-40">
              <option value="all">Todos os ATS</option>
              {atsOptions.map((ats) => <option key={ats} value={ats}>{ats}</option>)}
            </Select>
            <Select aria-label="Ordenar empresas" value={companySort} onChange={(e) => setCompanySort(e.target.value)} className="w-44">
              <option value="name">Nome (A–Z)</option>
              <option value="recent">Coleta mais recente</option>
            </Select>
            {hasCompanyFilters && (
              <Button type="button" size="sm" variant="ghost" onClick={clearCompanyFilters}>Limpar filtros</Button>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="Filtrar empresas por status">
          <Chip active={companyStatus === "all"} count={counts.all} onClick={() => setCompanyStatus("all")}>Todas</Chip>
          <Chip active={companyStatus === "monitored"} count={counts.monitored} onClick={() => setCompanyStatus("monitored")}><Radio size={13} /> Monitoradas</Chip>
          <Chip active={companyStatus === "watchlisted"} count={counts.watchlisted} onClick={() => setCompanyStatus("watchlisted")}><Star size={13} /> Favoritas</Chip>
          <Chip active={companyStatus === "blocked"} count={counts.blocked} onClick={() => setCompanyStatus("blocked")}><Ban size={13} /> Bloqueadas</Chip>
          <Chip active={companyStatus === "unmonitored"} count={counts.unmonitored} onClick={() => setCompanyStatus("unmonitored")}>Não monitoradas</Chip>
        </div>
        <div className="flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
          <span aria-live="polite">
            {isLoading ? "Carregando empresas…" : (
              <><strong className="font-semibold text-foreground">{filteredCompanies.length}</strong> de {companies.length} empresas</>
            )}
          </span>
          <span className="hidden sm:inline">As alterações nos toggles são salvas automaticamente.</span>
        </div>
        {isLoading ? (
          <Skeleton className="h-64" />
        ) : filteredCompanies.length === 0 ? (
          <EmptyState
            icon={<Search size={22} />}
            title={companies.length ? "Nenhuma empresa encontrada" : "Sua lista de empresas está vazia"}
            action={hasCompanyFilters ? <Button variant="outline" onClick={clearCompanyFilters}>Limpar filtros</Button> : undefined}
          >
            {companies.length ? "Experimente mudar o texto, o ATS ou o status selecionado." : "Adicione uma empresa ou importe a lista de boards verificados para começar."}
          </EmptyState>
        ) : (
          <div className="max-h-[70vh] overflow-auto rounded-lg border">
            <table className="w-full min-w-[800px] text-sm">
              <thead className="sticky top-0 z-10 bg-muted/95 text-left text-xs text-muted-foreground backdrop-blur">
                <tr>
                  <th className="p-3">Empresa</th>
                  <th className="p-3">ATS</th>
                  {COMPANY_FLAGS.map(({ key, label, icon: Icon }) => (
                    <th key={key} className="p-3 text-center font-medium">
                      <span className="inline-flex items-center gap-1.5"><Icon size={13} />{label}</span>
                    </th>
                  ))}
                  <th className="p-3">Última coleta</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {filteredCompanies.map((c) => (
                  <tr key={c.id} className="border-t transition-colors hover:bg-muted/30">
                    <td className="p-3"><span className="flex items-center gap-2.5"><CompanyLogo id={c.id} name={c.name} className="h-8 w-8" /> <span className="font-medium">{c.name}</span></span></td>
                    <td className="p-3"><span className="rounded-md bg-muted px-2 py-1 font-mono text-xs">{c.ats_kind ?? "—"}</span></td>
                    {COMPANY_FLAGS.map(({ key, label, active }) => (
                      <td key={key} className="p-3 text-center">
                        <button
                          type="button"
                          role="switch"
                          aria-checked={c[key]}
                          aria-label={`${c.name}: ${label}`}
                          title={`${label}: ${c[key] ? "ativado" : "desativado"}`}
                          disabled={update.isPending}
                          onClick={() => update.mutate({ id: c.id, body: { [key]: !c[key] } })}
                          className="group inline-flex min-h-9 min-w-12 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary/50 disabled:cursor-wait disabled:opacity-60"
                        >
                          <span className={`relative h-6 w-11 rounded-full p-0.5 transition-colors ${c[key] ? active : "bg-muted-foreground/25 group-hover:bg-muted-foreground/40"}`}>
                            <span className={`block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${c[key] ? "translate-x-5" : "translate-x-0"}`} />
                          </span>
                        </button>
                      </td>
                    ))}
                    <td className="whitespace-nowrap p-3 text-xs text-muted-foreground">{formatDateTime(c.last_polled_at)}</td>
                    <td className="p-3">{c.ats_board_token && <Button size="sm" variant="ghost" onClick={() => poll.mutate(c.id)}><Eye size={14} /> Coletar</Button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
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
          <span className="text-xs text-muted-foreground">Vagas do LinkedIn: crie alertas de vaga no LinkedIn e conecte o Gmail para identificá-los na sua caixa de entrada.</span>
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
            <Plus size={15} /> Salvar busca
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
