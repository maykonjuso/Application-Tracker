"""Motor de avaliação: filtros eliminatórios, Chance, Atratividade, quadrante e ação (RF-AVA)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracker.core import settings_store
from tracker.integrations.embeddings import cosine
from tracker.modules.applications.models import Application
from tracker.modules.applications.state_machine import RESPONDED, SENT
from tracker.modules.companies.models import Company, CompanyProfile, CompanySignal
from tracker.modules.companies.schemas import CompanyProfileContent
from tracker.modules.jobs.models import Job, JobFeedback
from tracker.modules.people.models import Person
from tracker.modules.profile import facts
from tracker.modules.profile import service as profile_service
from tracker.modules.profile.models import BlocklistEntry
from tracker.modules.profile.schemas import MasterProfile, PreferenceData
from tracker.modules.resumes import ats
from tracker.modules.resumes import service as resume_service
from tracker.modules.scoring import features as F
from tracker.modules.scoring.models import JobScore, ScoringWeights


async def current_weights(session: AsyncSession) -> ScoringWeights:
    row = await session.scalar(select(ScoringWeights).where(ScoringWeights.is_current.is_(True)))
    if row is None:
        row = ScoringWeights(
            version=1,
            chance_weights=F.CHANCE_WEIGHTS,
            attractiveness_weights=F.ATTRACTIVENESS_WEIGHTS,
            thresholds=F.THRESHOLDS,
            source="default",
            is_current=True,
        )
        session.add(row)
        await session.flush()
    return row


async def save_weights(
    session: AsyncSession,
    chance: dict[str, float],
    attr: dict[str, float],
    thresholds: dict[str, Any],
    source: str,
    metrics: dict[str, Any] | None = None,
    activate: bool = True,
) -> ScoringWeights:
    def norm(w: dict[str, float]) -> dict[str, float]:
        total = sum(max(0.0, v) for v in w.values()) or 1.0
        return {k: round(max(0.0, v) / total, 4) for k, v in w.items()}

    last = await session.scalar(select(func.max(ScoringWeights.version)))
    if activate:
        await session.execute(update(ScoringWeights).values(is_current=False))
    row = ScoringWeights(
        version=(last or 0) + 1,
        chance_weights=norm(chance),
        attractiveness_weights=norm(attr),
        thresholds={**F.THRESHOLDS, **thresholds},
        source=source,
        is_current=activate,
        metrics=metrics or {},
    )
    session.add(row)
    await session.flush()
    return row


def my_level(master: MasterProfile) -> str:
    h = " ".join(master.headlines.values()).lower()
    for level, pat in (
        ("staff", r"staff|principal|lead"),
        ("senior", r"senior|sênior"),
        ("mid", r"mid|pleno"),
        ("junior", r"junior|júnior"),
    ):
        if re.search(pat, h):
            return level
    years = facts.total_years(master)
    return "senior" if years >= 6 else "mid" if years >= 3 else "junior"


def requirement_coverage(master: MasterProfile, reqs: list[str]) -> tuple[float | None, list[str]]:
    if not reqs:
        return None, []
    idx = facts.skill_index(master)
    blob = facts.norm(master.model_dump_json())
    missing = []
    for r in reqs:
        if facts.has_skill(master, r, idx):
            continue
        n = facts.norm(re.sub(r"\(.*?\)", "", r))
        if n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", blob):
            continue
        missing.append(r)
    return round(1 - len(missing) / len(reqs), 3), missing


async def knockout(
    session: AsyncSession,
    job: Job,
    company: Company,
    master: MasterProfile,
    prefs: PreferenceData,
) -> dict[str, Any]:
    reasons: list[str] = []
    blocks = (await session.scalars(select(BlocklistEntry))).all()
    cname = company.normalized_name
    title_l = job.title.lower()
    for b in blocks:
        v = b.value.lower()
        if b.kind == "company" and (v in cname or cname in v or v in company.name.lower()):
            reasons.append(f"empresa bloqueada ({b.value})")
        elif b.kind == "domain" and any(v in d for d in company.domains or []):
            reasons.append(f"domínio bloqueado ({b.value})")
        elif b.kind == "keyword" and v in title_l:
            reasons.append(f"palavra bloqueada no título ({b.value})")
        elif b.kind == "recruiter" and job.recruiter and v in str(job.recruiter.get("name", "")).lower():
            reasons.append(f"recrutador bloqueado ({b.value})")
    if company.is_blocked:
        reasons.append("empresa marcada como bloqueada")

    other = await session.scalar(
        select(Application.id)
        .join(Job, Job.id == Application.job_id)
        .where(
            Job.company_id == company.id,
            Job.id != job.id,
            Job.normalized_title == job.normalized_title,
            Application.status.in_(SENT),
        )
    )
    if other:
        reasons.append("você já se candidatou a esta vaga (outra publicação)")
    cfg = await settings_store.get(session, "knockout")
    cutoff = datetime.now(UTC) - timedelta(days=int(cfg["reapply_cooldown_days"]))
    recent_reject = await session.scalar(
        select(Application.id)
        .join(Job, Job.id == Application.job_id)
        .where(Job.company_id == company.id, Application.status == "rejected", Application.last_activity_at >= cutoff)
    )
    if recent_reject:
        reasons.append(f"rejeitado nesta empresa há menos de {cfg['reapply_cooldown_days']} dias")

    if job.work_model and prefs.work_models and job.work_model not in prefs.work_models:
        reasons.append(f"modelo de trabalho {job.work_model} fora das preferências")
    if job.work_model in ("onsite", "hybrid") and job.location and prefs.locations:
        loc = facts.norm(job.location)
        if not any(facts.norm(p.split(",")[0]) in loc for p in prefs.locations):
            reasons.append(f"presencial/híbrido fora das cidades aceitas ({job.location})")
    sal = job.salary or {}
    regime = sal.get("regime") or job.regime
    mins = prefs.min_salary.get(regime or "", {}) if regime else {}
    cur = sal.get("currency")
    if sal.get("max") and cur and cur in mins:
        monthly_max = F.to_monthly(float(sal["max"]), sal.get("period"))
        if monthly_max < mins[cur]:
            reasons.append(f"salário máximo ({cur} {int(monthly_max)}/mês) abaixo do mínimo")
    extra = job.extra or {}
    if extra.get("scam_signals"):
        reasons.append("sinais de golpe: " + ", ".join(extra["scam_signals"][:2]))
    langs = {lang.name.lower() for lang in master.languages}
    for req_lang in (job.requirements or {}).get("languages") or []:
        rl = req_lang.lower()
        known = any(k in rl for k in langs) or any(
            x in rl
            for x in (["english", "inglês", "ingles"] if "english" in langs else [])
            + (["português", "portugues", "portuguese"] if "portuguese" in langs else [])
        )
        if not known:
            reasons.append(f"idioma exigido não falado ({req_lang})")
    if job.seniority in F.LEVELS and F.LEVELS.index(job.seniority) - F.LEVELS.index(my_level(master)) >= 2:
        reasons.append(f"senioridade {job.seniority} muito acima")
    return {"passed": not reasons, "reasons": reasons}


async def company_profile(session: AsyncSession, company_id: Any) -> tuple[CompanyProfileContent | None, CompanyProfile | None]:
    row = await session.scalar(
        select(CompanyProfile)
        .where(CompanyProfile.company_id == company_id)
        .order_by(CompanyProfile.collected_at.desc())
        .limit(1)
    )
    if row is None:
        return None, None
    return CompanyProfileContent.model_validate(row.content), row


async def responsiveness(
    session: AsyncSession, company_id: Any, profile: CompanyProfileContent | None
) -> tuple[F.Feature, dict[str, Any]]:
    apps = (
        await session.execute(
            select(Application.status, Application.submitted_at)
            .join(Job, Job.id == Application.job_id)
            .where(Job.company_id == company_id, Application.status.in_(SENT))
        )
    ).all()
    week_ago = datetime.now(UTC) - timedelta(days=7)
    considered = [a for a in apps if (sub := a[1]) is None or sub <= week_ago]
    responded = sum(1 for a in considered if a[0] in RESPONDED)
    adjust = 0.0
    if profile is not None:
        if (profile.ghosting_reports or 0) >= 3:
            adjust -= 2
        if profile.avg_process_days is not None and profile.avg_process_days <= 21:
            adjust += 2
    signals = (
        await session.scalars(
            select(CompanySignal).where(
                CompanySignal.company_id == company_id, CompanySignal.kind.in_(["ghosting_report", "response_time"])
            )
        )
    ).all()
    for s in signals:
        adjust += -2 if s.kind == "ghosting_report" else 2
    return F.responsividade(len(considered), responded, public_adjust=adjust)


async def score_job(session: AsyncSession, job: Job) -> JobScore:
    version, master = await profile_service.current_master(session)
    prefs = await profile_service.get_preferences(session)
    weights = await current_weights(session)
    company = await session.get(Company, job.company_id)
    assert company is not None
    ko = await knockout(session, job, company, master, prefs)
    profile, _ = await company_profile(session, company.id)
    reqs = job.requirements or {}
    must_cov, missing_must = requirement_coverage(master, list(reqs.get("must") or []))
    nice_cov, _ = requirement_coverage(master, list(reqs.get("nice") or []))
    hours = F.hours_since(job.posted_at or job.first_seen_at)
    age_days = (hours / 24) if hours is not None else None
    resp_feature, resp_info = await responsiveness(session, company.id, profile)
    people = (await session.scalars(select(Person).where(Person.company_id == company.id))).all()
    referral = any(p.kind == "referrer" and p.referral_status in ("asked", "accepted") for p in people)
    first_deg = sum(1 for p in people if p.kind in ("connection", "referrer"))
    extra = job.extra or {}
    generic_value = extra.get("is_generic")
    is_generic = generic_value if isinstance(generic_value, bool) and not extra.get("needs_extraction") else None

    chance_f = {
        "fit_tecnico": F.fit_tecnico(must_cov, nice_cov, missing_must),
        "senioridade": F.senioridade(job.seniority, my_level(master), reqs.get("years"), facts.total_years(master)),
        "concorrencia": F.concorrencia(job.applicants_count),
        "frescor": F.frescor(hours),
        "responsividade": resp_feature,
        "recrutador_ativo": F.recrutador_ativo(job.recruiter),
        "nao_fantasma": F.nao_fantasma(
            job.repost_count,
            age_days,
            is_generic,
            profile.layoffs_12m if profile else None,
            age_basis="data de publicação" if job.posted_at else "primeira coleta",
        ),
        "conexoes": F.conexoes(referral, first_deg, 0),
    }
    chance, chance_bd = F.weighted(chance_f, weights.chance_weights)

    sal = job.salary or {}
    regime = sal.get("regime") or job.regime or "CLT"
    expectation_map = prefs.salary_expectation.get(regime) or prefs.min_salary.get(regime) or {}
    cur = sal.get("currency")
    expectation = expectation_map.get(cur) if cur else None
    feedback_sim = await _feedback_similarity(session, job)
    master_skills = {facts.norm(s) for items in master.skills.values() for s in items}
    stack = profile.tech_stack if profile else []
    stack_hits = sum(1 for s in stack if facts.norm(s) in master_skills)
    src = [s.url for s in profile.sources][:3] if profile else []
    attr_f = {
        "reputacao": F.reputacao(
            profile.reputation.rating if profile else None,
            profile.reputation.rating_trend_12m if profile else None,
            src,
        ),
        "cultura": F.cultura(profile.culture_positive if profile else [], profile.culture_alerts if profile else []),
        "saude": F.saude(
            profile.headcount_growth_12m if profile else None,
            profile.recent_funding if profile else None,
            profile.layoffs_12m if profile else None,
            profile.crisis_news if profile else None,
        ),
        "remuneracao": F.remuneracao(job.salary, expectation, cur is not None),
        "carreira": F.carreira(
            profile.is_product_company if profile else None,
            profile.is_body_shop if profile else None,
            stack,
            stack_hits,
        ),
        "condicoes": F.condicoes(job.work_model, prefs.work_models, job.benefits or []),
        "alinhamento": F.alinhamento(
            company.sector or (profile.sector if profile else None),
            prefs.sectors_preferred,
            prefs.sectors_avoided,
            feedback_sim,
        ),
    }
    attractiveness, attr_bd = F.weighted(attr_f, weights.attractiveness_weights)

    th = weights.thresholds
    quad = F.quadrant(chance, attractiveness, th) if ko["passed"] else "blocked"
    prio = F.priority(chance, attractiveness, hours, company.is_watchlisted, th) if ko["passed"] else 0.0
    selection = await resume_service.select_for_job(session, job)
    action = suggested_action(quad, selection["needs_tailored"], bool(job.recruiter))

    await session.execute(update(JobScore).where(JobScore.job_id == job.id).values(is_current=False))
    existing = await session.scalar(
        select(JobScore).where(
            JobScore.job_id == job.id, JobScore.weights_version == weights.version, JobScore.master_version == version
        )
    )
    row = existing or JobScore(job_id=job.id, weights_version=weights.version, master_version=version)
    row.knockout = ko
    row.chance, row.chance_breakdown = chance, chance_bd
    row.attractiveness, row.attractiveness_breakdown = attractiveness, attr_bd
    row.priority, row.quadrant, row.suggested_action = prio, quad, action
    row.best_variant_id = selection["best_variant_id"]
    row.variant_scores = {
        **selection["scores"],
        "_needs_tailored": selection["needs_tailored"],
        "_best_score": selection["best_score"],
    }
    row.explanation = {
        "chance": F.top_reasons(chance_bd),
        "attractiveness": F.top_reasons(attr_bd),
        "missing_must": missing_must,
        "responsiveness": resp_info,
        "knockout": ko["reasons"],
    }
    row.is_current = True
    row.computed_at = datetime.now(UTC)
    if existing is None:
        session.add(row)
    await session.flush()
    return row


def suggested_action(quad: str, needs_tailored: bool, has_recruiter: bool) -> str:
    if quad == "blocked":
        return "não aplicar (filtro eliminatório)"
    if quad == "top":
        parts = ["CV específico" if needs_tailored else "coringa", "aplicar hoje"]
        if has_recruiter:
            parts.insert(1, "mensagem ao recrutador")
        return " + ".join(parts)
    if quad == "bet":
        return "CV específico + contato/indicação antes de aplicar"
    if quad == "volume":
        return "coringa; candidatura rápida com o assistente"
    return "não aplicar (só se sobrar tempo)"


async def _feedback_similarity(session: AsyncSession, job: Job) -> float | None:
    rows = (
        await session.execute(
            select(JobFeedback.verdict, Job.embedding)
            .join(Job, Job.id == JobFeedback.job_id)
            .where(JobFeedback.job_id != job.id)
            .limit(200)
        )
    ).all()
    if not rows or job.embedding is None:
        return None
    emb = [float(x) for x in job.embedding]
    good: list[float] = []
    bad: list[float] = []
    for verdict, other in rows:
        if other is None:
            continue
        sim = cosine(emb, [float(x) for x in other])
        (good if verdict == "good" else bad).append(sim)
    g = max(good) if good else 0.0
    b = max(bad) if bad else 0.0
    return F.clamp(0.5 + (g - b) / 2)


async def current_score(session: AsyncSession, job_id: Any) -> JobScore | None:
    return await session.scalar(select(JobScore).where(JobScore.job_id == job_id, JobScore.is_current.is_(True)))


def keyword_gap(master: MasterProfile, job: Job) -> list[str]:
    _, missing = requirement_coverage(master, list((job.requirements or {}).get("must") or []))
    return missing


__all__ = ["ats", "keyword_gap", "score_job"]
