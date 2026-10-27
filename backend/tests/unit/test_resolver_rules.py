"""Regras sem IA do preenchimento: localização por idioma e autodeclaração."""

from __future__ import annotations

import pytest

from tracker.integrations.embeddings import normalize_text
from tracker.modules.applications.resolver import FormField, location_text, self_id_value
from tracker.modules.profile.schemas import PreferenceData

P = PreferenceData(gender_identity="Homem cisgênero", sexual_orientation="Bissexual", race="Parda", lgbtqia=True)


@pytest.mark.parametrize(
    ("lang", "expected"), [("pt", "Brasília, Brasil"), ("en", "Brasília, Brazil"), (None, "Brasília, Brasil")]
)
def test_location_follows_form_language(lang: str | None, expected: str) -> None:
    assert location_text("Brasília, DF, Brazil", lang) == expected


def _sid(label: str, kind: str = "select", options: list[str] | None = None, lang: str = "pt", prefs: PreferenceData = P):
    f = FormField(key="x", label=label, kind=kind, options=options or [])
    return self_id_value(f, normalize_text(label), prefs, lang)


@pytest.mark.parametrize(
    ("label", "options", "expected"),
    [
        ("Gostaria de nos dizer sua identidade de gênero?", ["Mulher cisgênero", "Homem cisgênero", "Homem transgênero"], "Homem cisgênero"),
        ("Você faz parte da comunidade LGBTQIAP+?", ["Sim", "Não", "Prefiro não dizer"], "Sim"),
        ("Como você autodeclara sua cor/raça?", ["Branca", "Preta", "Parda", "Amarela", "Indígena"], "Parda"),
        ("Qual sua orientação sexual?", ["Heterossexual", "Bissexual", "Outra"], "Bissexual"),
        ("Sexo", ["Feminino", "Masculino"], "Masculino"),
        ("Gender", ["Woman", "Man", "Non-binary"], "Man"),
        ("Race/Ethnicity", ["White", "Black or African American", "Two or more races"], "Two or more races"),
        ("Gênero", ["Homem trans", "Mulher"], None),  # nunca "parecido"
        ("Você é pessoa com deficiência?", ["Sim", "Não"], None),  # não declarado: fica com você
    ],
)  # fmt: skip
def test_self_identification_picks_only_exact_options(label: str, options: list[str], expected: str | None) -> None:
    assert _sid(label, options=options) == expected


def test_self_identification_text_and_undeclared() -> None:
    assert _sid("Gender", kind="text", lang="en") == "Cisgender man"
    assert _sid("Identidade de gênero", kind="text") == "Homem cisgênero"
    assert _sid("Como você autodeclara sua cor/raça?", options=["Parda"], prefs=PreferenceData()) is None


def test_affirmative_opt_in_disability_and_unknown_language() -> None:

    prefs = P.model_copy(update={"disability": False})
    opt_in = "The answers below are optional. This data will be used for our affirmative actions. Do you feel comfortable answering the questions below?"
    assert _sid(opt_in, options=["Yes", "No"], lang="en", prefs=prefs) == "Yes"
    assert _sid(opt_in, options=["Yes", "No"], lang="en", prefs=PreferenceData()) is None  # nada declarado: fica com você
    assert _sid("Are you a person with disabilities?", options=["Yes", "No"], lang="en", prefs=prefs) == "No"
    assert _sid("If so, tell us what type of disability you have", options=["Physical", "Visual"], prefs=prefs) is None


def test_fit_length_cuts_at_sentence_end() -> None:
    from tracker.modules.applications.resolver import fit_length

    text = "Primeira frase curta. Segunda frase um pouco maior que a primeira. Terceira."
    assert fit_length(text, None) == text
    assert fit_length(text, 70) == "Primeira frase curta. Segunda frase um pouco maior que a primeira."
    assert len(fit_length("palavra " * 50, 40)) <= 40 and not fit_length("palavra " * 50, 40).endswith(" ")
