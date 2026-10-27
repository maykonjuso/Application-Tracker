"use client";

import { UserRound } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { TagInput } from "@/components/setup-guides";
import { Badge, Button, Card, CardTitle, EmptyState, Field, Input, PageHeader, Select, Tabs, Textarea } from "@/components/ui";
import { rawFetch } from "@/lib/api";
import { useGet, useSend, useTask } from "@/lib/hooks";
import { formatDateTime } from "@/lib/utils";

type Master = { version: number; data: Record<string, unknown>; pending_confirmations: { experience_id: string; items: string[] }[]; total_years: number };
type Version = { version: number; source: string; change_note: string | null; is_current: boolean; created_at: string };
type Answer = { id: string; canonical_question: string; field_kind: string; answer: { value: unknown }; language: string; sensitive: boolean; use_count: number; score: number };
type Block = { id: string; kind: string; value: string; reason: string | null };
type Star = { id: string; title: string; experience_id: string | null; situation: string; task: string; action: string; result: string; competencies: string[] };
type Doc = { id: string; kind: string; title: string; created_at: string; expires_at: string | null };
type Ref = { id: string; name: string; relation: string; contact: string; consent: boolean };
type Repo = { name: string; description: string | null; language: string | null; stars: number; url: string; topics: string[] };

const TABS = [
  { value: "mestre", label: "Currículo mestre" },
  { value: "preferencias", label: "Preferências" },
  { value: "respostas", label: "Banco de respostas" },
  { value: "star", label: "Histórias STAR" },
  { value: "bloqueios", label: "Bloqueios" },
  { value: "documentos", label: "Documentos" },
  { value: "referencias", label: "Referências" },
  { value: "github", label: "GitHub" },
] as const;
type Tab = (typeof TABS)[number]["value"];

export default function ProfilePage() {
  const [tab, setTab] = useState<Tab>("mestre");
  return (
    <div>
      <PageHeader icon={<UserRound size={20} />} title="Perfil" description="A fonte única de verdade: tudo o que é gerado sai daqui" />
      <Tabs value={tab} onChange={setTab} items={[...TABS]} />
      {tab === "mestre" && <MasterTab />}
      {tab === "preferencias" && <PrefsTab />}
      {tab === "respostas" && <AnswersTab />}
      {tab === "star" && <StarTab />}
      {tab === "bloqueios" && <BlocksTab />}
      {tab === "documentos" && <DocsTab />}
      {tab === "referencias" && <RefsTab />}
      {tab === "github" && <GithubTab />}
    </div>
  );
}

