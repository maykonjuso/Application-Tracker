from __future__ import annotations

import hashlib

from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.core import settings_store
from tracker.integrations.claude.client import FakeAIClient
from tracker.modules.applications.models import Application
from tracker.modules.assistant import service
from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.normalize import RawJob
from tracker.modules.profile.models import AnswerBankEntry

H = {"Authorization": "Extension tok"}


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


async def test_linkedin_list_import_then_enrich_on_open(anon_client: AsyncClient, session, ai: FakeAIClient, dispatched) -> None:
    """Sem robô: a lista que você vê no LinkedIn vira vagas; ao abrir uma, a descrição completa a vaga."""
    await _auth(session)
    await factories.seed_master(session)
    body = {"source": "linkedin", "jobs": [
        {"external_id": "4011111111", "title": "Data Engineer", "company": "Initech", "location": "Brazil (Remote)", "easy_apply": True},
        {"external_id": "4022222222", "title": "Backend Engineer", "company": "Hooli", "location": "São Paulo"},
        {"external_id": "4033333333", "title": "", "company": "X"},  # incompleta: ignorada
    ]}  # fmt: skip
    r = await anon_client.post("/api/v1/assistant/import-list", headers=H, json=body)
    assert r.json() == {"created": 2, "existing": 0}
    assert [d for d in dispatched if d[0] == "ai.process_job"].__len__() == 2
    r = await anon_client.post("/api/v1/assistant/import-list", headers=H, json=body)
    assert r.json() == {"created": 0, "existing": 2}

    page = {"url": "https://www.linkedin.com/jobs/view/4011111111/", "title": "Data Engineer | Initech",
            "text": "Data Engineer · Initech. About the job. Responsibilities: build pipelines. Requirements: Python, Spark. " * 10}  # fmt: skip
    r = await anon_client.post("/api/v1/assistant/page", headers=H, json=page)
    assert r.json()["known"] is True and r.json()["needs_details"] is True
    ai.on("job.extract", lambda req: {"title": "Data Engineer", "company_name": "Initech", "requirements_must": ["Python", "Spark"],
                                      "requirements_nice": [], "language": "en", "work_model": "remote"})  # fmt: skip
    r = await anon_client.post("/api/v1/assistant/analyze", headers=H, json=page)
    assert r.status_code == 200 and r.json()["requirements"]["must"] == ["Python", "Spark"]
    r = await anon_client.post("/api/v1/assistant/page", headers=H, json=page)
    assert r.json()["needs_details"] is False  # completa: não pede de novo


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


async def _fields(anon_client: AsyncClient, fields: list[dict]) -> dict:
    r = await anon_client.post(
        "/api/v1/assistant/fields", headers=H, json={"url": "https://x.com/f", "fields": fields, "allow_ai": False}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_correction_overrides_profile_next_time(anon_client: AsyncClient, session) -> None:
    """Você corrigiu um campo que veio do perfil: a próxima vaga já vem com a sua resposta."""
    await _auth(session)
    await factories.seed_master(session)
    field = {"key": "a", "label": "Phone", "kind": "tel"}
    before = (await _fields(anon_client, [field]))["values"]["a"]
    assert before["source"] == "profile"
    r = await anon_client.post(
        "/api/v1/assistant/learn",
        headers=H,
        json={"items": [{"question": "Phone", "value": "+55 61 90000-0000", "corrected": True}]},
    )
    assert r.json()["saved"] == 1
    after = (await _fields(anon_client, [field]))["values"]["a"]
    assert after == {"value": "+55 61 90000-0000", "source": "bank", "confidence": 1.0}


async def test_learn_skips_option_text_as_question(anon_client: AsyncClient, session) -> None:
    await _auth(session)
    r = await anon_client.post(
        "/api/v1/assistant/learn",
        headers=H,
        json={
            "items": [{"question": "Não", "value": "Não"}, {"question": "Parda", "value": "x", "options": ["Parda", "Branca"]}]
        },
    )
    assert r.json()["saved"] == 0


async def test_current_job_questions_and_consent(anon_client: AsyncClient, session) -> None:
    await _auth(session)
    await factories.seed_master(session)
    await settings_store.put(
        session,
        "current_compensation",
        {"company": "Sicoob", "regime": "CLT", "monthly_gross": 6000, "meal_monthly": 2100, "non_monetary": ["Plano de Saúde"]},
    )
    session.add(
        AnswerBankEntry(canonical_question="Pretensão salarial", field_kind="text", answer={"value": "12000"}, source="user")
    )
    await session.commit()
    out = await _fields(
        anon_client,
        [
            {"key": "loc", "label": "Which city/state do you currently reside in? Qual cidade/estado reside atualmente?"},
            {"key": "co", "label": "Confirme o nome da empresa em que você trabalha atualmente", "required": True},
            {"key": "cur", "label": "Caso se sinta confortável, compartilhe seu salário atual ou ultimo e benefícios!", "required": True},
            {"key": "cur_opt", "label": "If you feel comfortable, please share your current or last salary and benefits"},
            {"key": "exp", "label": "Qual sua pretensão salarial para vagas CLT?", "required": True},
            {"key": "ok", "label": "Concordo que meus dados pessoais sejam processados para fins de recrutamento e seleção.",
             "kind": "checkbox", "required": True},
            {"key": "mkt", "label": "Autorizo a empresa a enviar informações sobre novas oportunidades de emprego",
             "kind": "checkbox"},
        ],
    )  # fmt: skip
    v = {k: x["value"] for k, x in out["values"].items()}
    assert v["co"] == "Sicoob"
    assert v["cur"].startswith("CLT R$ 6.000 bruto/mês") and "12000" not in v["cur"]  # nunca a pretensão
    assert "cur_opt" not in v  # opcional: em branco de propósito
    assert v["exp"] == "12000"
    assert v["ok"] is True and "mkt" not in v
    assert "," in v["loc"]  # cidade + UF, não só a cidade
