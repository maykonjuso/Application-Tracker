"use client";

import { CompanyLogo } from "@/components/brand";
import { Wallet } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardTitle, EmptyState, Field, Input, Modal, PageHeader, Select, SkeletonList } from "@/components/ui";
import { useGet, useSend, useTask } from "@/lib/hooks";
import { formatDateTime, money } from "@/lib/utils";

type Annual = {
  annual_net: number;
  monthly_equivalent: number;
  net_monthly: number;
  take_home_monthly?: number;
  pj_equivalent_invoice?: number;
  inss?: number;
  irrf?: number;
  fgts?: number;
  plr_net?: number;
  pension_employer_monthly?: number;
  benefits_annual?: number;
  taxes_monthly?: number;
};
type CurrentCfg = {
  company: string | null;
  regime: string;
  monthly_gross: number;
  meal_monthly: number;
  health_monthly_estimate: number;
  health_employee_cost_monthly: number;
  wellhub_monthly_estimate: number;
  other_monthly: number;
  plr_salaries: number;
  pension_employee_rate: number;
  pension_match_rate: number;
  pension_match_cap: number;
  non_monetary: string[];
};
type Offer = {
  id: string;
  company_name: string;
  regime: string;
  base_salary: number;
  currency: string;
  period: string;
  benefits: Record<string, number>;
  bonus: Record<string, number>;
  work_model: string | null;
  respond_by: string | null;
  status: string;
  annualized: Annual;
  negotiation: {
    market?: { low?: number; median?: number; high?: number; currency?: string; period?: string; notes?: string };
    position?: string;
    target?: number;
    walk_away?: number;
    anchors?: string[];
    script?: string[];
    non_salary_levers?: string[];
    sources?: { url: string; title?: string }[];
  } | null;
};

