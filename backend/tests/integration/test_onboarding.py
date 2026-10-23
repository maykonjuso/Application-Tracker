"""Primeiro uso por um candidato qualquer: nada pessoal embutido, padrões derivados do próprio currículo."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.integrations.claude.client import FakeAIClient
from tracker.modules.jobs.models import SavedSearch
from tracker.modules.profile import service as profile_service
from tracker.modules.profile.models import AnswerBankEntry, BlocklistEntry
from tracker.modules.resumes.content import baseline, category_key


async def test_fresh_install_asks_for_resume_and_roles(client: AsyncClient) -> None:
    r = await client.get("/api/v1/onboarding/status")
    body = r.json()
    assert r.status_code == 200 and body["completed"] is False
    assert body["checks"]["resume"]["ok"] is False and body["checks"]["preferences"]["ok"] is False
    assert body["checks"]["google"]["redirect_uris"][1].endswith("/api/v1/integrations/google/callback")
    assert (await client.post("/api/v1/onboarding/complete", json={})).status_code == 409


async def test_complete_creates_search_from_target_roles(client: AsyncClient, session, dispatched) -> None:
    await factories.seed_master(session)
    prefs = (await client.get("/api/v1/profile/preferences")).json()
    assert prefs["locations"] == []  # nada pessoal como padrão
    assert (await client.post("/api/v1/onboarding/complete", json={})).status_code == 409  # sem cargos ainda
    prefs["target_roles"] = ["Data Engineer", "Engenheiro de Dados"]
    await client.put("/api/v1/profile/preferences", json=prefs)
    r = await client.post("/api/v1/onboarding/complete", json={"seed_companies": False})
    assert r.status_code == 200 and r.json()["search_started"] is True
    ss = (await session.scalars(select(SavedSearch))).one()
    assert ss.query == "Data Engineer | Engenheiro de Dados" and set(ss.sources) == {"monitored", "boards"}
    assert dispatched[-1] == ("ingest.run_saved_search", {"search_id": str(ss.id)})
    assert (await client.get("/api/v1/onboarding/status")).json()["completed"] is True


async def test_ai_check_endpoint(client: AsyncClient, ai: FakeAIClient) -> None:
    ai.on("onboarding.check", lambda req: {"ok": True, "message": "Bem-vindo!"})
    r = await client.post("/api/v1/onboarding/test/ai")
    assert r.status_code == 200 and r.json()["message"] == "Bem-vindo!"


async def test_integrations_include_agent_configuration(client: AsyncClient) -> None:
    response = await client.get("/api/v1/integrations")
    assert response.status_code == 200
    assert isinstance(response.json()["agent"]["token_set"], bool)


async def test_defaults_come_from_the_candidates_own_resume(session) -> None:
    m = factories.master()
    created = await profile_service.seed_defaults(session, m)
    blocked = [b.value for b in (await session.scalars(select(BlocklistEntry))).all()]
    current = [e.organization_short or e.organization for e in m.experience if e.end is None]
    assert blocked == current and created["blocklist"] == len(current)  # só o empregador atual da pessoa
    edu = await session.scalar(select(AnswerBankEntry).where(AnswerBankEntry.canonical_question.like("%education%")))
    assert edu is None or edu.answer["value"] in {"Bachelor's Degree", "Master's Degree", "Postgraduate", "Doctorate",
                                                    "Associate Degree"}  # fmt: skip


def test_skill_categories_from_any_resume_show_up_in_cvs() -> None:
    assert category_key("Data Engineering") == "data_engineering"
    assert category_key("Engenharia de Dados") == "data_engineering"
    assert category_key("Ferramentas de Design") == "ferramentas_de_design"
    m = factories.master()
    m.skills = {"data_engineering": ["Spark"], "ferramentas_de_design": ["Figma"]}
    rows = baseline(m, "frontend", "pt").skill_rows
    assert [r.category for r in rows] == ["Engenharia de Dados", "Ferramentas De Design"]  # nada some


def test_wildcard_profiles_are_generic() -> None:
    from tracker.modules.resumes.content import CATEGORY_LABELS, LABELS, WILDCARD_PROFILES

    assert {"data_engineer", "frontend", "mobile", "devops_sre", "data_scientist"} <= set(WILDCARD_PROFILES)
    assert set(LABELS) == {"en", "pt"} and "frontend" in CATEGORY_LABELS
    for spec in WILDCARD_PROFILES.values():
        assert all(c in CATEGORY_LABELS for c in spec["categories"])  # type: ignore[union-attr]
