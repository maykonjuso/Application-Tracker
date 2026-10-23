from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.integrations.claude.client import FakeAIClient
from tracker.modules.applications.models import Application, ApplicationEvent
from tracker.modules.applications.tasks import mark_ghosted, prepare
from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.models import Job, JobSnapshot, JobSource
from tracker.modules.jobs.normalize import RawJob
from tracker.modules.jobs.tasks import process_job
from tracker.modules.profile.models import BlocklistEntry
from tracker.modules.resumes import service as resume_service
from tracker.modules.resumes.content import baseline
from tracker.modules.scoring.models import JobScore

EXTRACTION = {
    "title": "Senior Data Engineer",
    "company_name": "Globex",
    "work_model": "remote",
    "regime": "CLT",
    "seniority": "mid",
    "requirements_must": ["Python", "Spark", "Kafka"],
    "requirements_nice": ["Airflow"],
    "language": "en",
    "apply_kind": "easy_apply",
}


def raw(ext: str = "1", title: str = "Senior Data Engineer", source: str = "greenhouse", desc: str | None = None) -> RawJob:
    return RawJob(
        source=source,
        external_id=ext,
        title=title,
        company_name="Globex Ltda",
        url=f"https://boards.greenhouse.io/globex/jobs/{ext}",
        description_text=desc or "We need Python, Spark and Kafka to build data pipelines. Remote.",
        posted_at=datetime.now(UTC) - timedelta(hours=3),
    )


async def test_upsert_dedup_and_repost(session) -> None:
    j1, new1 = await job_service.upsert_job(session, raw("1"))
    j2, new2 = await job_service.upsert_job(session, raw("1"))  # mesma fonte+id
    j3, new3 = await job_service.upsert_job(session, raw("2"))  # mesma vaga repostada
    j4, new4 = await job_service.upsert_job(session, raw("x9", source="linkedin"))  # outra fonte
    await session.commit()
    assert new1 and not new2 and not new3 and not new4
    assert j1.id == j2.id == j3.id == j4.id
    assert j1.repost_count == 1
    assert len((await session.scalars(select(JobSource).where(JobSource.job_id == j1.id))).all()) == 3
    assert len((await session.scalars(select(JobSnapshot))).all()) == 1
    assert j1.triage == "discovered"
    j5, new5 = await job_service.upsert_job(session, raw("3", title="Frontend Engineer", desc="React and TypeScript, onsite"))
    assert new5 and j5.id != j1.id


async def _seed_approved_wildcard(session) -> None:
    m = await factories.seed_master(session)
    from tracker.modules.profile.service import current_version

    v = await resume_service.create_variant(
        session,
        master_version=(await current_version(session)).version,
        master=m,
        content=baseline(m, "data_engineer", "en"),
        kind="wildcard",
        slug="data_engineer-en",
        name="Data Engineer",
        status="approved",
    )
    await session.commit()
    return v


async def test_process_job_scores_and_creates_application(session, sessionmaker, ai: FakeAIClient) -> None:
    await _seed_approved_wildcard(session)
    ai.on("job.extract", lambda req: EXTRACTION)
    job, _ = await job_service.upsert_job(session, raw("1"))
    await session.commit()
    result = await process_job(sessionmaker, str(job.id))
    assert result["quadrant"] in ("top", "volume", "bet", "discard")
    score = await session.scalar(select(JobScore).where(JobScore.job_id == job.id))
    assert score.chance_breakdown["fit_tecnico"]["value"] == pytest.approx(1.0)
    assert score.best_variant_id is not None
    await session.refresh(job)
    assert job.requirements["must"] == ["Python", "Spark", "Kafka"] and job.regime == "CLT"
    app = await session.scalar(select(Application).where(Application.job_id == job.id))
    assert app.status == "discovered" and app.channel == "external"  # URL do Greenhouse


