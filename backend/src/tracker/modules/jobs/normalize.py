"""Normalização heurística de vagas (antes/além da extração por IA). RF-VAG-06."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import nh3

PT_WORDS = {
    "de",
    "para",
    "com",
    "experiência",
    "conhecimento",
    "vaga",
    "requisitos",
    "você",
    "nossa",
    "empresa",
    "atividades",
    "responsabilidades",
    "desejável",
    "benefícios",
    "e",
    "em",
}
EN_WORDS = {
    "the",
    "and",
    "with",
    "experience",
    "you",
    "we",
    "our",
    "requirements",
    "skills",
    "responsibilities",
    "team",
    "will",
    "of",
    "to",
    "in",
}

SENIORITY = [
    ("intern", r"\b(intern|internship|estagi[aá]ri[oa]|est[aá]gio)\b"),
    ("junior", r"\b(junior|júnior|jr\.?)\b"),
    ("mid", r"\b(pleno|mid[- ]?level|intermediate|\bpl\b)\b"),
    ("senior", r"\b(senior|sênior|sr\.?)\b"),
    ("staff", r"\b(staff|principal|lead|tech lead|especialista|specialist)\b"),
]


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    company_name: str
    url: str | None = None
    company_domain: str | None = None
    location: str | None = None
    description_html: str | None = None
    description_text: str | None = None
    posted_at: datetime | None = None
    apply_url: str | None = None
    apply_kind: str | None = None
    ats_kind: str | None = None
    salary: dict[str, Any] | None = None
    work_model: str | None = None
    applicants_count: int | None = None
    recruiter: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def sanitize_html(value: str) -> str:
    return nh3.clean(
        value,
        tags={"p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "h1", "h2", "h3", "h4", "a", "span", "div"},
        attributes={"a": {"href"}},
        link_rel="noopener noreferrer nofollow",
    )


def html_to_text(value: str) -> str:
    v = html.unescape(value)
    v = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", v)
    v = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", v)
    v = re.sub(r"<[^>]+>", " ", v)
    v = html.unescape(v)
    v = re.sub(r"[ \t\r\f\v]+", " ", v)
    return re.sub(r"\n\s*\n+", "\n\n", v).strip()


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_language(text: str) -> str:
    words = re.findall(r"[a-záéíóúâêôãõç]+", text.lower())[:600]
    pt = sum(w in PT_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    return "pt" if pt > en else "en"


def detect_work_model(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(h[ií]brido|hybrid)\b", t):
        return "hybrid"
    if re.search(r"\b(remot[oae]|remotely|home ?office|anywhere)\b", t):
        return "remote"
    if re.search(r"\b(presencial|on[- ]?site|in[- ]office)\b", t):
        return "onsite"
    return None


def detect_regime(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bclt\b", t):
        return "CLT"
    if re.search(r"\b(pj|pessoa jur[ií]dica|contractor|contrato pj)\b", t):
        return "PJ"
    if re.search(r"\b(est[aá]gio|internship)\b", t):
        return "internship"
    return None


def detect_seniority(title: str, text: str = "") -> str | None:
    for level, pattern in SENIORITY:
        if re.search(pattern, title, re.I):
            return level
    for level, pattern in SENIORITY:
        if re.search(pattern, text[:400], re.I):
            return level
    return None


_SAL = re.compile(
    r"(R\$|US\$|USD|\$|€|EUR|BRL)\s?([\d.,]+)\s*(k|mil)?\s*(?:-|a|to|até)\s*(?:R\$|US\$|USD|\$|€|EUR|BRL)?\s?([\d.,]+)\s*(k|mil)?",
    re.I,
)


def _num(s: str, mult: str | None) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?", s):
        s = re.sub(r"[.,](\d{2})$", r"_\1", s)
        s = re.sub(r"[.,]", "", s).replace("_", ".")
    else:
        s = s.replace(",", ".")
    v = float(s)
    return v * 1000 if mult else v


def detect_salary(text: str) -> dict[str, Any] | None:
    m = _SAL.search(text)
    if not m:
        return None
    cur = {"r$": "BRL", "brl": "BRL", "us$": "USD", "usd": "USD", "$": "USD", "€": "EUR", "eur": "EUR"}[m.group(1).lower()]
    lo, hi = _num(m.group(2), m.group(3)), _num(m.group(4), m.group(5))
    period = "year" if hi > 50_000 or re.search(r"(?i)(year|ano|annual|anual)", text[m.end() : m.end() + 30]) else "month"
    return {"min": lo, "max": hi, "currency": cur, "period": period, "estimated": False}


def detect_apply_kind(url: str | None, text: str = "") -> tuple[str, str | None]:
    u = (url or "").lower()
    for ats, pattern in [
        ("greenhouse", "greenhouse.io"),
        ("lever", "lever.co"),
        ("ashby", "ashbyhq.com"),
        ("gupy", "gupy.io"),
        ("workday", "myworkdayjobs.com"),
        ("smartrecruiters", "smartrecruiters.com"),
        ("inhire", "inhire"),
        ("solides", "solides"),
        ("workable", "workable.com"),
        ("teamtailor", "teamtailor.com"),
        ("breezy", "breezy.hr"),
        ("kenoby", "kenoby"),
    ]:
        if pattern in u:
            return "external", ats
    if "linkedin.com" in u:
        if re.search(r"(?i)(easy apply|candidatura simplificada)", text):
            return "easy_apply", None
        return "unknown", None
    if re.search(r"(?i)mailto:|envie (seu )?curr[ií]culo para|send (your )?(cv|resume) to", text):
        return "email", None
    return ("external", None) if u else ("unknown", None)
