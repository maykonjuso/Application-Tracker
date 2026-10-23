from __future__ import annotations

import io
import json
import random
import re
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.integrations.claude.client import FakeAIClient
from tracker.modules.analytics import router as insights_router
from tracker.modules.analytics.tasks import weekly_report
from tracker.modules.applications.models import Application, ApplicationEvent, PendingQuestion
from tracker.modules.applications.resolver import FormField, choose_option, resolve_fields
from tracker.modules.offers import compare
from tracker.modules.scoring.calibration import calibrate, cross_val_auc
from tracker.modules.scoring.models import JobScore, ScoringWeights


def test_choose_option() -> None:
    assert choose_option(["Basic", "Advanced", "Native"], "Avançado", ["Advanced"]) == "Advanced"
    assert choose_option(["0-2 years", "3-5 years", "6+"], 4) == "3-5 years"
    assert choose_option(["0-2", "3-5", "6+"], 7) == "6+"
    assert choose_option(["Sim", "Não"], "Yes") == "Sim"
    assert choose_option(["United States (+1)", "Brazil (+55)"], "+55") == "Brazil (+55)"
    assert choose_option(["A", "B"], "Z") is None
    assert choose_option(["Data Engineer", "Software Engineer"], "Engineer") is None
    assert choose_option(["0-5 years", "5+ years"], 5) is None


async def test_resolver_rules_gaps_and_sensitive(session, ai: FakeAIClient) -> None:
    await factories.seed_master(session)
    job = await factories.job(session)
    app = Application(job_id=job.id, status="in_progress")
    session.add(app)
    await session.flush()
    ai.on("apply.answer", lambda req: {"answers": [{"key": "c", "value": "Yes", "option_index": 0, "confidence": 0.6}]})
    fields = [
        FormField(key="a", label="How many years of experience do you have with Rust?", kind="number", required=True),
        FormField(key="b", label="What is your expected salary?", kind="text", required=True),
        FormField(key="c", label="Do you enjoy hiking?", kind="radio", options=["Yes", "No"], required=True),
        FormField(key="d", label="Are you legally authorized to work in Brazil?", kind="radio", options=["Yes", "No"]),
    ]
    res = await resolve_fields(session, app, fields, 0)
    vals = {r.key: (r.value, r.resolution) for r in res.values}
    assert vals["a"] == ("0", "rule") and res.gaps == ["Rust"]  # nunca inventa anos
    assert vals["d"] == ("Yes", "rule")
    pending = {f.key for f in res.pending}
    assert pending == {"b", "c"}  # salário sem pretensão cadastrada; IA com confiança baixa
    assert not any(c.task == "apply.answer" and "salary" in str(c.user) for c in ai.calls)  # sensível nunca vai à IA
    assert len([c for c in ai.calls if c.task == "apply.answer"]) == 1  # uma chamada em lote
    pq = (await session.scalars(select(PendingQuestion))).all()
    assert {p.question_text for p in pq} == {"What is your expected salary?", "Do you enjoy hiking?"}


async def test_salary_from_preferences(client: AsyncClient, session) -> None:
    await factories.seed_master(session)
    await client.put("/api/v1/profile/preferences", json={"salary_expectation": {"CLT": {"BRL": 18000}}})
    job = await factories.job(session, regime="CLT", language="pt")
    app = Application(job_id=job.id, status="in_progress")
    session.add(app)
    await session.flush()
    res = await resolve_fields(session, app, [FormField(key="s", label="Pretensão salarial", kind="number")], 0)
    assert res.values[0].value == "18000" and res.values[0].resolution == "rule"


def test_clt_pj_comparator() -> None:
    c = compare.clt_annual(15000)
    assert c["inss"] == pytest.approx(951.63, abs=0.01)
    assert c["irrf"] == pytest.approx(2954.57, abs=0.5)
    assert compare.irrf(4800, compare.DEFAULT_PREMISES) == 0.0  # isenção até R$ 5 mil
    eq = compare.pj_equivalent_to_clt(15000)
    assert compare.pj_annual(eq)["annual_net"] == pytest.approx(c["annual_net"], rel=0.001)
    assert eq > 15000


async def test_offers_api(client: AsyncClient, session) -> None:
    job = await factories.job(session)
    app = Application(job_id=job.id, status="interviewing")
    session.add(app)
    await session.commit()
    r = await client.post(
        "/api/v1/offers",
        json={
            "application_id": str(app.id),
            "company_name": "Globex",
            "regime": "CLT",
            "base_salary": 15000,
            "benefits": {"vr": 1200},
        },
    )
    assert r.status_code == 201 and r.json()["annualized"]["pj_equivalent_invoice"] > 15000
    await client.post("/api/v1/offers", json={"company_name": "Initech", "regime": "PJ", "base_salary": 20000})
    cmp_ = (await client.post("/api/v1/offers/compare", json={})).json()
    assert [o["company"] for o in cmp_["offers"]] == ["Initech", "Globex"]  # PJ 20k > CLT 15k + VR
    await session.refresh(app)
    assert app.status == "offer"