async def test_knockout_blocklist(session, sessionmaker, ai: FakeAIClient) -> None:
    await factories.seed_master(session)
    session.add(BlocklistEntry(kind="company", value="Globex", reason="teste"))
    ai.on("job.extract", lambda req: EXTRACTION)
    job, _ = await job_service.upsert_job(session, raw("1"))
    await session.commit()
    result = await process_job(sessionmaker, str(job.id))
    assert result["quadrant"] == "blocked"
    app = await session.scalar(select(Application).where(Application.job_id == job.id))
    assert app.status == "blocked"


async def test_ai_failure_falls_back_to_heuristics(session, sessionmaker) -> None:
    await factories.seed_master(session)
    job, _ = await job_service.upsert_job(session, raw("1"))
    await session.commit()
    result = await process_job(sessionmaker, str(job.id))  # sem handler: AIError tratado
    assert "quadrant" in result


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic não instalado")
async def test_approve_prepares_resume_and_points_to_site(client: AsyncClient, session, sessionmaker, ai, dispatched) -> None:
    await _seed_approved_wildcard(session)
    ai.on("job.extract", lambda req: EXTRACTION)
    job, _ = await job_service.upsert_job(session, raw("1"))
    job.apply_kind = "easy_apply"
    await session.commit()
    await process_job(sessionmaker, str(job.id))
    r = await client.post(f"/api/v1/jobs/{job.id}/approve")
    assert r.json()["status"] == "queued"
    assert ("applications.prepare", {"application_id": r.json()["application_id"]}) in dispatched
    ai.on(
        "resume.cover_letter",
        lambda req: {"text": "I build Python and Spark pipelines at Acme.", "used_facts": ["acme"]},
    )
    out = await prepare(sessionmaker, r.json()["application_id"])
    assert out["status"] == "routed"
    app = await session.get(Application, uuid.UUID(r.json()["application_id"]))
    await session.refresh(app)
    assert app.resume_render_id is not None
    assert "site da vaga" in (app.next_action or "")  # sem robô: você se candidata com o assistente


async def test_manual_application_and_transitions(client: AsyncClient, session, dispatched) -> None:
    r = await client.post(
        "/api/v1/applications/manual",
        json={"company_name": "Initech", "title": "Backend Engineer", "url": "https://x.com/job/1"},
    )
    assert r.status_code == 201
    app_id = r.json()["id"]
    assert r.json()["status"] == "submitted" and r.json()["company_name"] == "Initech"
    r = await client.patch(f"/api/v1/applications/{app_id}", json={"status": "offer"})
    assert r.status_code == 200  # submitted → offer é permitido
    r = await client.patch(f"/api/v1/applications/{app_id}", json={"status": "queued"})
    assert r.status_code == 422
    detail = (await client.get(f"/api/v1/applications/{app_id}")).json()
    assert [e["payload"].get("to") for e in detail["timeline"] if e["kind"] == "status_change"][-1] == "offer"
    snaps = (await session.scalars(select(JobSnapshot).where(JobSnapshot.reason == "applied"))).all()
    assert len(snaps) == 1


async def test_mark_ghosted(session, sessionmaker) -> None:
    job = await factories.job(session)
    app = Application(job_id=job.id, status="submitted", last_activity_at=datetime.now(UTC) - timedelta(days=30))
    session.add(app)
    await session.commit()
    assert await mark_ghosted(sessionmaker) == 1
    await session.refresh(app)
    assert app.status == "ghosted"
    ev = await session.scalar(select(ApplicationEvent).where(ApplicationEvent.application_id == app.id))
    assert ev is not None


async def test_dashboard_today(client: AsyncClient, session) -> None:
    r = await client.get("/api/v1/dashboard/today")
    assert r.status_code == 200
    assert r.json()["onboarding_needed"] is True
    assert "agent" not in r.json() and "quota" not in r.json()
    await factories.seed_master(session)
    r = await client.get("/api/v1/dashboard/today")
    assert r.json()["onboarding_needed"] is False and "agent" not in r.json() and "quota" not in r.json()


