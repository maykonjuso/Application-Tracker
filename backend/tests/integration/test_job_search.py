from __future__ import annotations

from httpx import AsyncClient

from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.normalize import RawJob


async def test_job_search_matches_programming_language_source_tags(client: AsyncClient, session) -> None:
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="remoteok",
            external_id="golang-tagged-job",
            title="Backend Engineer",
            company_name="Globex US",
            description_text="Build reliable backend services.",
            location="Remote - US",
            work_model="remote",
            raw={"tags": ["Golang", "Backend"]},
        ),
    )
    other, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="remoteok",
            external_id="golang-tagged-canada-job",
            title="Backend Engineer",
            company_name="Globex Canada",
            description_text="Build reliable backend services in Canada.",
            location="Remote - Canada",
            work_model="remote",
            raw={"tags": ["Golang", "Backend"]},
        ),
    )
    await session.commit()

    response = await client.get("/api/v1/jobs", params={"q": "go", "location": "United States", "status": "open"})

    assert response.status_code == 200
    result_ids = {row["id"] for row in response.json()}
    assert str(job.id) in result_ids
    assert str(other.id) not in result_ids


async def test_discovery_search_matches_terms_across_title_and_description(client: AsyncClient, session) -> None:
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="linkedin",
            external_id="discovery-go-offer",
            title="Go Developer",
            company_name="Empresa não informada",
            description_text="We have an exciting job offer for a backend engineer.",
            location="Remote",
            work_model="remote",
        ),
    )
    await session.commit()

    response = await client.get(
        "/api/v1/jobs",
        params={
            "status": "open",
            "triage": "discovered",
            "sort": "recent",
            "limit": 30,
            "q": "go job offer",
            "location": "Remote",
            "work_model": "remote",
        },
    )

    assert response.status_code == 200
    assert str(job.id) in {row["id"] for row in response.json()}