function MasterTab() {
  const qc = useQueryClient();
  const master = useGet<Master>(["master"], "/api/v1/profile/master");
  const versions = useGet<Version[]>(["master", "versions"], "/api/v1/profile/master/versions");
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [diff, setDiff] = useState<{ path: string; before: unknown; after: unknown }[] | null>(null);
  const [imported, setImported] = useState<{ id: string; profile: Record<string, unknown>; low: string[] } | null>(null);
  useEffect(() => { if (master.data) setText(JSON.stringify(master.data.data, null, 2)); }, [master.data]);
  const save = useSend<{ data: unknown; change_note: string }>("PUT", "/api/v1/profile/master", { success: "Nova versão salva", invalidate: [["master"]] });
  const translate = useSend<void, { task_id: string }>("POST", "/api/v1/profile/master/translate?target=pt");
  const translateTask = useTask(() => qc.invalidateQueries({ queryKey: ["master"] }));
  const importTask = useTask(async () => {
    if (!imported) return;
    const r = await (await rawFetch(`/api/v1/profile/import/${imported.id}`)).json();
    setImported({ id: imported.id, profile: r.extracted.profile, low: r.extracted.low_confidence });
  });
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await (await rawFetch("/api/v1/profile/import", { method: "POST", body: fd })).json();
    setImported({ id: r.id, profile: {}, low: [] });
    if (r.task_id) importTask.start(r.task_id);
  };
  const confirmImport = async () => {
    if (!imported) return;
    await rawFetch(`/api/v1/profile/import/${imported.id}/confirm`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ data: imported.profile }),
    });
    toast.success("Importação aplicada como nova versão");
    setImported(null);
    qc.invalidateQueries({ queryKey: ["master"] });
  };

  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <div className="space-y-3 xl:col-span-2">
        {master.isError && <EmptyState title="Nenhum currículo mestre ainda">Importe um PDF/DOCX/YAML ao lado ou rode <code>tracker-seed</code>.</EmptyState>}
        {master.data && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="primary">versão {master.data.version}</Badge>
              <span className="text-muted-foreground">{master.data.total_years} anos de experiência pelas datas</span>
              {master.data.pending_confirmations.map((p) => (
                <Badge key={p.experience_id} tone="warning">confirmar {p.experience_id}</Badge>
              ))}
            </div>
            <Textarea rows={28} className="font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
            <div className="flex gap-2">
              <Input placeholder="O que mudou?" value={note} onChange={(e) => setNote(e.target.value)} />
              <Button onClick={() => {
                try { save.mutate({ data: JSON.parse(text), change_note: note }); } catch { toast.error("JSON inválido"); }
              }}>Salvar nova versão</Button>
            </div>
          </>
        )}
      </div>
      <div className="space-y-4">
        <Card className="space-y-2">
          <CardTitle>Importar CV ou perfil</CardTitle>
          <p className="text-xs text-muted-foreground">PDF do LinkedIn, CV em PDF/DOCX ou YAML do mestre. Nada é salvo sem sua confirmação.</p>
          <input type="file" accept=".pdf,.docx,.yaml,.yml" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          {importTask.progress && <p className="text-xs">{importTask.progress.message}</p>}
          {imported && Object.keys(imported.profile).length > 0 && (
            <div className="space-y-2 text-xs">
              <p>Extraído. Campos com baixa confiança: {imported.low.join(", ") || "nenhum"}</p>
              <Button size="sm" onClick={confirmImport}>Aplicar como nova versão</Button>
            </div>
          )}
        </Card>
        <Card className="space-y-2">
          <CardTitle>Tradução PT ↔ EN</CardTitle>
          <Button size="sm" variant="outline" disabled={translateTask.running} onClick={async () => translateTask.start((await translate.mutateAsync()).task_id)}>
            {translateTask.running ? "Traduzindo…" : "Gerar bullets em português"}
          </Button>
        </Card>
        <Card>
          <CardTitle>Versões</CardTitle>
          <ul className="space-y-1 text-xs">
            {versions.data?.map((v) => (
              <li key={v.version} className="flex items-center justify-between">
                <span>v{v.version} · {v.source} · {formatDateTime(v.created_at)}{v.change_note ? ` · ${v.change_note}` : ""}</span>
                {!v.is_current && master.data && (
                  <button className="text-primary underline" onClick={async () => setDiff(await (await rawFetch(`/api/v1/profile/master/diff?from_=${v.version}&to=${master.data!.version}`)).json())}>diff</button>
                )}
              </li>
            ))}
          </ul>
          {diff && (
            <div className="mt-2 max-h-64 overflow-auto text-xs">
              {diff.map((d) => <p key={d.path}><b>{d.path}</b>: {JSON.stringify(d.before)} → {JSON.stringify(d.after)}</p>)}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function PrefsTab() {
  const { data } = useGet<Record<string, unknown>>(["prefs"], "/api/v1/profile/preferences");
  const [p, setP] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { if (data) setP(data); }, [data]);
  const save = useSend("PUT", "/api/v1/profile/preferences", { success: "Preferências salvas", invalidate: [["prefs"]] });
  if (!p) return null;
  const list = (k: string) => (p[k] as string[]) ?? [];
  const setList = (k: string, v: string[]) => setP({ ...p, [k]: v });
  const money = (k: "salary_expectation" | "min_salary", regime: string) => ((p[k] as Record<string, Record<string, number>>)?.[regime]?.BRL ?? "") as number | "";
  const setMoney = (k: "salary_expectation" | "min_salary", regime: string, v: string) => {
    const cur = { ...((p[k] as Record<string, Record<string, number>>) ?? {}) };
    if (v.trim() === "") {
      // campo vazio = sem valor (não "R$ 0", que eliminaria todas as vagas no mínimo)
      const others = { ...(cur[regime] ?? {}) };
      delete others.BRL;
      if (Object.keys(others).length) cur[regime] = others;
      else delete cur[regime];
    } else cur[regime] = { ...(cur[regime] ?? {}), BRL: Number(v) };
    setP({ ...p, [k]: cur });
  };
  return (
    <Card className="grid gap-3 md:grid-cols-2">
      <Field label="Cargos-alvo"><TagInput value={list("target_roles")} onChange={(v) => setList("target_roles", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Stacks"><TagInput value={list("stacks")} onChange={(v) => setList("stacks", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Modelos de trabalho (remote, hybrid, onsite)"><TagInput value={list("work_models")} onChange={(v) => setList("work_models", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Cidades aceitas"><TagInput value={list("locations")} onChange={(v) => setList("locations", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Regimes (CLT, PJ)"><TagInput value={list("regimes")} onChange={(v) => setList("regimes", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Senioridades (junior, mid, senior)"><TagInput value={list("seniorities")} onChange={(v) => setList("seniorities", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Pretensão CLT (R$/mês)" hint="usada nos formulários; nunca gerada pela IA"><Input type="number" value={money("salary_expectation", "CLT")} onChange={(e) => setMoney("salary_expectation", "CLT", e.target.value)} /></Field>
      <Field label="Pretensão PJ (R$/mês)"><Input type="number" value={money("salary_expectation", "PJ")} onChange={(e) => setMoney("salary_expectation", "PJ", e.target.value)} /></Field>
      <Field label="Mínimo CLT (R$/mês)" hint="abaixo disso a vaga é eliminada"><Input type="number" value={money("min_salary", "CLT")} onChange={(e) => setMoney("min_salary", "CLT", e.target.value)} /></Field>
      <Field label="Mínimo PJ (R$/mês)"><Input type="number" value={money("min_salary", "PJ")} onChange={(e) => setMoney("min_salary", "PJ", e.target.value)} /></Field>
      <Field label="Setores preferidos"><TagInput value={list("sectors_preferred")} onChange={(v) => setList("sectors_preferred", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Setores evitados"><TagInput value={list("sectors_avoided")} onChange={(v) => setList("sectors_avoided", v)} placeholder="Digite e tecle Enter" /></Field>
      <Field label="Data de nascimento" hint="só para formulários que pedem; nunca vai para a IA">
        <Input placeholder="dd/mm/aaaa" value={String(p.birth_date ?? "")} onChange={(e) => setP({ ...p, birth_date: e.target.value || null })} />
      </Field>
      <Field label="Cidade e estado de nascimento" hint="ex.: Brasília, DF">
        <Input placeholder="Cidade, UF" value={String(p.birthplace ?? "")} onChange={(e) => setP({ ...p, birthplace: e.target.value || null })} />
      </Field>
      <Field label="Identidade de gênero" hint="autodeclaração: só para formulários que perguntam; nunca vai para a IA nem para o CV">
        <Input placeholder="ex.: Homem cisgênero" value={String(p.gender_identity ?? "")} onChange={(e) => setP({ ...p, gender_identity: e.target.value || null })} />
      </Field>
      <Field label="Orientação sexual">
        <Input placeholder="ex.: Bissexual" value={String(p.sexual_orientation ?? "")} onChange={(e) => setP({ ...p, sexual_orientation: e.target.value || null })} />
      </Field>
      <Field label="Cor/raça (IBGE)">
        <Input placeholder="ex.: Parda" value={String(p.race ?? "")} onChange={(e) => setP({ ...p, race: e.target.value || null })} />
      </Field>
      <Field label="Faz parte da comunidade LGBTQIAP+?">
        <Select value={p.lgbtqia == null ? "" : p.lgbtqia ? "sim" : "nao"} onChange={(e) => setP({ ...p, lgbtqia: e.target.value === "" ? null : e.target.value === "sim" })}>
          <option value="">Não responder (fica com você)</option>
          <option value="sim">Sim</option>
          <option value="nao">Não</option>
        </Select>
      </Field>
      <Field label="Pessoa com deficiência (PcD)?">
        <Select value={p.disability == null ? "" : p.disability ? "sim" : "nao"} onChange={(e) => setP({ ...p, disability: e.target.value === "" ? null : e.target.value === "sim" })}>
          <option value="">Não responder (fica com você)</option>
          <option value="sim">Sim</option>
          <option value="nao">Não</option>
        </Select>
      </Field>
      <Field label="Aviso prévio (dias)"><Input type="number" value={String(p.notice_period_days ?? 30)} onChange={(e) => setP({ ...p, notice_period_days: Number(e.target.value) })} /></Field>
      <div className="flex flex-col gap-2 text-sm">
        <label className="flex gap-2"><input type="checkbox" checked={!!p.relocation} onChange={(e) => setP({ ...p, relocation: e.target.checked })} /> Aceito mudar de cidade</label>
        <label className="flex gap-2"><input type="checkbox" checked={!!p.international} onChange={(e) => setP({ ...p, international: e.target.checked })} /> Vagas internacionais</label>
      </div>
      <div className="md:col-span-2"><Button onClick={() => save.mutate(p)}>Salvar preferências</Button></div>
    </Card>
  );
}

function AnswersTab() {
  const [q, setQ] = useState("");
  const { data } = useGet<Answer[]>(["answers", q], `/api/v1/answer-bank${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  const [f, setF] = useState({ canonical_question: "", field_kind: "text", value: "", language: "any" });
  const create = useSend("POST", "/api/v1/answer-bank", { success: "Resposta salva", invalidate: [["answers"]] });
  const del = useSend<string>("DELETE", (id) => `/api/v1/answer-bank/${id}`, { invalidate: [["answers"]] });
  return (
    <div className="space-y-3">
      <Card className="grid gap-2 md:grid-cols-5">
        <Input className="md:col-span-2" placeholder="Pergunta" value={f.canonical_question} onChange={(e) => setF({ ...f, canonical_question: e.target.value })} />
        <Input placeholder="Resposta" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />
        <Select value={f.field_kind} onChange={(e) => setF({ ...f, field_kind: e.target.value })}>
          {["text", "number", "select", "radio", "textarea", "checkbox"].map((k) => <option key={k}>{k}</option>)}
        </Select>
        <Button disabled={!f.canonical_question || !f.value} onClick={() => create.mutate({ canonical_question: f.canonical_question, field_kind: f.field_kind, answer: { value: f.value }, language: f.language })}>Adicionar</Button>
      </Card>
      <Input placeholder="Busca semântica (ex.: anos de experiência com Python)" value={q} onChange={(e) => setQ(e.target.value)} />
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground"><tr><th className="p-2">Pergunta</th><th>Resposta</th><th>Tipo</th><th>Usos</th><th /></tr></thead>
        <tbody>
          {data?.map((a) => (
            <tr key={a.id} className="border-t">
              <td className="p-2">{a.canonical_question} {a.sensitive && <Badge tone="warning">sensível</Badge>}</td>
              <td>{String(a.answer.value)}</td>
              <td>{a.field_kind}</td>
              <td>{a.use_count}</td>
              <td><Button size="sm" variant="ghost" onClick={() => window.confirm("Excluir este item?") && del.mutate(a.id)}>Excluir</Button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StarTab() {
  const { data } = useGet<Star[]>(["star"], "/api/v1/profile/star-stories");
  const empty = { title: "", experience_id: "", situation: "", task: "", action: "", result: "", competencies: "" };
  const [f, setF] = useState(empty);
  const create = useSend("POST", "/api/v1/profile/star-stories", { success: "História salva", invalidate: [["star"]] });
  const del = useSend<string>("DELETE", (id) => `/api/v1/profile/star-stories/${id}`, { invalidate: [["star"]] });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-2">
        <CardTitle>Nova história</CardTitle>
        <Input placeholder="Título (ex.: previsão de incidentes DB2)" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <Input placeholder="id da experiência (ex.: acme-atual)" value={f.experience_id} onChange={(e) => setF({ ...f, experience_id: e.target.value })} />
        {(["situation", "task", "action", "result"] as const).map((k) => (
          <Textarea key={k} rows={2} placeholder={{ situation: "Situação", task: "Tarefa", action: "Ação", result: "Resultado (com números)" }[k]} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
        ))}
        <Input placeholder="Competências (liderança, conflito, entrega…)" value={f.competencies} onChange={(e) => setF({ ...f, competencies: e.target.value })} />
        <Button disabled={!f.title || !f.result} onClick={async () => { await create.mutateAsync({ ...f, competencies: f.competencies.split(",").map((x) => x.trim()).filter(Boolean) }); setF(empty); }}>Salvar</Button>
      </Card>
      <div className="space-y-2">
        {data?.map((s) => (
          <Card key={s.id} className="text-sm">
            <div className="flex justify-between"><p className="font-medium">{s.title}</p><Button size="sm" variant="ghost" onClick={() => window.confirm("Excluir este item?") && del.mutate(s.id)}>Excluir</Button></div>
            <p><b>S:</b> {s.situation}</p><p><b>T:</b> {s.task}</p><p><b>A:</b> {s.action}</p><p><b>R:</b> {s.result}</p>
            <div className="mt-1 flex gap-1">{s.competencies.map((c) => <Badge key={c}>{c}</Badge>)}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function BlocksTab() {
  const { data } = useGet<Block[]>(["blocklist"], "/api/v1/blocklist");
  const [f, setF] = useState({ kind: "company", value: "", reason: "" });
  const create = useSend("POST", "/api/v1/blocklist", { success: "Bloqueio adicionado", invalidate: [["blocklist"]] });
  const del = useSend<string>("DELETE", (id) => `/api/v1/blocklist/${id}`, { invalidate: [["blocklist"]] });
  return (
    <div className="space-y-3">
      <Card className="grid gap-2 md:grid-cols-4">
        <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          <option value="company">Empresa</option><option value="domain">Domínio</option><option value="recruiter">Recrutador</option><option value="keyword">Palavra no título</option>
        </Select>
        <Input placeholder="Valor" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />
        <Input placeholder="Motivo" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />
        <Button disabled={!f.value} onClick={() => create.mutate(f)}>Bloquear</Button>
      </Card>
      {data?.map((b) => (
        <Card key={b.id} className="flex items-center justify-between text-sm">
          <span><Badge>{b.kind}</Badge> {b.value} <span className="text-muted-foreground">{b.reason}</span></span>
          <Button size="sm" variant="ghost" onClick={() => del.mutate(b.id)}>Remover</Button>
        </Card>
      ))}
    </div>
  );
}

function DocsTab() {
  const qc = useQueryClient();
  const { data } = useGet<Doc[]>(["documents"], "/api/v1/documents");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState("certificate");
  const del = useSend<string>("DELETE", (id) => `/api/v1/documents/${id}`, { invalidate: [["documents"]] });
  const upload = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title || file.name);
    fd.append("kind", kind);
    await rawFetch("/api/v1/documents", { method: "POST", body: fd });
    toast.success("Documento salvo");
    qc.invalidateQueries({ queryKey: ["documents"] });
  };
  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap items-center gap-2">
        <Input className="max-w-xs" placeholder="Título" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Select className="max-w-44" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="certificate">Certificado</option><option value="diploma">Diploma</option><option value="portfolio">Portfólio</option><option value="recommendation">Recomendação</option><option value="other">Outro</option>
        </Select>
        <input type="file" accept=".pdf,.png,.jpg,.jpeg,.docx" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </Card>
      {data?.map((d) => (
        <Card key={d.id} className="flex items-center justify-between text-sm">
          <a className="text-primary underline" href={`/api/v1/documents/${d.id}/download`}>{d.title}</a>
          <span className="text-xs text-muted-foreground">{d.kind} · {formatDateTime(d.created_at)}</span>
          <Button size="sm" variant="ghost" onClick={() => window.confirm("Excluir este item?") && del.mutate(d.id)}>Excluir</Button>
        </Card>
      ))}
    </div>
  );
}

function RefsTab() {
  const { data } = useGet<Ref[]>(["refs"], "/api/v1/profile/references");
  const [f, setF] = useState({ name: "", relation: "", contact: "", consent: false });
  const create = useSend("POST", "/api/v1/profile/references", { success: "Referência salva", invalidate: [["refs"]] });
  const del = useSend<string>("DELETE", (id) => `/api/v1/profile/references/${id}`, { invalidate: [["refs"]] });
  return (
    <div className="space-y-3">
      <Card className="grid gap-2 md:grid-cols-5">
        <Input placeholder="Nome" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <Input placeholder="Relação (ex.: ex-gestor na Acme)" value={f.relation} onChange={(e) => setF({ ...f, relation: e.target.value })} />
        <Input placeholder="Contato" value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} />
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} /> consentiu</label>
        <Button disabled={!f.name} onClick={() => create.mutate(f)}>Adicionar</Button>
      </Card>
      {data?.map((r) => (
        <Card key={r.id} className="flex items-center justify-between text-sm">
          <span>{r.name} · {r.relation} · {r.contact} {r.consent ? <Badge tone="success">consentiu</Badge> : <Badge tone="warning">pedir consentimento</Badge>}</span>
          <Button size="sm" variant="ghost" onClick={() => window.confirm("Excluir este item?") && del.mutate(r.id)}>Excluir</Button>
        </Card>
      ))}
    </div>
  );
}

function GithubTab() {
  const { data, isLoading } = useGet<Repo[]>(["github"], "/api/v1/profile/github/repos");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const imp = useSend("POST", "/api/v1/profile/github/import", { success: "Projetos adicionados ao mestre", invalidate: [["master"]] });
  if (isLoading) return <p className="text-sm">Carregando repositórios…</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">Repositórios com descrição viram projetos do mestre (só fatos do próprio repositório).</p>
      {data?.map((r) => (
        <label key={r.name} className="flex items-start gap-2 rounded-md border p-2 text-sm">
          <input type="checkbox" checked={sel.has(r.name)} onChange={(e) => { const s = new Set(sel); if (e.target.checked) s.add(r.name); else s.delete(r.name); setSel(s); }} />
          <span><b>{r.name}</b> · {r.language ?? "—"} · ★{r.stars}<br /><span className="text-muted-foreground">{r.description ?? "(sem descrição: não será importado)"}</span></span>
        </label>
      ))}
      <Button disabled={!sel.size} onClick={() => imp.mutate({ repos: data?.filter((r) => sel.has(r.name)) })}>Adicionar {sel.size} ao mestre</Button>
    </div>
  );
}
