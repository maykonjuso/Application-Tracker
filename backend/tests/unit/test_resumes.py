from __future__ import annotations

import shutil
from datetime import date

import pytest

from tests.factories import master
from tracker.modules.profile import facts
from tracker.modules.resumes import ats, content, render, validator
from tracker.modules.resumes.schemas import ResumeContent


def test_years_for_skill_and_total() -> None:
    m = master()
    today = date(2026, 10, 1)
    assert facts.total_years(m, today) == pytest.approx(5.8, abs=0.1)
    assert facts.years_for_skill(m, "Python", today) == pytest.approx(5.8, abs=0.1)
    assert facts.years_for_skill(m, "Kafka", today) == pytest.approx(3.7, abs=0.05)
    assert facts.years_for_skill(m, "Rust", today) == 0.0


def test_has_skill_aliases_and_subitems() -> None:
    m = master()
    assert facts.has_skill(m, "Postgres") == "PostgreSQL"
    assert facts.has_skill(m, "spark") == "Apache Spark (PySpark)"
    assert facts.has_skill(m, "T-SQL") == "SQL (T-SQL)"
    assert facts.has_skill(m, "Kubernetes") is None


def test_numbers_normalization() -> None:
    assert facts.numbers_in("~10,000 users and 10 mil reports, 5x faster, 60%+") >= {"10000", "5", "60"}
    assert "20000" in facts.numbers_in("20.000 reportes")


def test_baseline_passes_validation() -> None:
    m = master()
    c = content.baseline(m, "data_engineer", "en")
    report = validator.validate(c, m)
    assert report.passed, report.violations
    assert c.experience[0].period == "Mar 2023 -- Present"
    assert c.experience[1].period == "2021 -- 2023"


def _content() -> tuple[ResumeContent, object]:
    m = master()
    return content.baseline(m, "data_engineer", "en"), m


def test_validator_flags_invented_skill() -> None:
    c, m = _content()
    c.skill_rows[0].items.append("Kubernetes")
    rep = validator.validate(c, m)
    assert not rep.passed
    assert any(v.kind == "skill" and "Kubernetes" in v.text for v in rep.violations)


def test_validator_flags_invented_tech_in_bullet() -> None:
    c, m = _content()
    c.experience[0].bullets[0] += " Deployed on Kubernetes with Terraform."
    rep = validator.validate(c, m)
    assert {v.text.split("'")[1] for v in rep.violations if v.kind == "skill"} >= {"kubernetes", "terraform"}


def test_validator_flags_changed_metric() -> None:
    c, m = _content()
    c.experience[0].bullets[0] = c.experience[0].bullets[0].replace("12 ETL", "15 ETL")
    rep = validator.validate(c, m)
    assert any(v.kind == "number" and "15" in v.text for v in rep.violations)


def test_validator_flags_wrong_org_title_date() -> None:
    c, m = _content()
    c.experience[0].organization = "Google"
    c.experience[0].title = "Staff Engineer"
    c.experience[0].period = "Jan 2020 -- Present"
    kinds = {v.kind for v in validator.validate(c, m).violations}
    assert {"organization", "title", "date"} <= kinds


def test_validator_accepts_alias_and_confirmed_alternative_title() -> None:
    c, m = _content()
    c.skill_rows[0].items.append("Postgres")
    c.experience[0].title = "Software Engineer"
    assert validator.validate(c, m).passed


def test_years_claim_warning() -> None:
    c, m = _content()
    c.summary = "Engineer with **12+ years** of experience."  # 12 existe no mestre
    rep = validator.validate(c, m)
    assert rep.passed and any(w.kind == "years" for w in rep.warnings)


def test_tex_escape_and_bold() -> None:
    assert render.tex_escape("R&D 50% $5 #1 a_b ~x") == r"R\&D 50\% \$5 \#1 a\_b \textasciitilde{}x"
    assert render.md_bold(render.tex_escape("**C# & Go**")) == r"\textbf{C\# \& Go}"


def test_pdf_filename() -> None:
    assert render.pdf_filename(master(), "Data Engineer", "en") == "Ana_Silva_Data_Engineer_EN.pdf"


def test_keyword_coverage() -> None:
    cov, missing = ats.keyword_coverage("Python and Apache Spark pipelines", ["Python", "Spark", "Rust"])
    assert cov == pytest.approx(0.667, abs=0.01)
    assert missing == ["Rust"]


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic não instalado")
def test_render_compiles_pdf_with_special_chars() -> None:
    m = master()
    c = content.baseline(m, "data_engineer", "pt")
    c.summary = "Engenheira de dados com R&D, 50% e C# -- acentuação: ção, ê, ü."
    pdf = render.compile_pdf(render.render_tex(c, m))
    assert pdf.page_count >= 1
    assert "Ana Teste Silva".upper() in pdf.text.upper()
    assert "R&D" in pdf.text and "50%" in pdf.text
    report = ats.check(pdf.text, m, "pt", ["Python", "Kafka"])
    assert report.ok, [ch for ch in report.checks if not ch.ok]
    assert report.keyword_coverage == 1.0


def test_from_draft_keeps_master_experience_order() -> None:
    """A IA escolhe o texto; a ordem das experiências é sempre a do mestre."""
    from tracker.modules.resumes.schemas import DraftExperience, ResumeDraft

    m = master()
    ids = [e.id for e in m.experience]
    assert len(ids) >= 2
    draft = ResumeDraft(
        headline="h",
        summary="s",
        skill_rows=[],
        experiences=[DraftExperience(experience_id=i, title=m.experience_by_id(i).title, bullets=["x"]) for i in reversed(ids)],
        project_ids=[],
        used_facts=[],
        changes=[],
    )
    out = content.from_draft(m, draft, "en")
    assert [e.experience_id for e in out.experience] == ids


def test_render_keeps_master_order_and_localizes_pt() -> None:
    """CVs salvos com outra ordem saem na ordem do mestre; em português, "Brasil" e "aberto a remoto"."""
    m = master()
    m.contact.location = "Brasília, DF, Brazil"
    m.contact.remote = "Open to Remote"
    c = content.baseline(m, "data_engineer", "pt")
    titles = [e.title for e in c.experience]
    c.experience = list(reversed(c.experience))
    tex = render.render_tex(c, m)
    pos = [tex.find(render.tex_escape(t)) for t in titles]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)
    assert "Brasília, DF, Brasil" in tex and "aberto a remoto" in tex and "Brazil" not in tex
