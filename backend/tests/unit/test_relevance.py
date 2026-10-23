"""Filtro de relevância sem IA (cargo, senioridade e local), com locais reais vistos nos boards."""

from __future__ import annotations

import pytest

from tracker.modules.jobs.normalize import RawJob, normalize_title
from tracker.modules.jobs.relevance import filter_relevant, location_ok, matches, search_variants
from tracker.modules.jobs.tasks import matches_saved_search_filters

TERMS = [normalize_title(t) for t in ["data engineer", "engenheiro de dados", "backend", "software engineer", "machine learning"]]


@pytest.mark.parametrize(
    "location",
    [
        "Brazil",
        "São Paulo, SP",
        "Belo Horizonte, MG; Curitiba, PR; Recife, PE",
        "Remoto",
        "São Paulo ou Teletrabalho",
        "Brazil (Remote)",
        "Home based - Worldwide",
        "Home Based - Americas; Home based - EMEA",
        "Remote, Global",
        "LATAM",
    ],
)
def test_locations_open_to_brazil(location: str) -> None:
    assert location_ok(location, brazilian_company=False)


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "Remote - USA",
        "Remote Canada",
        "Bengaluru, India",
        "London, United Kingdom",
        "US-CA-Menlo Park",
        "Home based - EMEA",
        "Remote Spain",
    ],
)
def test_locations_closed_to_brazil(location: str) -> None:
    assert not location_ok(location, brazilian_company=False)


def test_ambiguous_location_depends_on_company() -> None:
    for loc in (None, "", "Remote", "Hybrid", "N/A"):
        assert location_ok(loc, brazilian_company=True)
        assert not location_ok(loc, brazilian_company=False)


def test_worldwide_only_when_international() -> None:
    assert not location_ok("Home based - Worldwide", brazilian_company=False, international=False)
    assert location_ok("São Paulo", brazilian_company=False, international=False)


def test_title_match_and_exclusions() -> None:
    def raw(title: str, loc: str | None = "Brazil") -> RawJob:
        return RawJob(source="x", external_id=title, title=title, company_name="X", location=loc)

    raws = [
        raw("Engenheiro de Dados Pleno"),
        raw("Senior Backend Software Engineer"),
        raw("Estágio em Engenharia de Dados"),
        raw("Engineering Manager, Data Engineer"),
        raw("Data Engineer", "Remote - USA"),
        raw("Product Designer"),
    ]
    kept = [r.title for r in filter_relevant(raws, TERMS, brazilian_company=False)]
    assert kept == ["Engenheiro de Dados Pleno", "Senior Backend Software Engineer"]
    # sem informar a empresa, o local não é filtrado (ex.: importação manual)
    assert "Data Engineer" in [r.title for r in filter_relevant(raws, TERMS)]
    assert matches("Pessoa Engenheira de Software Sênior", [normalize_title("engenheira de software")])


def test_saved_search_can_match_keywords_in_description() -> None:
    raw = RawJob(
        source="x",
        external_id="1",
        title="Backend Engineer",
        company_name="X",
        description_text="Build Go services for US customers.",
    )
    go = [normalize_title("Go")]
    assert not filter_relevant([raw], go)
    assert filter_relevant([raw], go, search_in_description=True) == [raw]


def test_go_keyword_matches_language_alias_and_board_tags_without_substring_noise() -> None:
    assert matches("Go Engineer", ["go"])
    assert matches("Golang Backend Developer", ["go"])
    assert matches("Go Engineer", ["golang"])
    assert not matches("Google Cloud Engineer", ["go"])
    assert search_variants("go") == ["go", "Golang"]
    assert search_variants("Golang") == ["Golang", "Go"]
    assert search_variants("backend Go") == ["backend Go"]

    tagged = RawJob(
        source="remoteok",
        external_id="2",
        title="Backend Engineer",
        company_name="X",
        raw={"tags": ["Golang", "Backend"]},
    )
    assert filter_relevant([tagged], ["go"], search_in_description=True) == [tagged]


def test_saved_search_filters_country_alias_and_work_model() -> None:
    raw = RawJob(
        source="x",
        external_id="1",
        title="Backend Engineer",
        company_name="X",
        location="Remote - US",
        work_model="remote",
    )
    assert matches_saved_search_filters(raw, {"country": "United States", "work_models": ["remote"]})
    assert matches_saved_search_filters(raw, {"country": "USA"})
    assert matches_saved_search_filters(
        RawJob(source="x", external_id="2", title="Backend", company_name="X", location="Worldwide"),
        {"country": "United States"},
    )
    assert not matches_saved_search_filters(raw, {"country": "Canada"})
    assert not matches_saved_search_filters(raw, {"work_models": ["onsite"]})
    assert not matches_saved_search_filters(
        RawJob(source="x", external_id="3", title="Backend", company_name="X", location="Remote"),
        {"country": "United States"},
    )
    assert not matches_saved_search_filters(
        RawJob(source="x", external_id="4", title="Backend", company_name="X", location="Brazil"),
        {"work_models": ["remote"]},
    )
    assert matches_saved_search_filters(
        RawJob(
            source="x",
            external_id="5",
            title="Backend",
            company_name="X",
            description_text="Work remotely from any location.",
        ),
        {"work_models": ["remote"]},
    )
