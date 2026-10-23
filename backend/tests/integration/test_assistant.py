from __future__ import annotations

import hashlib
import uuid

from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.core import settings_store
from tracker.integrations import job_sources
from tracker.integrations.claude.client import FakeAIClient
from tracker.modules.applications.models import Application
from tracker.modules.assistant import service
from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.models import SavedSearch
from tracker.modules.jobs.normalize import RawJob
from tracker.modules.jobs.tasks import run_saved_search
from tracker.modules.profile.models import AnswerBankEntry

H = {"Authorization": "Extension tok"}


async def test_only_selected_discoveries_enter_the_new_jobs_queue(client: AsyncClient, session) -> None:
    first, _ = await job_service.upsert_job(
        session,
        RawJob(source="greenhouse", external_id="selection-1", title="Backend Engineer", company_name="Globex",
               url="https://boards.greenhouse.io/globex/jobs/selection-1"),
    )
    second, _ = await job_service.upsert_job(
        session,
        RawJob(source="greenhouse", external_id="selection-2", title="Data Engineer", company_name="Globex",
               url="https://boards.greenhouse.io/globex/jobs/selection-2"),
    )
    await session.commit()

    response = await client.post("/api/v1/jobs/select", json={"job_ids": [str(first.id)]})

    assert response.status_code == 200
    assert response.json() == {"selected": 1}
    await session.refresh(first)
    await session.refresh(second)
    assert first.triage == "new"
    assert second.triage == "discovered"
    repeated = await client.post("/api/v1/jobs/select", json={"job_ids": [str(first.id)]})
    assert repeated.status_code == 409


async def test_linkedin_scraping_endpoints_are_removed(anon_client: AsyncClient) -> None:
    for method, path in (
        ("GET", "/api/v1/assistant/discovery/due"),
        ("GET", "/api/v1/assistant/discovery/status"),
        ("POST", f"/api/v1/assistant/discovery/{uuid.uuid4()}/complete"),
        ("POST", "/api/v1/assistant/import-list"),
    ):
        response = await anon_client.request(method, path, headers=H)
        assert response.status_code == 404


async def test_run_saved_search_returns_trackable_task(client: AsyncClient, session, dispatched) -> None:
    search = SavedSearch(
        name="Backend US",
        query="backend Go",
        filters={"country": "United States", "work_models": ["remote"]},
        sources=["boards"],
        schedule_cron="0 9 * * 1-5",
        is_active=True,
    )
    session.add(search)
    await session.commit()

    response = await client.post(f"/api/v1/saved-searches/{search.id}/run")

    assert response.status_code == 202
    assert uuid.UUID(response.json()["task_id"])
    assert dispatched[-1][0] == "ingest.run_saved_search"
    assert dispatched[-1][1]["search_id"] == str(search.id)
    assert "task_run_id" in dispatched[-1][1]


async def test_saved_search_rejects_linkedin_source(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/saved-searches",
        json={"name": "LinkedIn", "query": "backend", "sources": ["linkedin"]},
    )

    assert response.status_code == 422
    assert "LinkedIn" in response.json()["error"]["message"]


async def test_legacy_linkedin_only_search_cannot_be_run(client: AsyncClient, session) -> None:
    search = SavedSearch(
        name="Old LinkedIn search",
        query="backend",
        filters={},
        sources=["linkedin"],
        schedule_cron="0 9 * * 1-5",
        is_active=True,
    )
    session.add(search)
    await session.commit()

    response = await client.post(f"/api/v1/saved-searches/{search.id}/run")

    assert response.status_code == 409
    assert "só tinha LinkedIn" in response.json()["error"]["message"]


