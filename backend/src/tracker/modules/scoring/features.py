"""Features do motor de avaliação (docs/10-motor-avaliacao.md). Funções puras, valores em [0, 1]."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CHANCE_WEIGHTS = {
    "fit_tecnico": 0.30,
    "senioridade": 0.10,
    "concorrencia": 0.10,
    "frescor": 0.10,
    "responsividade": 0.15,
    "recrutador_ativo": 0.05,
    "nao_fantasma": 0.10,
    "conexoes": 0.10,
}
ATTRACTIVENESS_WEIGHTS = {
    "reputacao": 0.25,
    "cultura": 0.15,
    "saude": 0.15,
    "remuneracao": 0.20,
    "carreira": 0.10,
    "condicoes": 0.10,
    "alinhamento": 0.05,
}
THRESHOLDS = {
    "chance": 60,
    "attractiveness": 60,
    "priority_chance_weight": 0.55,
    "fresh_bonus": 5,
    "watchlist_bonus": 5,
}

NEUTRAL = 0.5
LEVELS = ["intern", "junior", "mid", "senior", "staff"]


@dataclass
class Feature:
    value: float
    evidence: str
    missing: bool = False
    sources: list[str] = field(default_factory=list)

    def as_dict(self, weight: float) -> dict[str, Any]:
        v = max(0.0, min(1.0, self.value))
        return {
            "value": round(v, 3),
            "weight": weight,
            "contribution": round(v * weight * 100, 2),
            "evidence": self.evidence,
            "missing": self.missing,
            "sources": self.sources,
        }


def clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


# ---------------- Chance ----------------
def fit_tecnico(must_cov: float | None, nice_cov: float | None, missing_must: list[str]) -> Feature:
    if must_cov is None:
        return Feature(NEUTRAL, "requisitos ainda não extraídos", missing=True)
    v = 0.7 * must_cov + 0.3 * (nice_cov if nice_cov is not None else must_cov)
    ev = f"{round(must_cov * 100)}% dos obrigatórios"
    if missing_must:
        ev += f"; faltam: {', '.join(missing_must[:4])}"
    return Feature(v, ev)


def senioridade(job_level: str | None, my_level: str, years_required: int | None, my_years: float) -> Feature:
    parts: list[float] = []
    ev: list[str] = []
    if job_level in LEVELS:
        diff = abs(LEVELS.index(job_level) - LEVELS.index(my_level))
        parts.append({0: 1.0, 1: 0.6, 2: 0.2}.get(diff, 0.05))
        ev.append(f"vaga {job_level}, você {my_level}")
    if years_required:
        ratio = my_years / years_required
        parts.append(1.0 if ratio >= 1 else 0.6 if ratio >= 0.75 else 0.25)
        ev.append(f"pede {years_required}+ anos, você tem {my_years}")
    if not parts:
        return Feature(NEUTRAL, "senioridade não informada", missing=True)
    return Feature(sum(parts) / len(parts), "; ".join(ev))


def concorrencia(applicants: int | None) -> Feature:
    if applicants is None:
        return Feature(NEUTRAL, "nº de candidatos desconhecido", missing=True)
    v = 1.0 if applicants < 25 else 0.7 if applicants <= 100 else 0.4 if applicants <= 200 else 0.15
    return Feature(v, f"{applicants} candidatos")


def hours_since(dt: datetime | None, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (now - dt).total_seconds() / 3600)


def frescor(hours: float | None) -> Feature:
    if hours is None:
        return Feature(NEUTRAL, "data de publicação desconhecida", missing=True)
    d = hours / 24
    v = 1.0 if hours < 24 else 0.8 if d < 3 else 0.55 if d < 7 else 0.35 if d < 14 else 0.15 if d < 30 else 0.05
    label = f"{int(hours)} h" if hours < 48 else f"{int(d)} dias"
    return Feature(v, f"publicada há {label}")


def responsividade(
    own_apps: int, own_responses: int, alpha: float = 2.0, beta: float = 6.0, public_adjust: float = 0.0
) -> tuple[Feature, dict[str, Any]]:
    """Beta(α,β) + dados próprios; sinais públicos ajustam o prior (±2 observações por sinal)."""
    a = max(0.1, alpha + max(0.0, public_adjust))
    b = max(0.1, beta + max(0.0, -public_adjust))
    r = (own_responses + a) / (own_apps + a + b)
    conf = "baixa" if own_apps < 3 else "média" if own_apps < 10 else "alta"
    info = {"rate": round(r, 3), "own_apps": own_apps, "own_responses": own_responses, "confidence": conf}
    return Feature(
        r,
        f"responde ~{round(r * 100)}% (confiança {conf}, {own_apps} dados)",
        missing=own_apps == 0 and public_adjust == 0,
    ), info


def recrutador_ativo(recruiter: dict[str, Any] | None) -> Feature:
    if not recruiter:
        return Feature(0.0, "recrutador não identificado")
    v = 0.5 + (0.5 if recruiter.get("active_recently") else 0.0)
    return Feature(
        v,
        f"recrutador: {recruiter.get('name', 'identificado')}"
        + (" (ativo recentemente)" if recruiter.get("active_recently") else ""),
    )


def nao_fantasma(
    repost_count: int,
    age_days: float | None,
    is_generic: bool | None,
    layoffs: bool | None,
    age_basis: str = "data de publicação",
) -> Feature:
    pen = 0.0
    evidence: list[str] = []
    if repost_count >= 2:
        pen += 0.3
        evidence.append(f"repostagem: {repost_count} vezes (sinal: 2 ou mais)")
    else:
        evidence.append(f"repostagem: {repost_count} (sinal a partir de 2; não atingido)")
    if age_days is not None and age_days > 60:
        pen += 0.3
        evidence.append(f"antiguidade desde {age_basis}: {int(age_days)} dias (sinal: acima de 60)")
    elif age_days is not None:
        evidence.append(f"antiguidade desde {age_basis}: {int(age_days)} dias (sinal acima de 60; não atingido)")
    else:
        evidence.append("antiguidade: sem data para avaliar")
    if is_generic is True:
        pen += 0.2
        evidence.append("descrição marcada como genérica pela extração")
    elif is_generic is False:
        evidence.append("descrição não foi marcada como genérica pela extração")
    else:
        evidence.append("descrição ainda não analisada quanto a conteúdo genérico")
    if layoffs is True:
        pen += 0.3
        evidence.append("há informação de demissões recentes da empresa")
    elif layoffs is False:
        evidence.append("perfil consultado não aponta demissões recentes")
    else:
        evidence.append("informação sobre demissões recentes indisponível")
    evidence.append("heurística de triagem; não confirma se a vaga é real ou está ativa")
    return Feature(clamp(1 - pen), ". ".join(evidence), missing=age_days is None or is_generic is None or layoffs is None)


def conexoes(referral_possible: bool, first_degree: int, second_degree: int) -> Feature:
    if referral_possible:
        return Feature(1.0, "indicação possível")
    if first_degree:
        return Feature(0.7, f"{first_degree} conexão(ões) de 1º grau")
    if second_degree:
        return Feature(0.4, f"{second_degree} conexão(ões) de 2º grau")
    return Feature(0.0, "nenhuma conexão conhecida")


# ---------------- Atratividade ----------------
def reputacao(rating: float | None, trend: float | None, sources: list[str]) -> Feature:
    if rating is None:
        return Feature(NEUTRAL, "sem avaliações públicas", missing=True)
    v = clamp((rating - 3.0) / 1.5) + (0.1 if (trend or 0) > 0.1 else -0.1 if (trend or 0) < -0.1 else 0)
    trend_s = f" ({'+' if (trend or 0) >= 0 else ''}{trend:.1f} em 12m)" if trend is not None else ""
    return Feature(clamp(v), f"nota {rating:.1f}{trend_s}", sources=sources)


def cultura(positive: list[str], alerts: list[str]) -> Feature:
    if not positive and not alerts:
        return Feature(NEUTRAL, "sem dados de cultura", missing=True)
    v = clamp(0.5 + 0.1 * len(positive) - 0.15 * len(alerts))
    ev = []
    if positive:
        ev.append("+ " + ", ".join(positive[:3]))
    if alerts:
        ev.append("alertas: " + ", ".join(alerts[:3]))
    return Feature(v, "; ".join(ev))


def saude(growth: float | None, funding: bool | None, layoffs: bool | None, crisis: bool | None) -> Feature:
    if growth is None and funding is None and layoffs is None and crisis is None:
        return Feature(NEUTRAL, "sem dados de saúde", missing=True)
    v = 0.5
    ev = []
    if growth is not None:
        v += clamp(growth * 2) * 0.3 if growth > 0 else max(-0.3, growth * 2 * 0.3)
        ev.append(f"headcount {'+' if growth >= 0 else ''}{round(growth * 100)}%/ano")
    if funding:
        v += 0.15
        ev.append("investimento recente")
    if layoffs:
        v -= 0.3
        ev.append("layoff em 12 meses")
    if crisis:
        v -= 0.2
        ev.append("notícias de crise")
    return Feature(clamp(v), "; ".join(ev))


def to_monthly(amount: float, period: str | None) -> float:
    return amount / 12 if period == "year" else amount * 160 if period == "hour" else amount


def remuneracao(salary: dict[str, Any] | None, expectation: float | None, currency_ok: bool) -> Feature:
    if not salary or expectation is None or not currency_ok:
        return Feature(NEUTRAL, "faixa salarial não informada" if not salary else "sem pretensão para comparar", missing=True)
    top = salary.get("max") or salary.get("min")
    if not top:
        return Feature(NEUTRAL, "faixa salarial incompleta", missing=True)
    monthly = to_monthly(float(top), salary.get("period"))
    ratio = monthly / expectation
    v = (
        1.0
        if ratio >= 1.2
        else 0.7 + (ratio - 1.0) * 1.5
        if ratio >= 1.0
        else max(0.0, 0.3 + (ratio - 0.85) * 2.67)
        if ratio >= 0.85
        else 0.0
    )
    return Feature(clamp(v), f"até {round(ratio * 100)}% da sua pretensão")


def carreira(product: bool | None, body_shop: bool | None, stack: list[str], my_skills_hit: int) -> Feature:
    if product is None and body_shop is None and not stack:
        return Feature(NEUTRAL, "sem dados de carreira", missing=True)
    v = 0.5
    ev = []
    if product:
        v += 0.3
        ev.append("produto próprio")
    if body_shop:
        v -= 0.3
        ev.append("alocação/body shop")
    if stack:
        v += min(0.2, 0.05 * my_skills_hit)
        ev.append(f"stack com {my_skills_hit} tecnologias suas")
    return Feature(clamp(v), "; ".join(ev))


def condicoes(work_model: str | None, preferred: list[str], benefits: list[str]) -> Feature:
    base = {"remote": 1.0, "hybrid": 0.6, "onsite": 0.3}.get(work_model or "", NEUTRAL)
    if work_model in preferred:
        base = max(base, 0.8)
    v = clamp(base * 0.8 + min(0.2, 0.03 * len(benefits)))
    return Feature(v, f"{work_model or 'modelo não informado'}, {len(benefits)} benefícios", missing=work_model is None)


def alinhamento(sector: str | None, preferred: list[str], avoided: list[str], feedback_sim: float | None) -> Feature:
    v = NEUTRAL
    ev = []

    def match(s: str, lst: list[str]) -> bool:
        return any(re.search(re.escape(x.lower()), s.lower()) for x in lst)

    if sector and match(sector, preferred):
        v += 0.3
        ev.append(f"setor preferido ({sector})")
    if sector and match(sector, avoided):
        v -= 0.4
        ev.append(f"setor evitado ({sector})")
    if feedback_sim is not None:
        v += (feedback_sim - 0.5) * 0.4
        ev.append("parecida com vagas que você marcou")
    return Feature(clamp(v), "; ".join(ev) or "neutro")


def weighted(features: dict[str, Feature], weights: dict[str, float]) -> tuple[float, dict[str, Any]]:
    total_w = sum(weights.get(k, 0) for k in features) or 1.0
    breakdown = {k: f.as_dict(weights.get(k, 0)) for k, f in features.items()}
    score = sum(clamp(f.value) * weights.get(k, 0) for k, f in features.items()) / total_w * 100
    return round(score, 1), breakdown


def quadrant(chance: float, attractiveness: float, th: dict[str, Any]) -> str:
    hc, ha = chance >= th["chance"], attractiveness >= th["attractiveness"]
    return "top" if hc and ha else "bet" if ha else "volume" if hc else "discard"


def priority(chance: float, attractiveness: float, hours: float | None, watchlisted: bool, th: dict[str, Any]) -> float:
    w = th.get("priority_chance_weight", 0.55)
    p = w * chance + (1 - w) * attractiveness
    if hours is not None and hours < 24:
        p += th.get("fresh_bonus", 5)
    if watchlisted:
        p += th.get("watchlist_bonus", 5)
    return round(p, 2)


def top_reasons(breakdown: dict[str, Any], n_pos: int = 3, n_neg: int = 2) -> dict[str, list[str]]:
    items = [(k, v) for k, v in breakdown.items() if not v.get("missing")]
    pos = sorted(items, key=lambda kv: -kv[1]["contribution"])
    neg = sorted(items, key=lambda kv: kv[1]["value"])
    return {
        "positives": [v["evidence"] for _, v in pos[:n_pos] if v["value"] >= 0.6],
        "negatives": [v["evidence"] for _, v in neg[:n_neg] if v["value"] < 0.5],
    }
