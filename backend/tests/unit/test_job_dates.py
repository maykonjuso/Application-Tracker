"""Datas de publicação: formatos das fontes e texto do anúncio."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from tracker.integrations import job_sources
from tracker.modules.jobs import normalize as nz

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tue, 06 Oct 2026 07:31:16 +0000", datetime(2026, 10, 6, 7, 31, 16, tzinfo=UTC)),  # RSS
        ("2026-10-02T18:41:51Z", datetime(2026, 10, 2, 18, 41, 51, tzinfo=UTC)),
        ("2026-10-20 10:00:00", datetime(2026, 10, 20, 10, tzinfo=UTC)),  # sem fuso = UTC
        ("02/10/2026", datetime(2026, 10, 2, 12, tzinfo=UTC)),
        (1790000000, datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)),  # segundos
        (1790000000000, datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)),  # milissegundos
        ("1790000000", datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)),
        ("há 3 dias", datetime(2026, 10, 24, 12, tzinfo=UTC)),
        ("há um mês", datetime(2026, 9, 27, 12, tzinfo=UTC)),
        ("2 weeks ago", datetime(2026, 10, 13, 12, tzinfo=UTC)),
        ("an hour ago", datetime(2026, 10, 27, 11, tzinfo=UTC)),
        ("hoje", NOW),
    ],
)
def test_parse_date_formats(value: object, expected: datetime) -> None:
    assert nz.parse_date(value, NOW) == expected


@pytest.mark.parametrize("value", [None, "", 0, -5, "9999-01-01", "1970-01-01", "2026-12-25", "lixo", True])
def test_parse_date_rejects_garbage_and_future(value: object) -> None:
    assert nz.parse_date(value, NOW) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Analista de Dados\nItaú · São Paulo · Há 2 semanas · Mais de 100 candidaturas", datetime(2026, 10, 13, 12, tzinfo=UTC)),
        ("Data Engineer\nRemote · Reposted 3 days ago · 50 applicants", datetime(2026, 10, 24, 12, tzinfo=UTC)),
        ("Vaga X\nPublicada em: 12/10/2026", datetime(2026, 10, 12, 12, tzinfo=UTC)),
        ("Posted on Oct 2, 2026", datetime(2026, 10, 2, 12, tzinfo=UTC)),
        ("Publicada em 5 de outubro de 2026", datetime(2026, 10, 5, 12, tzinfo=UTC)),
        ("Publicada hoje", NOW),
        ("Fundada há 3 meses, a vaga foi publicada há 2 dias", datetime(2026, 10, 25, 12, tzinfo=UTC)),
    ],
)
def test_find_posted_at_in_ad_text(text: str, expected: datetime) -> None:
    assert nz.find_posted_at(text, NOW) == expected


@pytest.mark.parametrize("text", ["Join us today!", "Empresa fundada há 3 meses", "5 anos de experiência", "", None])
def test_find_posted_at_ignores_unrelated_text(text: str | None) -> None:
    assert nz.find_posted_at(text, NOW) is None


async def test_gupy_enrich_fills_date_and_description() -> None:
    page = {
        "props": {
            "pageProps": {
                "job": {
                    "publishedAt": "2026-10-02T18:41:51.844Z",
                    "description": "<p>Time de dados</p>",
                    "prerequisites": "<ul><li>Python</li></ul>",
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script>'

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/jobs/1":
            return httpx.Response(200, text=html)
        return httpx.Response(500)

    ok = nz.RawJob(source="gupy", external_id="1", title="Eng. Dados", company_name="X")
    down = nz.RawJob(source="gupy", external_id="2", title="Eng. Dados", company_name="X")
    n = await job_sources.gupy_enrich("x", [ok, down], transport=httpx.MockTransport(handler))
    assert n == 1
    assert ok.posted_at == datetime(2026, 10, 2, 18, 41, 51, 844000, tzinfo=UTC)
    assert "Time de dados" in (ok.description_text or "") and "Python" in (ok.description_text or "")
    assert down.posted_at is None and down.description_text is None  # página fora do ar não quebra
