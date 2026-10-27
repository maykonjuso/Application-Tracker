"""Normalização heurística de vagas (antes/além da extração por IA). RF-VAG-06."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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
    if re.search(r"\b(remot[oae]|remote|home ?office|anywhere)\b", t):
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


# ---------------- Datas de publicação ----------------
# Datas fora desta janela são lixo da fonte (epoch zero, ano 9999, fuso errado).
_MIN_YEAR = 2000
_FUTURE_TOLERANCE = timedelta(days=1)
_UNIT_HOURS = {
    "min": 1 / 60, "minuto": 1 / 60, "minute": 1 / 60,
    "hora": 1, "hour": 1, "h": 1,
    "dia": 24, "day": 24, "d": 24,
    "semana": 24 * 7, "week": 24 * 7, "sem": 24 * 7, "w": 24 * 7,
    "mes": 24 * 30, "mês": 24 * 30, "month": 24 * 30, "mo": 24 * 30,
}  # fmt: skip
_NUM_WORDS = {"a": 1, "an": 1, "um": 1, "uma": 1, "one": 1, "two": 2, "dois": 2, "duas": 2, "three": 3, "três": 3, "tres": 3}
_UNIT_RE = r"(minutos?|minutes?|mins?|horas?|hours?|hrs?|dias?|days?|semanas?|weeks?|m[eê]s|meses|months?)"
_N_RE = r"(\d{1,3}|an?|uma?|one|two|dois|duas|three|tr[eê]s)"
# "há 3 dias", "3 days ago", "Reposted 2 weeks ago", "publicada há 5 horas"
_REL_PT = re.compile(rf"\bh[aá]\s+(?:mais\s+de\s+)?{_N_RE}\s+{_UNIT_RE}\b", re.I)
_REL_EN = re.compile(rf"\b{_N_RE}\+?\s+{_UNIT_RE}\s+ago\b", re.I)
_REL_SHORT = re.compile(r"\b(\d{1,3})\s*(h|d|w|mo)\b", re.I)  # "3d" em cards compactos, só após "posted/publicada"
_POSTED = r"\b(?:posted|publicad[ao]|anunciad[ao]|reposted|republicad[ao])\s*(?:em\s*)?:?\s*"
_TODAY = re.compile(_POSTED + r"(hoje|today|just now|agora)\b", re.I)
_YESTERDAY = re.compile(_POSTED + r"(ontem|yesterday)\b", re.I)
_MARKER = (
    r"(?:posted|published|publicad[ao]|anunciad[ao]|data de publica[cç][aã]o|reposted|republicad[ao])"
    r"\s*(?:on|em|in)?\s*:?\s*"
)
_ABS_BR = re.compile(rf"{_MARKER}(\d{{1,2}})[/.-](\d{{1,2}})[/.-](\d{{4}})", re.I)
_ABS_ISO = re.compile(rf"{_MARKER}(\d{{4}}-\d{{2}}-\d{{2}})", re.I)
_MONTHS = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "mai": 5, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "aug": 8, "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}  # fmt: skip
_ABS_EN = re.compile(rf"{_MARKER}([a-z]{{3}})[a-z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)  # "Posted on Oct 2, 2026"
_ABS_PT = re.compile(rf"{_MARKER}(\d{{1,2}})\s+de\s+([a-zç]{{3}})[a-zç]*\.?\s+de\s+(\d{{4}})", re.I)  # "12 de outubro de 2026"
_NOT_POSTED = re.compile(r"(fundad|founded|criad[ao]|desde|since|experi[eê]n|started|come[cç]ou|lan[cç]ad|launched)", re.I)
_RELATIVE_WINDOW = 3000  # a data aparece no topo da página/card; mais abaixo vira ruído ("fundada há 3 meses")


def _sane(dt: datetime, now: datetime) -> datetime | None:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    if dt.year < _MIN_YEAR or dt > now + _FUTURE_TOLERANCE:
        return None
    return min(dt, now)


def parse_date(value: Any, now: datetime | None = None) -> datetime | None:
    """Data de publicação vinda de uma fonte, em qualquer formato comum → UTC.

    Aceita epoch em segundos ou milissegundos, ISO 8601 (com ou sem fuso), só a data,
    RFC 2822 dos feeds RSS ("Tue, 06 Oct 2026 07:31:16 +0000"), dd/mm/aaaa e textos
    relativos ("há 3 dias", "2 weeks ago"). Datas absurdas viram None."""
    now = now or datetime.now(UTC)
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return _sane(value, now)
    if isinstance(value, int | float):
        if value <= 0:
            return None
        # abaixo de 1e11 é segundos (1e11 s = ano 5138); acima, milissegundos
        secs = value if value < 1e11 else value / 1000
        try:
            return _sane(datetime.fromtimestamp(secs, UTC), now)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if s.isdigit():
        return parse_date(int(s), now)
    try:
        return _sane(datetime.fromisoformat(s.replace("Z", "+00:00")), now)
    except ValueError:
        pass
    try:
        return _sane(parsedate_to_datetime(s), now)
    except (TypeError, ValueError, IndexError):
        pass
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        try:
            return _sane(datetime(int(m[3]), int(m[2]), int(m[1]), 12, tzinfo=UTC), now)
        except ValueError:
            return None
    return _relative(s, now, strict=False)


def _hours(n: str, unit: str) -> float | None:
    count = int(n) if n.isdigit() else _NUM_WORDS.get(n.lower())
    u = unit.lower()
    u = "mes" if u in ("mês", "mes", "meses") else u.rstrip("s")
    u = {"hr": "hour"}.get(u, u)
    if u.startswith("minut"):
        u = "min"
    per = _UNIT_HOURS.get(u)
    if count is None or per is None:
        return None
    return count * per


def _relative(text: str, now: datetime, strict: bool) -> datetime | None:
    if _TODAY.search(text) or (not strict and text.lower() in ("hoje", "today", "just now", "agora")):
        return now
    if _YESTERDAY.search(text) or (not strict and text.lower() in ("ontem", "yesterday")):
        return now - timedelta(days=1)
    for rx in (_REL_PT, _REL_EN):
        for m in rx.finditer(text):
            # "fundada há 3 meses", "experiência de 2 anos atrás": não é a data do anúncio
            if strict and _NOT_POSTED.search(text[max(0, m.start() - 40) : m.start()]):
                continue
            if (h := _hours(m[1], m[2])) is not None:
                return now - timedelta(hours=h)
    m = re.search(rf"{_MARKER}{_REL_SHORT.pattern}", text, re.I)
    if m and (h := _hours(m[1], m[2])) is not None:
        return now - timedelta(hours=h)
    return None


def find_posted_at(text: str | None, now: datetime | None = None) -> datetime | None:
    """Procura a data de publicação no texto do anúncio (LinkedIn, página colada, card da lista).

    `now` é o momento em que o texto foi capturado: "há 3 dias" é relativo a ele."""
    if not text:
        return None
    now = now or datetime.now(UTC)
    head = text[:_RELATIVE_WINDOW]
    for rx in (_ABS_ISO,):
        if m := rx.search(head):
            return parse_date(m[1], now)
    if m := _ABS_BR.search(head):
        try:
            return _sane(datetime(int(m[3]), int(m[2]), int(m[1]), 12, tzinfo=UTC), now)
        except ValueError:
            pass
    for rx, order in ((_ABS_EN, ("mon", "day")), (_ABS_PT, ("day", "mon"))):
        if m := rx.search(head):
            mon_s, day_s = (m[1], m[2]) if order[0] == "mon" else (m[2], m[1])
            mon = _MONTHS.get(mon_s.lower()[:3])
            if mon:
                try:
                    return _sane(datetime(int(m[3]), mon, int(day_s), 12, tzinfo=UTC), now)
                except ValueError:
                    pass
    return _relative(head, now, strict=True)