async def test_saved_search_accepts_and_runs_configured_ats_sources(
    client: AsyncClient, session, sessionmaker, monkeypatch
) -> None:
    response = await client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Go at Globex",
            "query": "Go",
            "sources": ["boards"],
            "schedule_cron": "0 9 * * 1-5",
            "filters": {"custom_sources": [{"name": "Globex", "url": "https://boards.greenhouse.io/globex"}]},
        },
    )
    assert response.status_code == 201
    assert "custom" in response.json()["sources"]
    assert response.json()["filters"]["custom_sources"] == [
        {"name": "Globex", "url": "https://boards.greenhouse.io/globex"}
    ]

    search = await session.get(SavedSearch, uuid.UUID(response.json()["id"]))
    assert search is not None
    calls: list[tuple[str, str]] = []

    async def fake_greenhouse(token: str, company_name: str) -> list[RawJob]:
        calls.append((token, company_name))
        return []

    monkeypatch.setitem(job_sources.FETCHERS, "greenhouse", fake_greenhouse)
    result = await run_saved_search(sessionmaker, str(search.id))
    assert calls == [("globex", "Globex")]
    assert result["custom_sources"]["0:Globex"] == {"seen": 0, "created": 0}


async def test_saved_search_rejects_unsupported_custom_site(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Unsupported source",
            "query": "Go",
            "filters": {"custom_sources": [{"name": "Careers", "url": "https://careers.example.com/jobs"}]},
        },
    )
    assert response.status_code == 422


async def _auth(session) -> None:
    await settings_store.put(session, "extension", {"token_sha256": hashlib.sha256(b"tok").hexdigest()})
    await session.commit()


def test_url_helpers() -> None:
    assert service.linkedin_job_id("https://www.linkedin.com/jobs/view/123456/?trk=x") == "123456"
    assert service.linkedin_job_id("https://www.linkedin.com/jobs/search/?currentJobId=987&keywords=a") == "987"
    assert (
        service.normalize_url("https://Boards.greenhouse.io/globex/jobs/1?gh_src=abc&utm_source=x")
        == "https://boards.greenhouse.io/globex/jobs/1"
    )
    assert service.normalize_url("https://site.com/careers/job?gh_jid=55&utm=1") == "https://site.com/careers/job?gh_jid=55"
    assert service.looks_like_job("https://empresa.gupy.io/jobs/1", "", "")
    assert service.looks_like_job("https://x.com/careers/a", "Vaga Dados", "Responsabilidades ... Requisitos ...")
    assert not service.looks_like_job("https://news.com/a", "Notícia", "Texto qualquer sobre economia")
    assert service.is_success_page("https://x.com/apply", "Thank you for applying! We will be in touch.")
    assert service.is_success_page("https://x.com/obrigado", "")


async def test_token_required_and_generation(client: AsyncClient, anon_client: AsyncClient) -> None:
    r = await anon_client.post(
        "/api/v1/assistant/page", json={"url": "https://x.com"}, headers={"Authorization": "Extension errado"}
    )
    assert r.status_code == 401
    token = (await client.post("/api/v1/assistant/token")).json()["token"]
    r = await anon_client.post(
        "/api/v1/assistant/page", json={"url": "https://x.com", "text": "nada"}, headers={"Authorization": f"Extension {token}"}
    )
    assert r.status_code == 200 and r.json()["known"] is False


async def test_page_known_job_card(anon_client: AsyncClient, session) -> None:
    await _auth(session)
    await factories.seed_master(session)
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="linkedin",
            external_id="777",
            title="Data Engineer",
            company_name="Globex",
            url="https://www.linkedin.com/jobs/view/777/",
        ),
    )
    await session.commit()
    r = await anon_client.post(
        "/api/v1/assistant/page",
        headers=H,
        json={"url": "https://www.linkedin.com/jobs/search/?currentJobId=777&x=1", "text": "..."},
    )
    assert r.json()["known"] is True and r.json()["job_id"] == str(job.id) and r.json()["company"] == "Globex"