export default function OffersPage() {
  const { data, isLoading } = useGet<Offer[]>(["offers"], "/api/v1/offers");
  const { data: current, isLoading: currentLoading } = useGet<{ config: CurrentCfg; annualized: Annual | null }>(["offers", "current"], "/api/v1/offers/current");
  const [open, setOpen] = useState(false);
  const base = current?.annualized?.annual_net ?? null;
  const sorted = [...(data ?? [])].sort((a, b) => b.annualized.annual_net - a.annualized.annual_net);
  return (
    <div className="space-y-4">
      <PageHeader icon={<Wallet size={20} />} title="Ofertas" description="Compare propostas pelo valor anual líquido (CLT e PJ na mesma régua)" actions={<Button onClick={() => setOpen(true)}>Registrar proposta</Button>} />
      {currentLoading ? <SkeletonList count={1} /> : current && <CurrentJob cfg={current.config} annual={current.annualized} />}
      {isLoading ? <SkeletonList count={2} className="lg:grid-cols-2" /> : sorted.length === 0 ? <EmptyState title="Nenhuma proposta ainda" /> : (
        <div className="grid gap-3 lg:grid-cols-2">{sorted.map((o, i) => <OfferCard key={o.id} o={o} best={i === 0 && sorted.length > 1} base={base} />)}</div>
      )}
      <Simulator />
      <NewOffer open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

function CurrentJob({ cfg, annual }: { cfg: CurrentCfg; annual: Annual | null }) {
  const [f, setF] = useState<CurrentCfg>(cfg);
  const save = useSend<CurrentCfg>("PUT", "/api/v1/settings/current_compensation", { success: "Remuneração atual atualizada", invalidate: [["offers"]] });
  const num = (k: keyof CurrentCfg, label: string, hint?: string, step = "1") => (
    <Field label={label} hint={hint}>
      <Input type="number" step={step} value={Number(f[k] ?? 0)} onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} />
    </Field>
  );
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle className="mb-0 flex items-center gap-2">{f.company && <CompanyLogo name={f.company} className="h-8 w-8" />}Meu emprego atual {f.company ? `(${f.company})` : ""}: base de comparação</CardTitle>
        {annual && (
          <div className="flex flex-wrap gap-3 text-sm">
            <span>Cai na conta: <b>{money(annual.take_home_monthly)}</b>/mês</span>
            <span>Pacote anual: <b>{money(annual.annual_net)}</b> (≈ {money(annual.monthly_equivalent)}/mês)</span>
            <span>PJ equivalente: <b>{money(annual.pj_equivalent_invoice)}</b>/mês</span>
          </div>
        )}
      </div>
      <div className="grid gap-2 md:grid-cols-4 xl:grid-cols-6">
        <Field label="Empresa"><Input value={f.company ?? ""} onChange={(e) => setF({ ...f, company: e.target.value })} /></Field>
        <Field label="Regime"><Select value={f.regime} onChange={(e) => setF({ ...f, regime: e.target.value })}><option>CLT</option><option>PJ</option></Select></Field>
        {num("monthly_gross", "Salário bruto/mês")}
        {num("meal_monthly", "Ticket (VR/VA)/mês")}
        {num("plr_salaries", "PLR (nº de salários/ano)", "ex.: 1,5", "0.1")}
        {num("pension_employee_rate", "Minha previdência (%)", "0,06 = 6%", "0.01")}
        {num("pension_match_rate", "Contrapartida da empresa", "igual ao que eu aplico…", "0.01")}
        {num("pension_match_cap", "…até o teto de", "0,06 = 6%", "0.01")}
        {num("health_employee_cost_monthly", "Plano de saúde: eu pago/mês")}
        {num("health_monthly_estimate", "Plano: valor estimado do benefício", "quanto custaria contratar")}
        {num("wellhub_monthly_estimate", "Academia (Wellhub/TotalPass): valor estimado")}
        {num("other_monthly", "Outros benefícios/mês")}
      </div>
      {f.non_monetary.length > 0 && (
        <p className="text-xs text-muted-foreground">Também tenho (sem valor em dinheiro na conta): {f.non_monetary.join(" · ")}</p>
      )}
      <Button onClick={() => save.mutate(f)}>Salvar e recalcular</Button>
    </Card>
  );
}

