from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from httpx import AsyncClient

from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.normalize import RawJob
from tracker.modules.jobs.quality import assess_job_quality


async def test_job_detail_includes_quality_checkup(client: AsyncClient, session) -> None:
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="greenhouse",
            external_id="quality-checkup",
            title="Backend Engineer",
            company_name="Globex",
            url="https://boards.greenhouse.io/globex/jobs/quality-checkup",
            description_text="A" * 600,
            posted_at=datetime.now(UTC),
        ),
    )
    await session.commit()

    response = await client.get(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 200
    quality = response.json()["quality"]
    assert quality["score"] == 100
    assert quality["checks"]
    assert any(check["key"] == "integrity" and check["status"] == "ok" for check in quality["checks"])


async def test_discovered_job_search_matches_each_keyword_not_exact_phrase(client: AsyncClient, session) -> None:
    matching, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="linkedin",
            external_id="keyword-search-match",
            title="Go Developer",
            company_name="Globex",
            url="https://www.linkedin.com/jobs/view/keyword-search-match/",
            description_text="The job description offers a remote position.",
        ),
    )
    non_matching, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="linkedin",
            external_id="keyword-search-no-match",
            title="Go Engineer",
            company_name="Globex",
            url="https://www.linkedin.com/jobs/view/keyword-search-no-match/",
            description_text="A remote position with no additional details.",
        ),
    )
    await session.commit()

    response = await client.get(
        "/api/v1/jobs",
        params={"status": "open", "triage": "discovered", "q": "go job offer", "work_model": "remote"},
    )

    assert response.status_code == 200
    result_ids = {job["id"] for job in response.json()}
    assert str(matching.id) in result_ids
    assert str(non_matching.id) not in result_ids


def _job(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "title": "Backend Engineer",
        "company_id": uuid.uuid4(),
        "company_name": "Globex",
        "apply_url": None,
        "canonical_url": "https://example.com/jobs/backend",
        "description_text": "A" * 600,
        "posted_at": datetime(2026, 10, 20, tzinfo=UTC),
        "repost_count": 0,
        "extra": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_quality_score_measures_data_not_legitimacy() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    job = _job()
    snapshot = SimpleNamespace(sha256=hashlib.sha256(job.description_text.encode()).hexdigest())

    result = assess_job_quality(
        job, [SimpleNamespace(source="greenhouse")], [snapshot], company_name=job.company_name, now=now
    )

    assert result["score"] == 100
    assert result["summary"] == "Consistência alta"
    assert "não a legitimidade" in result["score_explanation"]
    checks = {check["key"]: check for check in result["checks"]}
    assert checks["integrity"]["status"] == "ok"
    assert checks["official_verification"]["status"] == "unknown"
    assert checks["company"]["status"] == "ok"


def test_quality_check_flags_stale_description_and_ai_scam_signals() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    job = _job(
        posted_at=now - timedelta(days=120),
        extra={"scam_signals": ["pede pagamento para participar"]},
    )
    snapshot = SimpleNamespace(sha256="different-hash")

    result = assess_job_quality(
        job, [SimpleNamespace(source="greenhouse")], [snapshot], company_name=job.company_name, now=now
    )

    checks = {check["key"]: check for check in result["checks"]}
    assert checks["recency"]["status"] == "risk"
    assert checks["integrity"]["status"] == "risk"
    assert checks["scam_signals"]["status"] == "risk"
    assert result["summary"] == "Sinais suspeitos exigem revisão"


def test_ghost_job_explanation_lists_evidence_thresholds_and_unknowns() -> None:
    now = datetime(2026, 10, 23, tzinfo=UTC)
    job = _job(posted_at=now - timedelta(days=12), extra={})
    breakdown = {
        "nao_fantasma": {
            "value": 1.0,
            "missing": True,
            "evidence": (
                "repostagem: 0 (sinal a partir de 2; não atingido). "
                "antiguidade: 12 dias (sinal acima de 60; não atingido). "
                "descrição ainda não analisada quanto a conteúdo genérico. "
                "informação sobre demissões recentes indisponível. "
                "heurística de triagem; não confirma se a vaga é real ou está ativa"
            ),
        }
    }

    result = assess_job_quality(job, [], [], chance_breakdown=breakdown, company_name=job.company_name, now=now)
    ghost_check = next(check for check in result["checks"] if check["key"] == "ghost_job")

    assert ghost_check["status"] == "unknown"
    assert "Nenhum dos fatores conhecidos" not in ghost_check["detail"]
    assert "repostagem: 0" in ghost_check["detail"]
    assert "acima de 60" in ghost_check["detail"]
    assert "demissões recentes indisponível" in ghost_check["detail"]
    assert "não confirma" in ghost_check["detail"]


def test_quality_check_rejects_non_web_link_and_missing_information() -> None:
    job = _job(
        canonical_url="https://example.com/jobs/1",
        apply_url="javascript:alert(1)",
        title="",
        description_text="",
        posted_at=None,
    )

    result = assess_job_quality(job, [], [], company_name=job.company_name, now=datetime(2026, 10, 23, tzinfo=UTC))

    checks = {check["key"]: check for check in result["checks"]}
    assert checks["secure_link"]["status"] == "risk"
    assert checks["source"]["status"] == "risk"
    assert checks["company"]["status"] == "risk"
    assert checks["description"]["status"] == "risk"
    assert checks["recency"]["status"] == "unknown"
    assert checks["integrity"]["status"] == "unknown"
    assert checks["ghost_job"]["status"] == "unknown"
    assert result["score"] < 100
    assert result["summary"] == "Verificações importantes pendentes"
