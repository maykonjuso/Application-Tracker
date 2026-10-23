from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.factories import FIXTURES
from tracker.integrations import job_sources
from tracker.modules.applications import state_machine
from tracker.modules.jobs import normalize as nz
from tracker.modules.jobs.tasks import cron_due, matches_query
from tracker.modules.scoring import features as F


def _transport(name: str) -> httpx.MockTransport:
    data = json.loads((FIXTURES / "ats" / f"{name}.json").read_text())
    return httpx.MockTransport(lambda req: httpx.Response(200, json=data))


async def test_greenhouse_parsing() -> None:
    jobs = await job_sources.greenhouse("globex", "Globex", _transport("greenhouse"))
    assert [j.external_id for j in jobs] == ["4001", "4002"]
    assert "Python" in (jobs[0].description_text or "")
    assert jobs[0].ats_kind == "greenhouse" and jobs[0].posted_at.year == 2026


async def test_lever_parsing() -> None:
    jobs = await job_sources.lever("initech", "Initech", _transport("lever"))
    assert jobs[0].work_model == "remote"
    assert jobs[0].salary["currency"] == "USD" and jobs[0].salary["period"] == "year"
    assert "PostgreSQL" in jobs[0].description_text


async def test_ashby_parsing() -> None:
    jobs = await job_sources.ashby("umbrella", "Umbrella", _transport("ashby"))
    assert jobs[0].salary == {"min": 8000, "max": 11000, "currency": "USD", "period": "month", "estimated": False}


async def test_jobicy_go_keyword_uses_supported_golang_tag() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "jobTitle": "Go Engineer",
                        "companyName": "Globex",
                        "url": "https://jobicy.com/jobs/123-go-engineer",
                        "jobDescription": "<p>Build Go services.</p>",
                    }
                ]
            },
        )

    jobs = await job_sources.board_jobicy("go", httpx.MockTransport(respond))

    assert len(requests) == 1
    assert requests[0].url.path == "/api/jobs"
    assert dict(requests[0].url.params) == {"count": "50", "tag": "golang"}
    assert jobs[0].external_id == "123" and jobs[0].source == "jobicy"
    assert jobs[0].title == "Go Engineer" and "Build Go services." in (jobs[0].description_text or "")


def test_parse_ats_url() -> None:
    assert job_sources.parse_ats_url("https://boards.greenhouse.io/globex/jobs/4001") == (
        "greenhouse",
        "globex",
        "4001",
    )
    assert job_sources.parse_ats_url("https://jobs.lever.co/initech/abc-123") == ("lever", "initech", "abc-123")
    assert job_sources.parse_ats_url("https://empresa.gupy.io/jobs/123") == ("gupy", "empresa", "123")
    assert job_sources.parse_ats_url("https://www.linkedin.com/jobs/view/1") is None
    assert job_sources.parse_ats_url("https://evil.example/jobs.lever.co/initech") is None
    assert job_sources.parse_ats_url("http://jobs.lever.co/initech") is None
    assert job_sources.parse_ats_url("https://user@jobs.lever.co/initech") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vaga 100% remota, CLT", ("remote", "CLT")),
        ("Modelo híbrido em Brasília, contrato PJ", ("hybrid", "PJ")),
        ("On-site in Austin", ("onsite", None)),
    ],
)
def test_detect_work_model_and_regime(text: str, expected: tuple) -> None:
    assert (nz.detect_work_model(text), nz.detect_regime(text)) == expected


def test_detect_salary_and_language() -> None:
    assert nz.detect_salary("Faixa R$ 15.000 - R$ 20.000 CLT") == {
        "min": 15000.0,
        "max": 20000.0,
        "currency": "BRL",
        "period": "month",
        "estimated": False,
    }
    assert nz.detect_salary("USD 120k - 150k per year")["period"] == "year"
    assert nz.detect_language("Buscamos pessoa com experiência em Python para nossa empresa") == "pt"
    assert nz.detect_language("We are looking for an engineer with experience in Python") == "en"