function OfferCard({ o, best, base }: { o: Offer; best: boolean; base: number | null }) {
  const neg = useSend<void, { task_id: string }>("POST", `/api/v1/offers/${o.id}/negotiation`);
  const status = useSend<{ status: string }>("POST", `/api/v1/offers/${o.id}/status`, { success: "Atualizado", invalidate: [["offers"], ["applications"]] });
  const task = useTask();
  const a = o.annualized;
  const n = o.negotiation;
  return (
    <Card className="space-y-2 text-sm">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-2 font-semibold"><CompanyLogo name={o.company_name} className="h-8 w-8" /> {o.company_name} {best && <Badge tone="success">melhor anual líquido</Badge>}</p>
        <Badge>{o.regime} · {o.status}</Badge>
      </div>
      <p>{money(o.base_salary, o.currency)} / {o.period === "year" ? "ano" : "mês"} · {o.work_model ?? "modelo n/d"}</p>
      <div className="grid grid-cols-2 gap-2 rounded-md bg-muted p-2">
        <div><p className="text-xs text-muted-foreground">Anual líquido</p><p className="font-semibold">{money(a.annual_net)}</p></div>
        <div><p className="text-xs text-muted-foreground">Equivalente mensal</p><p className="font-semibold">{money(a.monthly_equivalent)}</p></div>
        {a.pj_equivalent_invoice && <div className="col-span-2 text-xs">PJ precisaria faturar {money(a.pj_equivalent_invoice)}/mês para empatar</div>}
        {base != null && (
          <div className="col-span-2 text-xs">
            Em relação ao emprego atual:{" "}
            <b className={a.annual_net >= base ? "text-success" : "text-danger"}>
              {a.annual_net >= base ? "+" : ""}{money(a.annual_net - base)}/ano ({a.annual_net >= base ? "+" : ""}{Math.round((a.annual_net / base - 1) * 100)}%)
            </b>
          </div>
        )}
      </div>
      {o.respond_by && <p className="text-xs text-warning">Responder até {formatDateTime(o.respond_by)}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={task.running} onClick={async () => task.start((await neg.mutateAsync()).task_id)}>{task.running ? "Pesquisando mercado…" : "Referência salarial e negociação"}</Button>
        <Button size="sm" variant="success" onClick={() => status.mutate({ status: "accepted" })}>Aceitar</Button>
        <Button size="sm" variant="ghost" onClick={() => status.mutate({ status: "declined" })}>Recusar</Button>
      </div>
      {n && (
        <details className="text-xs" open>
          <summary className="cursor-pointer font-medium">Plano de negociação</summary>
          {n.market && <p>Mercado: {money(n.market.low, n.market.currency)} – {money(n.market.high, n.market.currency)} (mediana {money(n.market.median, n.market.currency)}) {n.market.notes}</p>}
          {n.position && <p>{n.position}</p>}
          {n.target && <p><b>Alvo:</b> {money(n.target)} · <b>mínimo aceitável:</b> {money(n.walk_away)}</p>}
          {n.anchors?.length ? <><p className="mt-1 font-medium">Argumentos</p><ul className="list-disc pl-4">{n.anchors.map((x) => <li key={x}>{x}</li>)}</ul></> : null}
          {n.script?.length ? <><p className="mt-1 font-medium">Roteiro</p><ol className="list-decimal pl-4">{n.script.map((x) => <li key={x}>{x}</li>)}</ol></> : null}
          {n.non_salary_levers?.length ? <p className="mt-1">Além do salário: {n.non_salary_levers.join("; ")}</p> : null}
          {n.sources?.map((s) => <a key={s.url} className="mr-2 text-primary underline" href={s.url} target="_blank" rel="noreferrer">{s.title ?? "fonte"}</a>)}
        </details>
      )}
    </Card>
  );
}

function Simulator() {
  const [s, setS] = useState({ regime: "CLT", monthly: 15000, benefits_monthly: 1200, bonus_annual: 0 });
  const [res, setRes] = useState<Annual & { premises?: Record<string, unknown> } | null>(null);
  const run = useSend<typeof s, Annual>("POST", "/api/v1/offers/simulate");
  return (
    <Card className="space-y-3">
      <CardTitle>Simulador CLT x PJ</CardTitle>
      <div className="grid gap-2 md:grid-cols-5">
        <Field label="Regime"><Select value={s.regime} onChange={(e) => setS({ ...s, regime: e.target.value })}><option>CLT</option><option>PJ</option></Select></Field>
        <Field label={s.regime === "CLT" ? "Salário bruto/mês" : "Faturamento/mês"}><Input type="number" value={s.monthly} onChange={(e) => setS({ ...s, monthly: Number(e.target.value) })} /></Field>
        <Field label="Benefícios/mês"><Input type="number" value={s.benefits_monthly} onChange={(e) => setS({ ...s, benefits_monthly: Number(e.target.value) })} /></Field>
        <Field label="Bônus/ano"><Input type="number" value={s.bonus_annual} onChange={(e) => setS({ ...s, bonus_annual: Number(e.target.value) })} /></Field>
        <Button className="self-end" onClick={async () => setRes(await run.mutateAsync(s))}>Calcular</Button>
      </div>
      {res && (
        <div className="grid gap-2 text-sm md:grid-cols-4">
          <p>Líquido mensal: <b>{money(res.net_monthly)}</b></p>
          <p>Anual líquido: <b>{money(res.annual_net)}</b></p>
          <p>Equivalente/mês: <b>{money(res.monthly_equivalent)}</b></p>
          {res.pj_equivalent_invoice && <p>PJ equivalente: <b>{money(res.pj_equivalent_invoice)}</b></p>}
          {res.inss != null && <p className="text-xs">INSS {money(res.inss)} · IRRF {money(res.irrf)} · FGTS/ano {money(res.fgts)}</p>}
        </div>
      )}
      <p className="text-xs text-muted-foreground">Premissas (INSS, IRRF com isenção até R$ 5 mil, FGTS, imposto PJ ~11%, contador) são aproximadas e editáveis via API; confira com seu contador.</p>
    </Card>
  );
}

function NewOffer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [f, setF] = useState({
    company_name: "", regime: "CLT", base_salary: 0, period: "month", vr: 0, saude: 0, bonus: 0, work_model: "remote", respond_by: "",
    plr: 0, pension: 0, match: 0, cost: 0,
  });
  const create = useSend("POST", "/api/v1/offers", { success: "Proposta registrada", invalidate: [["offers"], ["applications"]] });
  return (
    <Modal open={open} onClose={onClose} title="Registrar proposta">
      <div className="space-y-2">
        <Field label="Empresa"><Input value={f.company_name} onChange={(e) => setF({ ...f, company_name: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Regime"><Select value={f.regime} onChange={(e) => setF({ ...f, regime: e.target.value })}><option>CLT</option><option>PJ</option></Select></Field>
          <Field label="Período"><Select value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })}><option value="month">mensal</option><option value="year">anual</option></Select></Field>
        </div>
        <Field label="Valor base"><Input type="number" value={f.base_salary} onChange={(e) => setF({ ...f, base_salary: Number(e.target.value) })} /></Field>
        <div className="grid grid-cols-3 gap-2">
          <Field label="VR/VA mês"><Input type="number" value={f.vr} onChange={(e) => setF({ ...f, vr: Number(e.target.value) })} /></Field>
          <Field label="Saúde mês"><Input type="number" value={f.saude} onChange={(e) => setF({ ...f, saude: Number(e.target.value) })} /></Field>
          <Field label="Bônus ano"><Input type="number" value={f.bonus} onChange={(e) => setF({ ...f, bonus: Number(e.target.value) })} /></Field>
        </div>
        <div className="grid grid-cols-4 gap-2">
          <Field label="PLR (salários/ano)"><Input type="number" step="0.1" value={f.plr} onChange={(e) => setF({ ...f, plr: Number(e.target.value) })} /></Field>
          <Field label="Previdência (%)"><Input type="number" step="0.01" value={f.pension} onChange={(e) => setF({ ...f, pension: Number(e.target.value) })} /></Field>
          <Field label="Contrapartida até"><Input type="number" step="0.01" value={f.match} onChange={(e) => setF({ ...f, match: Number(e.target.value) })} /></Field>
          <Field label="Eu pago (plano etc.)"><Input type="number" value={f.cost} onChange={(e) => setF({ ...f, cost: Number(e.target.value) })} /></Field>
        </div>
        <Field label="Responder até"><Input type="datetime-local" value={f.respond_by} onChange={(e) => setF({ ...f, respond_by: e.target.value })} /></Field>
        <Button
          disabled={!f.company_name || !f.base_salary}
          onClick={async () => {
            await create.mutateAsync({
              company_name: f.company_name, regime: f.regime, base_salary: f.base_salary, period: f.period,
              benefits: { vr_va: f.vr, saude: f.saude, employee_costs_monthly: f.cost },
              bonus: { ...(f.bonus ? { annual: f.bonus } : {}), plr_salaries: f.plr, pension_employee_rate: f.pension, pension_match_rate: f.match, pension_match_cap: f.match },
              work_model: f.work_model,
              respond_by: f.respond_by ? new Date(f.respond_by).toISOString() : null,
            });
            onClose();
          }}
        >Salvar</Button>
      </div>
    </Modal>
  );
}
