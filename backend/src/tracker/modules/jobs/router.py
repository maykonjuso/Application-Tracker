"""Rotas de vagas e buscas salvas (docs/06 §Vagas)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Text, and_, case, exists, func, or_, select

from tracker.api.deps import SessionDep, TaskAccepted, get_or_404
from tracker.core import audit
from tracker.core.security import CurrentUser
from tracker.core.tasks import dispatch, enqueue
from tracker.modules.applications import service as app_service
from tracker.modules.applications.models import Application
from tracker.modules.companies.models import Company
from tracker.modules.jobs.models import Job, JobFeedback, JobSnapshot, JobSource, SavedSearch
from tracker.modules.jobs.quality import assess_job_quality
from tracker.modules.jobs.schemas import (
    FeedbackIn,
    JobDetail,
    JobImportIn,
    JobOut,
    JobQualityOut,
    JobSourceOut,
    SavedSearchIn,
    SavedSearchOut,
    ScoreOut,
)
from tracker.modules.scoring.models import JobScore

router = APIRouter(prefix="/jobs", tags=["jobs"])
searches_router = APIRouter(prefix="/saved-searches", tags=["jobs"])


class SelectDiscoveredIn(BaseModel):
    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


def _job_out(
    job: Job,
    company: Company | None,
    score: JobScore | None,
    app: Application | None,
    cls: type[JobOut] = JobOut,
    quality: JobQualityOut | None = None,
) -> Any:
    if cls is JobDetail:
        if quality is None:
            raise ValueError("JobDetail requires a quality assessment")
        job_data = {column.key: getattr(job, column.key) for column in Job.__mapper__.column_attrs}
        out = cls.model_validate({**job_data, "quality": quality})
    else:
        out = cls.model_validate(job)
    out.company_name = company.name if company else None
    out.score = ScoreOut.model_validate(score) if score else None
    out.application_id = app.id if app else None
    out.application_status = app.status if app else None
    return out


@router.get("", response_model=list[JobOut])
async def list_jobs(
    session: SessionDep,
    _user: CurrentUser,
    quadrant: Annotated[list[str] | None, Query(description="um ou mais quadrantes")] = None,
    status: str = "open",
    triage: str | None = None,
    q: str | None = None,
    company_id: uuid.UUID | None = None,
    company: Annotated[list[uuid.UUID] | None, Query()] = None,
    tech: Annotated[list[str] | None, Query(description="tecnologias exigidas (todas), ex.: tech=Python&tech=Spark")] = None,
    work_model: Annotated[list[str] | None, Query()] = None,
    regime: Annotated[list[str] | None, Query()] = None,
    seniority: Annotated[list[str] | None, Query()] = None,
    apply_kind: Annotated[list[str] | None, Query()] = None,
    location: str | None = None,
    salary_min: float | None = Query(None, description="salário mensal mínimo (BRL ou moeda da vaga)"),
    with_salary: bool = False,
    min_chance: float | None = None,
    min_attractiveness: float | None = None,
    posted_within_days: int | None = None,
    applied: bool | None = None,
    sort: str = "priority",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[JobOut]:
    stmt = (
        select(Job, Company, JobScore, Application)
        .join(Company, Company.id == Job.company_id)
        .outerjoin(JobScore, and_(JobScore.job_id == Job.id, JobScore.is_current.is_(True)))
        .outerjoin(Application, Application.job_id == Job.id)
    )
    if status != "all":
        stmt = stmt.where(Job.status == status)
    if quadrant:
        stmt = stmt.where(JobScore.quadrant.in_(quadrant))
    if triage:
        stmt = stmt.where(Job.triage == triage)
    if company_id:
        stmt = stmt.where(Job.company_id == company_id)
    if q:
        terms = [term for term in q.split() if term]
        stmt = stmt.where(
            and_(
                *(
                    Job.title.ilike(f"%{term}%")
                    | Company.name.ilike(f"%{term}%")
                    | Job.description_text.ilike(f"%{term}%")
                    | exists(
                        select(JobSource.id).where(
                            JobSource.job_id == Job.id,
                            JobSource.raw.cast(Text).ilike(f"%{term}%"),
                        )
                    )
                    for term in terms
                )
            )
        )
    if company:
        stmt = stmt.where(Job.company_id.in_(company))
    for t in tech or []:  # cada tecnologia precisa aparecer nos requisitos ou na descrição
        like = f"%{t.strip()}%"
        stmt = stmt.where(Job.requirements.cast(Text).ilike(like) | Job.description_text.ilike(like))
    if work_model:
        stmt = stmt.where(Job.work_model.in_(work_model))
    if regime:
        stmt = stmt.where(Job.regime.in_(regime))
    if seniority:
        stmt = stmt.where(Job.seniority.in_(seniority))
    if apply_kind:
        stmt = stmt.where(Job.apply_kind.in_(apply_kind))
    if location:
        location_terms = [Job.location.ilike(f"%{location}%")]
        from tracker.modules.jobs import relevance

        aliases = relevance.country_aliases(location)
        for alias in aliases:
            if alias == "us":
                location_terms.append(
                    Job.location.op("~*")(r"(^|[^a-zA-Z])us([^a-zA-Z]|$)")
                )
            else:
                location_terms.append(Job.location.ilike(f"%{alias}%"))
        if aliases:
            location_terms.extend(Job.location.ilike(f"%{term}%") for term in ("worldwide", "global", "anywhere"))
        stmt = stmt.where(or_(*location_terms))
    monthly = monthly_salary_expr()
    if with_salary or salary_min is not None:
        stmt = stmt.where(monthly.is_not(None))
    if salary_min is not None:
        stmt = stmt.where(monthly >= salary_min)
    if min_chance is not None:
        stmt = stmt.where(JobScore.chance >= min_chance)
    if min_attractiveness is not None:
        stmt = stmt.where(JobScore.attractiveness >= min_attractiveness)
    if posted_within_days:
        since = datetime.now(UTC) - timedelta(days=posted_within_days)
        stmt = stmt.where(func.coalesce(Job.posted_at, Job.first_seen_at) >= since)
    if applied is True:
        stmt = stmt.where(Application.submitted_at.is_not(None))
    elif applied is False:
        stmt = stmt.where(Application.submitted_at.is_(None))
    order = {
        "priority": (JobScore.priority.desc().nulls_last(), Job.first_seen_at.desc()),
        "recent": (Job.posted_at.desc().nulls_last(), Job.first_seen_at.desc()),
        "chance": (JobScore.chance.desc().nulls_last(),),
        "attractiveness": (JobScore.attractiveness.desc().nulls_last(),),
        "salary": (monthly.desc().nulls_last(), JobScore.priority.desc().nulls_last()),
        "company": (Company.name.asc(), JobScore.priority.desc().nulls_last()),
    }.get(sort, (Job.first_seen_at.desc(),))
    rows = (await session.execute(stmt.order_by(*order).limit(min(limit, 200)).offset(offset))).all()
    return [_job_out(j, c, s, a) for j, c, s, a in rows]


def monthly_salary_expr() -> Any:
    """Salário mensal da vaga (máximo da faixa; anual ÷ 12, por hora × 160). NULL quando não informado."""
    top = func.coalesce(Job.salary["max"].as_float(), Job.salary["min"].as_float())
    period = Job.salary["period"].as_string()
    return case((period == "year", top / 12), (period == "hour", top * 160), else_=top)


@router.get("/facets")
async def facets(session: SessionDep, _user: CurrentUser, status: str = "open") -> dict[str, Any]:
    """Opções dos filtros a partir das vagas que você tem: tecnologias mais pedidas, empresas, faixas de salário..."""
    stmt = select(Job, Company.name).join(Company, Company.id == Job.company_id)
    if status != "all":
        stmt = stmt.where(Job.status == status)
    rows = (await session.execute(stmt)).all()
    techs: Counter[str] = Counter()
    display: dict[str, str] = {}
    companies: Counter[tuple[str, str]] = Counter()
    fields: dict[str, Counter[str]] = {
        "work_model": Counter(),
        "regime": Counter(),
        "seniority": Counter(),
        "apply_kind": Counter(),
    }
    salaries: list[float] = []
    for job, cname in rows:
        reqs = job.requirements or {}
        for item in {str(x).strip() for x in [*(reqs.get("must") or []), *(reqs.get("nice") or [])] if x}:
            if len(item) <= 40:  # tecnologia/skill, não frase
                key = item.lower()
                techs[key] += 1
                display.setdefault(key, item)
        companies[(str(job.company_id), cname)] += 1
        for f, counter in fields.items():
            if v := getattr(job, f):
                counter[v] += 1
        sal = job.salary or {}
        top = sal.get("max") or sal.get("min")
        if isinstance(top, int | float):
            salaries.append(top / 12 if sal.get("period") == "year" else top * 160 if sal.get("period") == "hour" else top)
    return {
        "total": len(rows),
        "techs": [{"value": display[k], "count": n} for k, n in techs.most_common(60)],
        "companies": [
            {"id": cid, "name": name, "count": n} for (cid, name), n in sorted(companies.items(), key=lambda x: x[0][1].lower())
        ],
        **{f: [{"value": v, "count": n} for v, n in c.most_common()] for f, c in fields.items()},
        "salary": {"count": len(salaries), "min": min(salaries, default=None), "max": max(salaries, default=None)},
    }


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> JobDetail:
    job = await get_or_404(session, Job, job_id)
    company = await session.get(Company, job.company_id)
    score = await session.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.is_current.is_(True)))
    app = await app_service.get_for_job(session, job.id)
    sources = list((await session.scalars(select(JobSource).where(JobSource.job_id == job.id))).all())
    snapshots = list(
        (
            await session.scalars(
                select(JobSnapshot).where(JobSnapshot.job_id == job.id).order_by(JobSnapshot.captured_at)
            )
        ).all()
    )
    quality = JobQualityOut.model_validate(
        assess_job_quality(
            job,
            sources,
            snapshots,
            chance_breakdown=score.chance_breakdown if score else None,
            company_name=company.name if company else None,
        )
    )
    out: JobDetail = _job_out(job, company, score, app, JobDetail, quality)
    out.sources = [JobSourceOut.model_validate(source) for source in sources]
    out.snapshots = [
        {"id": str(s.id), "reason": s.reason, "captured_at": s.captured_at.isoformat(), "sha256": s.sha256}
        for s in snapshots
    ]
    return out


@router.get("/{job_id}/snapshots/{snapshot_id}")
async def get_snapshot(job_id: uuid.UUID, snapshot_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    snap = await get_or_404(session, JobSnapshot, snapshot_id)
    if snap.job_id != job_id:
        raise HTTPException(404)
    return {
        "description_text": snap.description_text,
        "html": snap.description_html_sanitized,
        "data": snap.data,
        "captured_at": snap.captured_at.isoformat(),
        "reason": snap.reason,
    }


@router.post("/import", response_model=TaskAccepted, status_code=202)
async def import_job(body: JobImportIn, session: SessionDep, _user: CurrentUser) -> TaskAccepted:
    tr = await enqueue(session, "ingest.import_job", **body.model_dump())
    return TaskAccepted(task_id=tr.id)


@router.post("/select")
async def select_discovered_jobs(body: SelectDiscoveredIn, session: SessionDep, _user: CurrentUser) -> dict[str, int]:
    """Move apenas vagas selecionadas em Descobrir para a fila de vagas novas."""
    job_ids = set(body.job_ids)
    jobs = list(
        (
            await session.scalars(
                select(Job).where(Job.id.in_(job_ids), Job.status == "open", Job.triage == "discovered")
            )
        ).all()
    )
    if len(jobs) != len(job_ids):
        raise HTTPException(409, "algumas vagas não estão mais aguardando seleção em Descobrir")
    for job in jobs:
        job.triage = "new"
        await audit.record(session, actor="user", action="job.selected_for_queue", entity_type="job", entity_id=job.id)
    await session.commit()
    return {"selected": len(jobs)}


@router.post("/{job_id}/feedback", status_code=204)
async def feedback(job_id: uuid.UUID, body: FeedbackIn, session: SessionDep, _user: CurrentUser) -> Response:
    await get_or_404(session, Job, job_id)
    session.add(JobFeedback(job_id=job_id, verdict=body.verdict, reason=body.reason))
    await session.commit()
    return Response(status_code=204)


@router.post("/{job_id}/rescore", status_code=202)
async def rescore(job_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> dict[str, str]:
    await get_or_404(session, Job, job_id)
    dispatch("scoring.score_job", job_id=str(job_id))
    return {"status": "queued"}


@router.post("/rescore-all", response_model=TaskAccepted, status_code=202)
async def rescore_all(session: SessionDep, _user: CurrentUser) -> TaskAccepted:
    tr = await enqueue(session, "scoring.rescore_all")
    return TaskAccepted(task_id=tr.id)


@router.post("/{job_id}/approve")
async def approve(job_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Aprova a vaga: a candidatura entra na fila e o pacote (CV sob medida, respostas) é preparado."""
    job = await get_or_404(session, Job, job_id)
    score = await session.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.is_current.is_(True)))
    app = await app_service.get_for_job(session, job.id) or await app_service.create_for_job(
        session, job, actor="user", queue_priority=score.priority if score else 0.0
    )
    if app.status in ("skipped", "blocked"):
        await app_service.transition(session, app, "discovered", actor="user", reason="aprovada manualmente")
    if app.status == "discovered":
        await app_service.transition(session, app, "queued", actor="user", reason="aprovada")
    job.triage = "approved"
    await audit.record(session, actor="user", action="job.approve", entity_type="job", entity_id=job.id)
    await session.commit()
    dispatch("applications.prepare", application_id=str(app.id))
    return {"application_id": str(app.id), "status": app.status}