async def test_analyze_unknown_job_uses_ai_once(anon_client: AsyncClient, session, ai: FakeAIClient) -> None:
    await _auth(session)
    await factories.seed_master(session)
    ai.on(
        "job.extract",
        lambda req: {
            "title": "ML Engineer",
            "company_name": "Initech",
            "requirements_must": ["Python"],
            "requirements_nice": [],
            "language": "en",
            "work_model": "remote",
        },
    )
    body = {
        "url": "https://careers.initech.com/jobs/ml?utm=a",
        "title": "ML Engineer",
        "text": "Requirements: Python. Responsibilities: models.",
    }
    r = await anon_client.post("/api/v1/assistant/analyze", headers=H, json=body)
    assert r.status_code == 200 and r.json()["title"] == "ML Engineer" and r.json()["score"] is not None
    r2 = await anon_client.post("/api/v1/assistant/page", headers=H, json=body)
    assert r2.json()["known"] is True  # agora é reconhecida sem IA
    assert len([c for c in ai.calls if c.task == "job.extract"]) == 1


async def test_fields_without_job_and_learn(anon_client: AsyncClient, session) -> None:
    await _auth(session)
    await factories.seed_master(session)
    fields = [
        {"key": "a", "label": "E-mail", "kind": "email"},
        {"key": "b", "label": "Expected salary", "kind": "text"},
        {"key": "c", "label": "Favorite color?", "kind": "text"},
    ]
    r = await anon_client.post(
        "/api/v1/assistant/fields", headers=H, json={"url": "https://x.com/f", "fields": fields, "allow_ai": False}
    )
    out = r.json()
    assert out["values"]["a"]["value"] == "ana@example.com" and out["values"]["a"]["source"] == "profile"
    assert "b" in out["sensitive"] and "c" not in out["values"]
    await anon_client.post(
        "/api/v1/assistant/learn", headers=H, json={"items": [{"question": "Favorite color?", "value": "Blue"}]}
    )
    r = await anon_client.post(
        "/api/v1/assistant/suggest",
        headers=H,
        json={"url": "https://x.com/f", "field": {"key": "c", "label": "Favorite color?", "kind": "text"}},
    )
    assert r.json()["value"] == "Blue" and r.json()["source"] == "bank"