def test_detect_seniority_and_apply_kind() -> None:
    assert nz.detect_seniority("Engenheiro de Dados Pleno") == "mid"
    assert nz.detect_seniority("Sr. Backend Engineer") == "senior"
    assert nz.detect_apply_kind("https://empresa.gupy.io/jobs/1") == ("external", "gupy")
    assert nz.detect_apply_kind("https://www.linkedin.com/jobs/view/1", "Candidatura simplificada") == (
        "easy_apply",
        None,
    )


def test_sanitize_html_strips_scripts() -> None:
    out = nz.sanitize_html('<p onclick="x()">oi</p><script>alert(1)</script><a href="javascript:x">l</a>')
    assert "script" not in out and "onclick" not in out and "javascript" not in out


def test_features_ranges_and_monotonic() -> None:
    assert F.frescor(10).value == 1.0 and F.frescor(24 * 40).value == 0.05
    assert F.concorrencia(10).value > F.concorrencia(300).value
    f, info = F.responsividade(0, 0)
    assert f.value == pytest.approx(0.25) and info["confidence"] == "baixa"
    f2, _ = F.responsividade(10, 8)
    assert f2.value > f.value and _["confidence"] == "alta"
    assert F.nao_fantasma(3, 90, True, True).value == 0.0
    transparent = F.nao_fantasma(0, 12, False, None)
    assert transparent.value == 1.0 and transparent.missing
    assert "repostagem: 0" in transparent.evidence
    assert "sinal acima de 60; não atingido" in transparent.evidence
    assert "informação sobre demissões recentes indisponível" in transparent.evidence
    assert "não confirma se a vaga é real" in transparent.evidence
    assert F.remuneracao({"max": 24000, "period": "month"}, 20000, True).value == 1.0
    assert F.remuneracao({"max": 15000, "period": "month"}, 20000, True).value == 0.0


@pytest.mark.parametrize("seed", range(20))
def test_weighted_score_bounds(seed: int) -> None:
    import random

    rnd = random.Random(seed)
    feats = {k: F.Feature(rnd.random() * 1.4 - 0.2, "x") for k in F.CHANCE_WEIGHTS}
    score, _ = F.weighted(feats, F.CHANCE_WEIGHTS)
    assert 0 <= score <= 100
    k = rnd.choice(list(feats))
    higher = dict(feats)
    higher[k] = F.Feature(min(1.0, max(0.0, feats[k].value)) + 0.1, "x")
    assert F.weighted(higher, F.CHANCE_WEIGHTS)[0] >= score


def test_quadrant_and_priority() -> None:
    th = F.THRESHOLDS
    assert F.quadrant(70, 70, th) == "top"
    assert F.quadrant(40, 70, th) == "bet"
    assert F.quadrant(70, 40, th) == "volume"
    assert F.quadrant(30, 30, th) == "discard"
    assert F.priority(70, 70, 5, True, th) == pytest.approx(80.0)


def test_state_machine() -> None:
    for s in state_machine.STATUSES:
        assert s in state_machine.TRANSITIONS
    assert state_machine.can_transition("submitted", "interviewing")
    assert state_machine.can_transition("ghosted", "interviewing")
    assert not state_machine.can_transition("accepted", "submitted")
    with pytest.raises(state_machine.InvalidTransition):
        state_machine.check("discovered", "offer")
    for s in state_machine.ACTIVE:
        assert state_machine.can_transition(s, "withdrawn")


def test_saved_search_matching_and_cron() -> None:
    from tracker.modules.jobs.models import Job

    j = Job(title="Senior Data Engineer", description_text="Python Spark", work_model="remote", seniority="senior")
    assert matches_query(j, "data engineer | analytics engineer", {"work_models": ["remote"]})
    assert not matches_query(j, "frontend", {})
    now = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)  # segunda 15h UTC
    assert cron_due("0 * * * *", now - timedelta(hours=2), now)
    assert not cron_due("0 9 1 1 *", now - timedelta(hours=2), now)