@router.post("/{job_id}/skip")
async def skip(job_id: uuid.UUID, session: SessionDep, _user: CurrentUser, reason: str | None = None) -> dict[str, Any]:
    job = await get_or_404(session, Job, job_id)
    job.triage = "skipped"
    app = await app_service.get_for_job(session, job.id)
    if app and app.status in ("discovered", "queued"):
        await app_service.transition(session, app, "skipped", actor="user", reason=reason)
    await session.commit()
    return {"status": "skipped"}


@router.post("/{job_id}/restore")
async def restore(job_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Desfaz o "Não tenho interesse": a vaga volta para Novas."""
    job = await get_or_404(session, Job, job_id)
    job.triage = "new"
    app = await app_service.get_for_job(session, job.id)
    if app and app.status == "skipped":
        await app_service.transition(session, app, "discovered", actor="user", reason="descarte desfeito")
    await session.commit()
    return {"status": "new"}


# ---------- Buscas salvas ----------
def reject_linkedin_source(sources: list[str]) -> None:
    if "linkedin" in sources:
        raise HTTPException(422, "A busca automática do LinkedIn foi removida. Use sites públicos ou boards de empresas.")


@searches_router.get("", response_model=list[SavedSearchOut])
async def list_searches(session: SessionDep, _user: CurrentUser) -> list[SavedSearch]:
    return list((await session.scalars(select(SavedSearch).order_by(SavedSearch.name))).all())


def _check_cron(expr: str) -> None:
    from datetime import datetime

    from tracker.modules.jobs.tasks import cron_due

    try:
        cron_due(expr, None, datetime.now())
    except Exception as exc:
        raise HTTPException(422, f"cron inválido: {exc}") from exc


@searches_router.post("", response_model=SavedSearchOut, status_code=201)
async def create_search(body: SavedSearchIn, session: SessionDep, _user: CurrentUser) -> SavedSearch:
    reject_linkedin_source(body.sources)
    _check_cron(body.schedule_cron)
    row = SavedSearch(**body.model_dump())
    session.add(row)
    await session.commit()
    return row


@searches_router.put("/{search_id}", response_model=SavedSearchOut)
async def update_search(search_id: uuid.UUID, body: SavedSearchIn, session: SessionDep, _user: CurrentUser) -> SavedSearch:
    row = await get_or_404(session, SavedSearch, search_id)
    reject_linkedin_source(body.sources)
    _check_cron(body.schedule_cron)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await session.commit()
    return row


@searches_router.delete("/{search_id}", status_code=204)
async def delete_search(search_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> Response:
    await session.delete(await get_or_404(session, SavedSearch, search_id))
    await session.commit()
    return Response(status_code=204)


@searches_router.post("/{search_id}/run", response_model=TaskAccepted, status_code=202)
async def run_search(search_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> TaskAccepted:
    search = await get_or_404(session, SavedSearch, search_id)
    if not set(search.sources) - {"linkedin"}:
        raise HTTPException(409, "Esta busca salva só tinha LinkedIn. Abra-a em Descobrir e escolha outra fonte.")
    task = await enqueue(session, "ingest.run_saved_search", search_id=str(search_id))
    return TaskAccepted(task_id=task.id)