async def test_people_crm_triggers_rescore(client: AsyncClient, session, dispatched) -> None:
    comp = await factories.company(session)
    await factories.job(session, comp)
    await session.commit()
    r = await client.post(
        "/api/v1/people",
        json={"full_name": "Bia", "company_id": str(comp.id), "kind": "referrer", "referral_status": "asked"},
    )
    assert r.status_code == 201
    assert any(n == "scoring.score_job" for n, _ in dispatched)
    assert (await client.get("/api/v1/people", params={"q": "bia"})).json()[0]["company_name"] == "Globex"


async def test_package_search_export_delete(client: AsyncClient, session) -> None:
    await factories.seed_master(session)
    job = await factories.job(session, apply_kind="external", ats_kind="workday")
    app = Application(job_id=job.id, status="queued", cover_letter="Carta.")
    session.add(app)
    await session.commit()
    pkg = (await client.get(f"/api/v1/applications/{app.id}/package")).json()
    assert pkg["cover_letter"] == "Carta." and pkg["answers"]["LinkedIn"].startswith("https://linkedin.com")
    s = (await client.get("/api/v1/search", params={"q": "globex"})).json()
    assert s["companies"][0]["name"] == "Globex" and s["applications"]
    r = await client.post("/api/v1/export")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "data/application.json" in z.namelist() and "data/oauth_credential.json" not in z.namelist()
    assert json.loads(z.read("data/application.json"))[0]["cover_letter"] == "Carta."
    assert (await client.request("DELETE", "/api/v1/data", json={"confirm": "não"})).status_code == 422
    r = await client.request("DELETE", "/api/v1/data", json={"confirm": "APAGAR TUDO"})
    assert r.status_code == 200
    assert (await client.get("/api/v1/applications")).json() == []


async def test_insights_funnel_and_weekly_fallback(client: AsyncClient, session, sessionmaker) -> None:
    await factories.seed_master(session)
    now = datetime.now(UTC)
    for i, status in enumerate(["submitted", "interviewing", "rejected", "offer"]):
        job = await factories.job(session, title=f"Data Engineer {i}", normalized_title=f"data engineer {i}")
        app = Application(job_id=job.id, status=status, submitted_at=now - timedelta(days=2))
        session.add(app)
        await session.flush()
        if status in ("interviewing", "offer"):
            session.add(ApplicationEvent(application_id=app.id, kind="status_change", payload={"to": "interviewing"}))
    await session.commit()
    f = (await client.get("/api/v1/insights/funnel")).json()
    assert f["submitted"] == 4 and f["interviewing"] == 2 and f["responded"] == 3
    perf = (await client.get("/api/v1/insights/performance")).json()
    assert perf["by_channel"][0]["sent"] == 4
    data = await weekly_report(sessionmaker)  # sem IA: relatório determinístico
    assert len(data["recommendations"]) == 3
    stored = (await client.get("/api/v1/insights/weekly-report")).json()
    assert stored["headline"].startswith("4 candidaturas")


async def test_calibration_proposes_inactive_weights(session) -> None:
    rnd = random.Random(1)
    old = datetime.now(UTC) - timedelta(days=40)
    for i in range(120):
        job = await factories.job(session, title=f"Job {i}", normalized_title=f"job {i}")
        fit = rnd.random()
        fresh = rnd.random()
        positive = rnd.random() < (0.1 + 0.7 * fresh)  # frescor é o que importa nestes dados
        session.add(Application(job_id=job.id, status="interviewing" if positive else "rejected", submitted_at=old))
        session.add(
            JobScore(
                job_id=job.id,
                weights_version=1,
                master_version=1,
                quadrant="top",
                is_current=True,
                chance_breakdown={"fit_tecnico": {"value": fit}, "frescor": {"value": fresh}},
            )
        )
    await session.commit()
    result = await calibrate(session)
    await session.commit()
    assert result["status"] == "proposed" and result["n"] == 120
    assert result["weights"]["frescor"] > result["weights"]["fit_tecnico"]
    w = await session.scalar(select(ScoringWeights).where(ScoringWeights.source == "calibrated"))
    assert w.is_current is False  # nunca ativa sozinho


def test_cross_val_auc_signal() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    x = rng.random((200, 2))
    y = (x[:, 0] + 0.1 * rng.random(200) > 0.55).astype(float)
    assert cross_val_auc(x, y) > 0.85


async def test_github_import(client: AsyncClient, session) -> None:
    await factories.seed_master(session)
    repos = [
        {
            "name": "etl-kit",
            "description": "Toolkit de ETL em Python",
            "language": "Python",
            "stargazers_count": 3,
            "html_url": "https://github.com/ana-teste/etl-kit",
            "topics": ["etl"],
            "fork": False,
        },
        {"name": "fork-x", "description": "x", "fork": True},
    ]
    insights_router._gh_transport = httpx.MockTransport(lambda req: httpx.Response(200, json=repos))
    try:
        listed = (await client.get("/api/v1/profile/github/repos")).json()
        assert [r["name"] for r in listed] == ["etl-kit"]
        r = await client.post("/api/v1/profile/github/import", json={"repos": listed})
        assert r.json()["added"] == ["etl-kit"]
        master = (await client.get("/api/v1/profile/master")).json()
        assert any(p["id"] == "etl-kit" for p in master["data"]["projects"]) and master["version"] == 2
    finally:
        insights_router._gh_transport = None


