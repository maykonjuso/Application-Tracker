"""Tarefas de vagas: extração por IA, coleta de fontes, buscas salvas, importação por URL/texto."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from tracker.core import settings_store
from tracker.core.tasks import dispatch
from tracker.integrations import job_sources
from tracker.integrations.claude.client import AIError, AIRequest, get_ai, load_prompt
from tracker.modules.companies.models import Company
from tracker.modules.jobs import normalize as nz
from tracker.modules.jobs import service
from tracker.modules.jobs.models import Job, JobSource, SavedSearch
from tracker.modules.jobs.schemas import JobNormalized
from tracker.modules.platform.models import IntegrationState
from tracker.modules.profile import service as profile_service
from tracker.workers.celery_app import celery_app
from tracker.workers.runtime import SM, progress, run, tracked

log = logging.getLogger(__name__)


async def extract(sm: SM, job_id: str) -> None:
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        company = await s.get(Company, job.company_id)
        user = (
            f"<job>\nTítulo: {job.title}\nEmpresa: {company.name if company else ''}\n"
            f"Local: {job.location or ''}\nURL: {job.canonical_url or ''}\n\n{job.description_text[:15000]}\n</job>"
        )
    res = await get_ai(sm).structured(
        AIRequest(
            task="job.extract",
            system=load_prompt("job.extract"),
            user=user,
            effort="low",
            max_tokens=4000,
            entity_type="job",
            entity_id=uuid.UUID(job_id),
            cache_ttl_days=90,
        ),
        JobNormalized,
    )
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        service.apply_extraction(job, res.output)
        await s.commit()


async def process_job(sm: SM, job_id: str) -> dict[str, Any]:
    """Pipeline pós-coleta: extração (IA) → avaliação → candidatura."""
    from tracker.modules.scoring.tasks import score_and_route

    try:
        await extract(sm, job_id)
    except AIError as exc:
        log.warning("extração indisponível para %s: %s (seguindo com heurísticas)", job_id, exc)
    return await score_and_route(sm, job_id)


@celery_app.task(name="ai.process_job")
def process_job_task(job_id: str) -> dict[str, Any]:
    return run(lambda sm: process_job(sm, job_id))


async def ingest_raw(sm: SM, raws: list[nz.RawJob], saved_search_id: Any = None) -> dict[str, int]:
    created = 0
    seen_ids: list[str] = []
    for raw in raws:
        async with sm() as s:
            job, is_new = await service.upsert_job(s, raw, saved_search_id)
            await s.commit()
            seen_ids.append(str(job.id))
            if is_new:
                created += 1
                dispatch("ai.process_job", job_id=str(job.id))
    return {"seen": len(raws), "created": created}


async def _health(sm: SM, name: str, error: str | None) -> None:
    async with sm() as s:
        st = await s.scalar(select(IntegrationState).where(IntegrationState.name == name))
        if st is None:
            st = IntegrationState(name=name)
            s.add(st)
        now = datetime.now(UTC)
        if error:
            st.last_error_at, st.last_error = now, error[:500]
            st.consecutive_errors = (st.consecutive_errors or 0) + 1
        else:
            st.last_success_at, st.consecutive_errors = now, 0
        await s.commit()


async def poll_company(sm: SM, company_id: str) -> dict[str, Any]:
    async with sm() as s:
        c = await s.get(Company, uuid.UUID(company_id))
        if c is None or not c.ats_kind or not c.ats_board_token:
            return {"skipped": True}
        ats, token, name = c.ats_kind, c.ats_board_token, c.name
        # local vazio/genérico ("Remote") só é descartado se a empresa for sabidamente estrangeira
        hq = (c.hq_location or "").lower()
        brazilian = not hq or "brasil" in hq or "brazil" in hq
    fetcher = job_sources.FETCHERS.get(ats)
    if fetcher is None:
        return {"skipped": True}
    try:
        raws = await fetcher(token, name)
    except (httpx.HTTPError, ValueError) as exc:
        await _health(sm, f"source:{ats}", str(exc))
        raise
    await _health(sm, f"source:{ats}", None)
    from tracker.modules.jobs import relevance

    async with sm() as s:
        terms = await relevance.terms(s)
        international = (await profile_service.get_preferences(s)).international
    # sem IA: só vagas dos seus cargos-alvo, sem estágio/gestão e que dá para fazer do Brasil
    relevant = relevance.filter_relevant(raws, terms, brazilian, international)
    result = await ingest_raw(sm, relevant)
    result["fetched"] = len(raws)
    async with sm() as s:
        c = await s.get(Company, uuid.UUID(company_id))
        assert c is not None
        c.last_polled_at = datetime.now(UTC)
        ext_ids = {r.external_id for r in raws}
        # vagas que sumiram da fonte → encerradas (RF-VAG-10)
        srcs = (
            await s.execute(
                select(JobSource, Job)
                .join(Job, Job.id == JobSource.job_id)
                .where(Job.company_id == c.id, JobSource.source == ats)
            )
        ).all()
        # fonte voltou vazia mas tínhamos várias abertas: provável falha/mudança da página, não fecha nada
        open_count = sum(1 for _, job in srcs if job.status == "open")
        if not raws and open_count >= 3:
            result["suspicious_empty"] = True
            srcs = []
        for src, job in srcs:
            if src.external_id not in ext_ids and job.status == "open":
                job.status = "closed"
        await s.commit()
    return result


@celery_app.task(name="ingest.poll_company")
def poll_company_task(company_id: str) -> dict[str, Any]:
    return run(lambda sm: poll_company(sm, company_id))


@celery_app.task(name="ingest.poll_monitored_companies")
def poll_monitored() -> int:
    async def body(sm: SM) -> int:
        async with sm() as s:
            if not await settings_store.flag(s, "job_sources"):
                return 0
            ids = (
                await s.scalars(select(Company.id).where(Company.is_monitored.is_(True), Company.ats_board_token.is_not(None)))
            ).all()
        for cid in ids:
            dispatch("ingest.poll_company", company_id=str(cid))
        return len(ids)

    return run(body)


def matches_query(job: Job, query: str, filters: dict[str, Any]) -> bool:
    text = f"{job.title} {job.description_text}".lower()
    terms = [t.strip().lower() for t in query.replace(" OR ", "|").split("|") if t.strip()]
    if terms and not any(all(w in text for w in t.split()) for t in terms):
        return False
    if filters.get("work_models") and job.work_model not in filters["work_models"]:
        return False
    return not (filters.get("seniorities") and job.seniority and job.seniority not in filters["seniorities"])


def matches_saved_search_filters(raw: nz.RawJob, filters: dict[str, Any]) -> bool:
    from tracker.modules.jobs import relevance

    country = str(filters.get("country") or "").strip().casefold()
    if country and not relevance.location_matches_country(raw.location, country):
        return False
    work_models = filters.get("work_models") or []
    if work_models:
        work_model = raw.work_model or nz.detect_work_model(
            f"{raw.title}\n{raw.location or ''}\n{raw.description_text or ''}"
        )
        return work_model in work_models
    return True


def _cron_field(spec: str, value: int, lo: int, hi: int) -> bool:
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/")
            step = int(step_s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def cron_matches(expr: str, dt: datetime) -> bool:
    minute, hour, dom, month, dow = expr.split()
    return (
        _cron_field(minute, dt.minute, 0, 59)
        and _cron_field(hour, dt.hour, 0, 23)
        and _cron_field(dom, dt.day, 1, 31)
        and _cron_field(month, dt.month, 1, 12)
        and _cron_field(dow, (dt.weekday() + 1) % 7, 0, 6)
    )


def cron_due(expr: str, last: datetime | None, now: datetime) -> bool:
    """O cron (min hora dia mês dia-semana, fuso do usuário) disparou entre `last` e `now`?"""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from tracker.core.config import get_settings

    if len(expr.split()) != 5:
        raise ValueError("cron precisa de 5 campos")
    cron_matches(expr, now)  # valida a sintaxe
    if last is None:
        return True
    tz = ZoneInfo(get_settings().user_timezone)
    t = last.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = now.astimezone(tz)
    if (end - t) > timedelta(days=14):
        return True
    while t <= end:
        if cron_matches(expr, t):
            return True
        t += timedelta(minutes=1)
    return False


async def run_saved_search(sm: SM, search_id: str) -> dict[str, Any]:
    async with sm() as s:
        ss = await s.get(SavedSearch, uuid.UUID(search_id))
        assert ss is not None
        sources = list(ss.sources)
        query, filters = ss.query, dict(ss.filters)
        ss.last_run_at = datetime.now(UTC)
        await s.commit()
    result: dict[str, Any] = {}
    if "monitored" in sources:
        async with sm() as s:
            ids = (await s.scalars(select(Company.id).where(Company.is_monitored.is_(True)))).all()
        for cid in ids:
            dispatch("ingest.poll_company", company_id=str(cid))
        result["monitored_companies"] = len(ids)
    if "boards" in sources:
        from tracker.modules.jobs import relevance

        wanted = filters.get("boards") or list(job_sources.BOARDS)
        terms = relevance.split_terms(query)
        boards: dict[str, Any] = {}
        for name in wanted:
            fetch = job_sources.BOARDS.get(name)
            if fetch is None:
                continue
            try:
                raws_by_id: dict[tuple[str, str], nz.RawJob] = {}
                for search_query in relevance.search_variants(query):
                    for raw in await fetch(search_query):
                        raws_by_id[(raw.source, raw.external_id)] = raw
                raws = relevance.filter_relevant(
                    list(raws_by_id.values()),
                    terms,
                    brazilian_company=None if filters.get("country") else False,
                    search_in_description=True,
                )
                raws = [raw for raw in raws if matches_saved_search_filters(raw, filters)]
            except (httpx.HTTPError, ValueError) as exc:
                await _health(sm, f"board:{name}", str(exc))
                boards[name] = f"erro: {str(exc)[:80]}"
                continue
            await _health(sm, f"board:{name}", None)
            boards[name] = await ingest_raw(sm, raws, saved_search_id=uuid.UUID(search_id))
        result["boards"] = boards
    if "custom" in sources:
        from tracker.modules.jobs import relevance

        custom_results: dict[str, Any] = {}
        for index, source in enumerate(filters.get("custom_sources") or []):
            name = str(source["name"]).strip()
            parsed = job_sources.parse_ats_url(str(source["url"]))
            if parsed is None:
                raise ValueError(f"URL inválida ou fonte não suportada: {source['url']}")
            ats, token, _ = parsed
            fetcher = job_sources.FETCHERS.get(ats)
            if fetcher is None:
                raise ValueError(f"Fonte personalizada não suportada: {ats}")
            key = f"{index}:{name}"
            health_name = f"custom:{ats}:{index}"
            try:
                raws = await fetcher(token, name)
                raws = relevance.filter_relevant(
                    raws,
                    relevance.split_terms(query),
                    brazilian_company=None if filters.get("country") else False,
                    search_in_description=True,
                )
                raws = [raw for raw in raws if matches_saved_search_filters(raw, filters)]
            except (httpx.HTTPError, ValueError) as exc:
                await _health(sm, health_name, str(exc))
                custom_results[key] = f"erro: {str(exc)[:80]}"
                continue
            await _health(sm, health_name, None)
            custom_results[key] = await ingest_raw(sm, raws, saved_search_id=uuid.UUID(search_id))
        result["custom_sources"] = custom_results
    async with sm() as s:
        jobs = (await s.scalars(select(Job).where(Job.status == "open"))).all()
        matched = [j for j in jobs if matches_query(j, query, filters)]
        result["matched_existing"] = len(matched)
    return result


async def _run_saved_search_task(sm: SM, task_run_id: str | None, search_id: str) -> dict[str, Any]:
    return await run_saved_search(sm, search_id)


celery_app.task(name="ingest.run_saved_search")(tracked("ingest.run_saved_search")(_run_saved_search_task))


@celery_app.task(name="ingest.run_due_saved_searches")
def run_due_saved_searches() -> int:
    async def body(sm: SM) -> int:
        now = datetime.now(UTC)
        async with sm() as s:
            rows = (await s.scalars(select(SavedSearch).where(SavedSearch.is_active.is_(True)))).all()
            due = [str(r.id) for r in rows if cron_due(r.schedule_cron, r.last_run_at, now)]
        for sid in due:
            dispatch("ingest.run_saved_search", search_id=sid)
        return len(due)

    return run(body)


async def fetch_page_text(url: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, str]:
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        transport=transport,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
    ) as c:
        r = await c.get(url)
        r.raise_for_status()
    return r.text, nz.html_to_text(r.text)


async def _import(
    sm: SM,
    task_run_id: str | None,
    url: str | None = None,
    text: str | None = None,
    company_name: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    raws: list[nz.RawJob] = []
    if url and (parsed := job_sources.parse_ats_url(url)):
        ats, token, job_ext = parsed
        await progress(sm, task_run_id, 20, f"buscando na API pública ({ats})")
        name = company_name or token.replace("-", " ").title()
        all_jobs = await job_sources.FETCHERS[ats](token, name)
        raws = [r for r in all_jobs if not job_ext or r.external_id == job_ext or (r.url or "").endswith(job_ext)]
        async with sm() as s:
            from tracker.modules.companies.service import get_or_create

            c = await get_or_create(s, name)
            c.ats_kind, c.ats_board_token = ats, token
            await s.commit()
    if not raws:
        html = None
        if url and not text:
            await progress(sm, task_run_id, 20, "baixando a página da vaga")
            html, text = await fetch_page_text(url)
        assert text
        await progress(sm, task_run_id, 40, "extraindo dados com IA")
        res = await get_ai(sm).structured(
            AIRequest(
                task="job.extract",
                system=load_prompt("job.extract"),
                user=f"<job>\nURL: {url or ''}\n{text[:15000]}\n</job>",
                effort="low",
                max_tokens=4000,
            ),
            JobNormalized,
        )
        x = res.output
        ext = hashlib.sha256((url or text).encode()).hexdigest()[:24]
        apply_kind, ats_kind = nz.detect_apply_kind(url, text)
        raws = [
            nz.RawJob(
                source="linkedin" if url and "linkedin.com" in url else "manual",
                external_id=ext,
                title=title or x.title,
                company_name=company_name or x.company_name,
                url=url,
                location=x.location,
                description_html=html,
                description_text=text,
                apply_url=url,
                apply_kind=x.apply_kind if x.apply_kind != "unknown" else apply_kind,
                ats_kind=ats_kind,
            )
        ]
        async with sm() as s:
            job, _ = await service.upsert_job(s, raws[0])
            service.apply_extraction(job, x)
            await s.commit()
            job_id = str(job.id)
        from tracker.modules.scoring.tasks import score_and_route

        await score_and_route(sm, job_id)
        return {"job_ids": [job_id]}
    result = await ingest_raw(sm, raws)
    async with sm() as s:
        ids = [
            str(i)
            for i in (
                await s.scalars(select(JobSource.job_id).where(JobSource.external_id.in_([r.external_id for r in raws])))
            ).all()
        ]
    return {**result, "job_ids": ids}


celery_app.task(name="ingest.import_job")(tracked("ingest.import_job")(_import))