async def test_submitted_unknown_page_creates_tracking(anon_client: AsyncClient, session, dispatched) -> None:
    await _auth(session)
    r = await anon_client.post(
        "/api/v1/assistant/submitted",
        headers=H,
        json={
            "url": "https://jobs.acme.com/123",
            "title": "Backend Engineer | Acme",
            "company": "Acme",
            "job_title": "Backend Engineer",
            "answers": [{"question": "Notice period?", "value": "30 days"}],
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    app = await session.scalar(select(Application))
    assert app.channel == "assisted"
    assert any(n == "ai.process_job" for n, _ in dispatched)
    assert await session.scalar(select(AnswerBankEntry).where(AnswerBankEntry.canonical_question == "Notice period?"))
    r2 = await anon_client.post("/api/v1/assistant/submitted", headers=H, json={"url": "https://jobs.acme.com/123"})
    assert r2.json()["already"] is True


def test_flow_pages_are_not_jobs() -> None:
    for url in (
        "https://stefanini.gupy.io/candidates/signin",
        "https://stefanini.gupy.io/candidates/auth/google",
        "https://stefanini.gupy.io/candidates/applications/767752082/steps/4328765764/curriculum",
        "https://empresa.com/login?next=/jobs/1",
        "https://accounts.google.com/o/oauth2/v2/auth",
    ):
        assert service.is_flow_page(url), url
        assert not service.looks_like_job(url, "Vaga", "Responsabilidades Requisitos Benefícios")
    assert not service.is_flow_page("https://stefanini.gupy.io/jobs/12571260")


async def test_gupy_flow_keeps_origin_job_and_registers_on_it(
    anon_client: AsyncClient, session, ai: FakeAIClient, dispatched
) -> None:
    """Regressão: login e etapas da Gupy viravam vagas novas e o envio caía numa cópia da vaga."""
    await _auth(session)
    await factories.seed_master(session)
    job, _ = await job_service.upsert_job(
        session,
        RawJob(source="gupy", external_id="12571260", title="Engenheiro de Dados Pleno (Azure/Databricks)",
               company_name="Stefanini", url="https://stefanini.gupy.io/jobs/12571260",
               description_text="Requisitos: Azure, Databricks"),
    )  # fmt: skip
    await session.commit()
    origin = str(job.id)

    for path in ("candidates/signin", "candidates/auth/google", "candidates/applications/1/steps/2/curriculum"):
        r = await anon_client.post(
            "/api/v1/assistant/page", headers=H,
            json={"url": f"https://stefanini.gupy.io/{path}", "text": "Responsabilidades Requisitos", "session_job_id": origin},
        )  # fmt: skip
        assert r.json()["known"] is True and r.json()["job_id"] == origin and r.json()["flow_page"] is True
    r = await anon_client.post("/api/v1/assistant/page", headers=H, json={"url": "https://stefanini.gupy.io/candidates/signin"})
    assert r.json() == {"known": False, "looks_like_job": False, "auto_analyze": False, "flow_page": True, "success_page": False}
    r = await anon_client.post(
        "/api/v1/assistant/analyze", headers=H, json={"url": "https://stefanini.gupy.io/candidates/signin"}
    )
    assert r.status_code == 422
    assert not [c for c in ai.calls if c.task == "job.extract"]  # nenhuma IA gasta em tela de login

    # sem job_id (ex.: aba nova), o envio acha a vaga pelo título no mesmo site, mesmo com outro nome de empresa
    r = await anon_client.post(
        "/api/v1/assistant/submitted", headers=H,
        json={"url": "https://stefanini.gupy.io/candidates/applications/1/steps/2/finish", "company": "Stefanini Group",
              "job_title": "Engenheiro de Dados Pleno (Azure/Databricks)"},
    )  # fmt: skip
    assert r.json()["job_id"] == origin and r.json()["status"] == "submitted"
    apps = (await session.scalars(select(Application))).all()
    assert len(apps) == 1


async def test_analyze_reuses_same_site_job_with_different_company_name(
    anon_client: AsyncClient, session, ai: FakeAIClient
) -> None:
    await _auth(session)
    await factories.seed_master(session)
    job, _ = await job_service.upsert_job(
        session,
        RawJob(source="gupy", external_id="9", title="Engenheiro de Dados Pleno", company_name="Stefanini",
               url="https://stefanini.gupy.io/jobs/9", description_text="Azure"),
    )  # fmt: skip
    await session.commit()
    ai.on("job.extract", lambda req: {"title": "Engenheiro de Dados Pleno", "company_name": "Stefanini Group",
                                      "requirements_must": [], "requirements_nice": [], "language": "pt"})  # fmt: skip
    r = await anon_client.post("/api/v1/assistant/analyze", headers=H,
                               json={"url": "https://stefanini.gupy.io/job/9/detalhes", "text": "Requisitos Azure"})  # fmt: skip
    assert r.status_code == 200 and r.json()["job_id"] == str(job.id)
    ai.on("job.extract", lambda req: {"title": "Não informado", "company_name": "X", "requirements_must": [],
                                      "requirements_nice": [], "language": "pt"})  # fmt: skip
    r = await anon_client.post("/api/v1/assistant/analyze", headers=H, json={"url": "https://x.com/pagina", "text": "algo"})
    assert r.status_code == 422


async def test_linkedin_alert_email_becomes_jobs_without_ai(session, ai: FakeAIClient, dispatched) -> None:
    from tests.unit.test_linkedin_alerts import mail
    from tracker.modules.inbox.service import process_email
    from tracker.modules.jobs.models import Job

    row = await process_email(session, ai, mail())
    await session.commit()
    assert row is not None and row.classification == "notificacao_linkedin" and "2 vaga(s)" in row.extracted["summary"]
    titles = sorted(j.title for j in (await session.scalars(select(Job))).all())
    assert titles == ["Data Engineer", "Senior Analytics Engineer"]
    assert not [c for c in ai.calls if c.task == "email.classify"]  # alerta não gasta IA