async def test_ai_answers_respect_question_intent(session, ai) -> None:
    """Pergunta condicional que não vale para você e pedido de matrícula nunca viram um texto de apresentação."""
    from tracker.modules.applications.resolver import resolve_core

    await factories.seed_master(session)
    job = await factories.job(session, title="Pessoa Desenvolvedora Backend Python")
    seen: dict[str, str] = {}

    def answer(req):
        seen["system"] = req.system
        seen["user"] = req.user
        return {
            "answers": [
                {"key": "m", "value": "Na MyBroker, projetei uma plataforma…", "confidence": 0.95},
                {"key": "g", "value": "Sou desenvolvedor backend Python…", "confidence": 0.85},
                {"key": "x", "value": "", "confidence": 0.0},
            ]
        }

    ai.on("apply.answer", answer)
    fields = [
        FormField(key="m", label="5. Caso você seja colaborador do GB, informe a sua matrícula.", kind="textarea"),
        FormField(key="g", label="Caso você já tenha trabalhado conosco, conte como foi", kind="textarea"),
        FormField(key="x", label="Quem indicou você?", kind="text"),
    ]
    res, usable = await resolve_core(session, fields, job, allow_ai=True)
    filled = {r.key for r in res.values}
    assert "m" not in filled  # matrícula: só você responde (nem vai para a IA)
    assert 'key="m"' not in seen["user"]
    assert "g" not in filled  # condicional com confiança < 0,9: fica como sugestão para você revisar
    assert "x" not in filled and "x" not in usable  # vazio = não se aplica
    assert "Desenvolvedor" in seen["system"] and "condicional" in seen["system"].lower()  # prompt v3 em uso
    assert "Pessoa Desenvolvedora Backend Python" in seen["user"]


async def test_salary_experience_and_birth_rules(session) -> None:
    """Pretensão já respondida vale para "expectativa salarial"; tempo de experiência na área; nascimento do perfil."""
    from tracker.modules.applications.resolver import resolve_core
    from tracker.modules.profile import service as profile_service
    from tracker.modules.profile.models import AnswerBankEntry

    await factories.seed_master(session)
    session.add(
        AnswerBankEntry(
            canonical_question="Pretensão salarial", field_kind="text", answer={"value": "12000"}, source="user", sensitive=True
        )
    )
    prefs = await profile_service.get_preferences(session)
    await profile_service.put_preferences(session, prefs.model_copy(update={"birthplace": "Brasília, DF"}))
    job = await factories.job(session, language="pt")
    await session.flush()
    fields = [
        FormField(key="s", label="1. Qual sua expectativa salarial (para o modelo CLT)?", kind="textarea", required=True),
        FormField(
            key="y", label="3. Qual é o seu tempo de experiência CLT na área de tecnologia?", kind="textarea", required=True
        ),
        FormField(key="b", label="4. Qual é a sua cidade e estado de nascimento?", kind="textarea", required=True),
        FormField(key="d", label="Data de nascimento", kind="text"),
    ]
    res, _ = await resolve_core(session, fields, job, allow_ai=False)
    vals = {r.key: r.value for r in res.values}
    assert vals["s"] == "12000"
    assert vals["y"].endswith("anos") and int(vals["y"].split()[0]) >= 1
    assert vals["b"] == "Brasília, DF"
    assert "d" not in vals and "d" in {f.key for f in res.pending}  # sem cadastro: fica para você (nunca para a IA)


async def test_current_job_block_from_master(session) -> None:
    """Bloco "atual ou último trabalho": empresa, cargo, início, "Até" vazio e "trabalho atual" marcado, sem IA."""
    from tracker.modules.applications.resolver import resolve_core

    await factories.seed_master(session)
    job = await factories.job(session, title="Data Engineer Pleno")
    fields = [
        FormField(key="y", label="Em que ano foi seu primeiro emprego ou trabalho remunerado?", kind="number"),
        FormField(key="c", label="Empresa *", kind="text", required=True),
        FormField(key="p", label="Posição *", kind="text", required=True),
        FormField(key="d", label="De", kind="month"),
        FormField(key="a", label="Até", kind="month"),
        FormField(key="x", label="Este é o meu trabalho atual", kind="checkbox"),
        FormField(key="s", label="Atual ou último salário", kind="text"),
    ]
    res, _ = await resolve_core(session, fields, job, allow_ai=False)
    vals = {r.key: r.value for r in res.values}
    assert vals["y"].isdigit() and vals["c"] and vals["p"] and re.fullmatch(r"\d{4}-\d{2}", vals["d"])
    assert vals["x"] is True and "a" not in vals
    assert "s" not in vals  # salário atual: fica para você