async def test_jobs_list_and_import_endpoint(client: AsyncClient, session, dispatched) -> None:
    await factories.job(session)
    await session.commit()
    r = await client.get("/api/v1/jobs", params={"sort": "recent"})
    assert r.json()[0]["company_name"] == "Globex"
    r = await client.post("/api/v1/jobs/import", json={"url": "https://boards.greenhouse.io/globex/jobs/4001"})
    assert r.status_code == 202 and dispatched[-1][0] == "ingest.import_job"
    r = await client.post("/api/v1/jobs/import", json={})
    assert r.status_code == 422


async def test_company_create_triggers_poll(client: AsyncClient, dispatched) -> None:
    r = await client.post(
        "/api/v1/companies",
        json={"name": "Globex", "ats_kind": "greenhouse", "ats_board_token": "globex", "is_monitored": True},
    )
    assert r.status_code == 201
    assert dispatched[-1] == ("ingest.poll_company", {"company_id": r.json()["id"]})


async def test_job_closed_when_removed_from_source(session, sessionmaker, monkeypatch) -> None:
    from tracker.integrations import job_sources
    from tracker.modules.companies.service import get_or_create
    from tracker.modules.jobs.tasks import poll_company

    c = await get_or_create(session, "Globex")
    c.ats_kind, c.ats_board_token = "greenhouse", "globex"
    await session.commit()
    batches = [[raw("1"), raw("2", title="Analytics Engineer", desc="dbt and SQL")], [raw("1")]]

    async def fake_fetch(token, name, transport=None):
        return batches.pop(0)

    monkeypatch.setitem(job_sources.FETCHERS, "greenhouse", fake_fetch)
    await poll_company(sessionmaker, str(c.id))
    await poll_company(sessionmaker, str(c.id))
    jobs = {j.title: j.status for j in (await session.scalars(select(Job))).all()}
    assert jobs == {"Senior Data Engineer": "open", "Analytics Engineer": "closed"}


async def test_job_filters_and_facets(client: AsyncClient, session) -> None:
    async def add(ext: str, title: str, company: str, reqs: list[str], salary: dict | None, wm: str) -> None:
        job, _ = await job_service.upsert_job(
            session,
            RawJob(source="greenhouse", external_id=ext, title=title, company_name=company, url=f"https://x/{ext}",
                   description_text=f"Stack: {' '.join(reqs)}", salary=salary, work_model=wm),
        )  # fmt: skip
        job.requirements = {"must": reqs, "nice": []}

    await add("f1", "Data Engineer", "Acme", ["Python", "Spark"], {"min": 10000, "max": 14000, "period": "month"}, "remote")
    await add("f2", "Backend Engineer", "Globex", ["Java", "Kafka"], {"min": 120000, "max": 180000, "period": "year"}, "hybrid")
    await add("f3", "ML Engineer", "Acme", ["Python", "PyTorch"], None, "remote")
    await session.commit()

    async def titles(**params: object) -> list[str]:
        r = await client.get("/api/v1/jobs", params={"triage": "", "sort": "company", **params})
        assert r.status_code == 200, r.text
        return sorted(j["title"] for j in r.json())

    assert await titles(tech=["Python"]) == ["Data Engineer", "ML Engineer"]
    assert await titles(tech=["Python", "Spark"]) == ["Data Engineer"]
    assert await titles(work_model=["hybrid"]) == ["Backend Engineer"]
    assert await titles(salary_min=13000) == ["Backend Engineer", "Data Engineer"]  # 180k/ano = 15k/mês
    assert await titles(salary_min=14500) == ["Backend Engineer"]
    assert await titles(with_salary=True) == ["Backend Engineer", "Data Engineer"]
    facets = (await client.get("/api/v1/jobs/facets")).json()
    acme = next(c for c in facets["companies"] if c["name"] == "Acme")
    assert acme["count"] == 2 and await titles(company=[acme["id"]]) == ["Data Engineer", "ML Engineer"]
    assert facets["techs"][0] == {"value": "Python", "count": 2}
    assert facets["salary"] == {"count": 2, "min": 14000, "max": 15000}
