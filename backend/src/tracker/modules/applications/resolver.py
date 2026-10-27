"""Resolução de campos de formulário de candidatura (docs/09 §4, RF-CAN-04).

Ordem: campos de perfil → banco (exato) → banco (semântico) → regras → IA → pendente.
Perguntas sensíveis nunca chegam à IA.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracker.core import settings_store
from tracker.core.humanize import humanize
from tracker.integrations.claude.client import AIError, AIRequest, get_ai, load_prompt, sessionmaker_from
from tracker.integrations.embeddings import cosine, embed_one, normalize_text
from tracker.modules.applications.models import Application, ApplicationAnswer, PendingQuestion
from tracker.modules.jobs.models import Job
from tracker.modules.profile import facts
from tracker.modules.profile import service as profile_service
from tracker.modules.profile.models import AnswerBankEntry
from tracker.modules.profile.schemas import MasterProfile, PreferenceData
from tracker.modules.resumes.validator import validate_free_text

log = logging.getLogger(__name__)


class FormField(BaseModel):
    key: str
    label: str
    kind: str = "text"  # text|number|email|tel|select|radio|checkbox|textarea|file|date|typeahead
    options: list[str] = Field(default_factory=list)
    required: bool = False
    current_value: str | None = None
    error: str | None = None
    max_length: int | None = Field(default=None, ge=1, le=100000)  # limite de caracteres do site, quando existe


class FieldAnswer(BaseModel):
    value: str
    option_index: int | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    used_facts: list[str] = Field(default_factory=list)


@dataclass
class Resolved:
    key: str
    value: Any
    resolution: str
    confidence: float = 1.0
    bank_id: Any = None


@dataclass
class Resolution:
    values: list[Resolved] = field(default_factory=list)
    pending: list[FormField] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


def norm_q(q: str) -> str:
    return profile_service.normalize_question(q)


def choose_option(options: list[str], value: Any, prefer: list[str] | None = None) -> str | None:
    if not options:
        return None
    candidates = [str(value)] if value is not None else []
    candidates += prefer or []
    norm_opts = {normalize_text(o): o for o in options}
    for c in candidates:
        n = normalize_text(str(c))
        if n in norm_opts:
            return norm_opts[n]
    for c in candidates:
        n = normalize_text(str(c))
        for no, o in norm_opts.items():
            if n and (n in no or no in n) and len(no) > 0:
                return o
    if isinstance(value, int | float) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value)):
        v = float(value)
        for o in options:  # faixas: "3-5 anos", "5+"
            nums = [float(x) for x in re.findall(r"\d+", o)]
            if len(nums) == 2 and nums[0] <= v <= nums[1]:
                return o
            if len(nums) == 1 and "+" in o and v >= nums[0]:
                return o
    yes_no = {"yes": ["yes", "sim"], "no": ["no", "nao", "não"]}
    sval = normalize_text(str(value)) if value is not None else ""
    for key, words in yes_no.items():
        if sval in words:
            for o in options:
                if normalize_text(o) in yes_no[key]:
                    return o
    return None


PROFILE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(first name|primeiro nome|given name)\b", "first_name"),
    (r"\b(last name|sobrenome|family name|surname)\b", "last_name"),
    (r"\b(full name|nome completo)\b|^nome$|^name$", "full_name"),
    (r"e-?mail", "email"),
    (r"country code|codigo do pais|ddi", "country_code"),
    (r"phone|telefone|celular|mobile|whatsapp", "phone"),
    # "cidade/estado", "where do you live", "current location": cidade + UF (não só a cidade)
    (
        r"cidade\s*/\s*estado|city\s*/\s*state|\breside\b|where do you (currently )?live|onde (voce )?mora|"
        r"current location|localizacao atual|^(location|localizacao|localidade)\b",
        "location",
    ),
    (r"^(city|cidade)\b", "city"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"portfolio|website|site pessoal|personal site", "portfolio"),
    (r"headline|titulo profissional", "headline"),
]


def location_text(location: str | None, language: str | None) -> str | None:
    """ "Brasília, DF, Brazil" → "Brasília, Brasil" (vaga BR) ou "Brasília, Brazil" (vaga em inglês)."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return None
    city, country = parts[0], parts[-1] if len(parts) > 1 else None
    if country and normalize_text(country) in ("brazil", "brasil", "br"):
        country = "Brazil" if language == "en" else "Brasil"
    return f"{city}, {country}" if country else city


# perguntas sobre o seu emprego atual (vêm de Ofertas → remuneração atual, não da IA)
CURRENT_COMPANY_RE = re.compile(
    r"current (company|employer)|empresa atual|empregador atual|nome da empresa (em que|onde)|"
    r"empresa (em|na) que (voce )?trabalha|company where you (currently )?work",
)
CURRENT_SALARY_RE = re.compile(
    r"(current|last|present|actual)\b.{0,25}(salary|compensation|pay)|"
    r"(salario|remuneracao)\s+(atual|ultim[oa]|de hoje)|(atual|ultim[oa])\s+(salario|remuneracao)",
)
# consentimento para o processo seletivo (obrigatório para se candidatar) x opt-in de marketing (fica com você)
CONSENT_RE = re.compile(
    r"(concordo|autorizo|aceito|consinto|i agree|i consent|i accept|i acknowledge).{0,160}"
    r"(dados pessoais|personal (data|information)|privacidade|privacy|lgpd|gdpr|recrutamento|recruitment|selecao)"
)
MARKETING_RE = re.compile(
    r"novas oportunidades|future (opportunities|openings|roles)|newsletter|marketing|midias sociais|"
    r"comunicac|send me|enviar informac|talent (pool|community)|banco de talentos"
)


# autodeclaração: a pergunta → (campo das preferências, opções equivalentes por valor declarado)
ORIENTATION_RE = re.compile(r"orientacao sexual|sexual orientation")
LGBT_RE = re.compile(r"lgbt")
GENDER_RE = re.compile(r"identidade de genero|\bgenero\b|\bgender\b|^sexo\b")
RACE_RE = re.compile(r"\b(cor|raca|etnia|race|ethnicity|ethnic|racial)\b")
SELF_ID_SYNONYMS: dict[str, list[str]] = {
    "homem cisgenero": ["Homem cisgênero", "Homem cis", "Homem (cisgênero)", "Homem cisgênero (cis)", "Masculino",
                        "Cisgender man", "Cis man", "Man (cisgender)", "Male", "Homem", "Man"],
    "mulher cisgenero": ["Mulher cisgênero", "Mulher cis", "Feminino", "Cisgender woman", "Cis woman", "Female",
                         "Mulher", "Woman"],
    "bissexual": ["Bissexual", "Bisexual"],
    "parda": ["Parda", "Pardo", "Pardo(a)", "Parda(o)", "Pardo/Parda", "Parda/Pardo", "Pardos", "Mixed", "Mixed race",
              "Brown", "Multiracial", "Two or more races"],
}  # fmt: skip
SELF_ID_EN = {
    "homem cisgenero": "Cisgender man",
    "mulher cisgenero": "Cisgender woman",
    "bissexual": "Bisexual",
    "parda": "Mixed (Pardo)",
}


EXACT_ONLY = {"homem", "man", "mulher", "woman", "male", "female", "masculino", "feminino"}


def choose_exact(options: list[str], wanted: list[str]) -> str | None:
    """Só aceita opção igual (ou que começa pela resposta, ex.: "Sim, faço parte"). Nada de "parecido"."""
    norm = [(normalize_text(o), o) for o in options]
    for w in wanted:
        nw = normalize_text(w)
        for no, o in norm:
            if no == nw:
                return o
    for w in wanted:
        nw = normalize_text(w)
        if nw in EXACT_ONLY:
            continue  # "Homem" não pode virar "Homem transgênero"
        hits = [o for no, o in norm if re.match(rf"{re.escape(nw)}(\W|$)", no)]
        if len(hits) == 1:
            return hits[0]
    return None


AFFIRMATIVE_OPT_IN_RE = re.compile(
    r"(comfortable|confortavel|deseja|would you like|gostaria).{0,40}(answer|respond|responder)|"
    r"(affirmative|afirmativ|diversidade|diversity).{0,200}(comfortable|confortavel|deseja|responder|answer)"
)
DISABILITY_RE = re.compile(r"deficien|disabilit|\bpcd\b|\bpwd\b")
DISABILITY_DETAIL_RE = re.compile(r"\b(tipo|type|qual|which|what kind|descreva|describe|laudo|cid)\b")


def self_id_value(f: FormField, label_n: str, prefs: PreferenceData, language: str | None) -> Any | None:
    """Resposta da autodeclaração para esta pergunta, ou None (não é pergunta disso / não declarado / sem opção exata)."""
    declared_any = any(
        v not in (None, "")
        for v in (prefs.gender_identity, prefs.sexual_orientation, prefs.race, prefs.lgbtqia, prefs.disability)
    )
    if AFFIRMATIVE_OPT_IN_RE.search(label_n) and not GENDER_RE.search(label_n) and not RACE_RE.search(label_n):
        declared: str | bool | None = True if declared_any else None  # "quer responder às perguntas abaixo?"
    elif DISABILITY_RE.search(label_n):
        if DISABILITY_DETAIL_RE.search(label_n):
            return None  # tipo/laudo da deficiência: só você responde
        declared = prefs.disability
    elif ORIENTATION_RE.search(label_n):
        declared = prefs.sexual_orientation
    elif LGBT_RE.search(label_n):
        declared = prefs.lgbtqia
    elif GENDER_RE.search(label_n):
        declared = prefs.gender_identity
    elif RACE_RE.search(label_n):
        declared = prefs.race
    else:
        return None
    if declared is None or declared == "":
        return None
    if isinstance(declared, bool):
        wanted = ["Sim", "Yes"] if declared else ["Não", "No"]
        text = ("Yes" if declared else "No") if language == "en" else ("Sim" if declared else "Não")
    else:
        key = normalize_text(declared)
        wanted = [declared, *SELF_ID_SYNONYMS.get(key, [])]
        text = SELF_ID_EN.get(key, declared) if language == "en" else declared
    if f.kind in ("select", "radio", "typeahead"):
        return choose_exact(f.options, wanted) if f.options else None
    if f.kind == "checkbox":
        return [o] if f.options and (o := choose_exact(f.options, wanted)) else None
    return text


LANG_NAMES = {
    "spanish": "spanish", "espanhol": "spanish", "french": "french", "frances": "french", "german": "german",
    "alemao": "german", "italian": "italian", "italiano": "italian", "mandarin": "chinese", "chines": "chinese",
}  # fmt: skip
NO_LANGUAGE_RE = re.compile(r"(don.?t|do not|nao) (speak|falo)|\bnone\b|\bnenhum|no knowledge|sem conhecimento|not applicable")


def unknown_language_option(f: FormField, label_n: str, master: MasterProfile) -> str | None:
    """Pergunta de nível num idioma que não está no seu perfil: escolhe "não falo" se o formulário tiver."""
    asked = next((v for k, v in LANG_NAMES.items() if re.search(rf"\b{k}\b", label_n)), None)
    if asked is None:
        return None
    known = {LANG_NAMES.get(normalize_text(lang.name), normalize_text(lang.name)) for lang in master.languages}
    if asked in known:
        return None
    hits = [o for o in f.options if NO_LANGUAGE_RE.search(normalize_text(o))]
    return hits[0] if len(hits) == 1 else None


def current_salary_text(comp: dict[str, Any]) -> str | None:
    """Remuneração atual em uma linha, do cadastro em Ofertas. Sem cadastro, fica para você."""
    gross = float(comp.get("monthly_gross") or 0)
    if gross <= 0:
        return None

    def brl(v: float) -> str:
        return f"R$ {v:,.0f}".replace(",", ".")

    parts = [f"{comp.get('regime') or 'CLT'} {brl(gross)} bruto/mês"]
    if comp.get("meal_monthly"):
        parts.append(f"VR/VA {brl(float(comp['meal_monthly']))}/mês")
    if comp.get("plr_salaries"):
        parts.append(f"PLR ~{str(comp['plr_salaries']).replace('.', ',')} salário(s)/ano")
    bens = [b for b in comp.get("non_monetary") or [] if re.search(r"sa[uú]de|odonto|previd|wellhub|gympass|seguro", b, re.I)]
    if bens:
        parts.append(", ".join(bens[:5]))
    return " + ".join(parts)


def profile_value(kind: str, master: MasterProfile) -> str | None:
    c = master.contact
    parts = c.full_name.split()
    return {
        "first_name": parts[0],
        "last_name": " ".join(parts[1:]) or parts[0],
        "full_name": c.full_name,
        "email": c.email,
        "country_code": "+55" if (c.phone or "").startswith("+55") else None,
        "phone": c.phone,
        "city": (c.location or "").split(",")[0] or None,
        "linkedin": f"https://{c.linkedin}" if c.linkedin and not c.linkedin.startswith("http") else c.linkedin,
        "github": f"https://{c.github}" if c.github and not c.github.startswith("http") else c.github,
        "portfolio": f"https://{c.portfolio}" if c.portfolio and not c.portfolio.startswith("http") else c.portfolio,
        "headline": next(iter(master.headlines.values()), None),
    }.get(kind)


YEARS_RE = re.compile(
    r"(how many years|quantos anos|years of (?:work |professional )?experience|anos de experi[eê]ncia|"
    r"experience (?:do you have )?(?:with|in)|tempo de experi[eê]ncia)",
    re.I,
)
SKILL_AFTER = re.compile(r"(?:with|in|using|com|em|de|no|na)\s+([A-Za-z0-9#+./ \-]{1,40}?)\s*(?:\?|$|\(|,)", re.I)
LANG_RE = re.compile(r"(english|ingl[eê]s|portugu[eê]s|portuguese|spanish|espanhol)", re.I)
SALARY_RE = re.compile(r"(salary|sal[aá]ri|salarial|pretens|remunera|compensation|expected pay|\bctc\b|faixa salarial)", re.I)
NOTICE_RE = re.compile(
    r"(notice period|aviso pr[eé]vio|when can you start|start date|disponibilidade para in[ií]cio|quando pode come[cç]ar)",
    re.I,
)
REMOTE_RE = re.compile(r"(remote|remoto|home office)", re.I)
HYBRID_RE = re.compile(r"(hybrid|h[ií]brido|presencial|on-?site|office)", re.I)
RELOCATE_RE = re.compile(r"(relocat|mudar de cidade|mudan[cç]a|reloca)", re.I)
BR_AUTH_RE = re.compile(
    r"(authori[sz]ed|autoriza[cç][aã]o|legally|legalmente|permiss[aã]o).*(brazil|brasil)|(brazil|brasil).*(authori[sz]ed|autoriza|legal)",
    re.I,
)
COVER_RE = re.compile(
    r"(cover letter|carta de apresenta|why (do you want|are you interested)|por que (voc[eê] )?(quer|deseja|se interessa)|"
    r"(fale|conte|escreva)( um pouco)? sobre (voc[eê]|sua trajet[oó]ria)|saber mais sobre voc[eê]|apresente-se|"
    r"tell us (a bit |more )?about yourself|introduce yourself)",
    re.I,
)


# "experiência na área de tecnologia", "em TI", "CLT", "profissional": tempo total, não uma skill
GENERIC_AREA = {"work", "professional", "experience", "experiencia", "total", "tecnologia", "technology", "tech", "ti", "it",
                "area", "software", "desenvolvimento", "development", "programacao", "programming", "clt", "mercado",
                "industry", "field", "carreira", "career", "profissional"}  # fmt: skip
BIRTHPLACE_RE = re.compile(
    r"(cidade|estado|local|city|place|uf).{0,30}(nasc|birth)|naturalidade|(nasc|birth).{0,20}(cidade|local|city|place)"
)
BIRTHDATE_RE = re.compile(r"data de nascimento|date of birth|birth ?date|\bdob\b|nascido em|quando voce nasceu")


def _years_answer(label: str, master: MasterProfile) -> tuple[int, str | None] | None:
    if not YEARS_RE.search(label):
        return None
    m = SKILL_AFTER.search(label)
    skill = m.group(1).strip() if m else None
    if skill and set(normalize_text(skill).split()) & GENERIC_AREA:
        skill = None
    if skill:
        years = facts.years_for_skill(master, skill)
        if facts.has_skill(master, skill) is None:
            return 0, skill  # nunca inventar: 0 e registra o gap
        return math.floor(years), None
    return math.floor(facts.total_years(master)), None


async def _rules(
    f: FormField, master: MasterProfile, prefs: PreferenceData, job: Job, cover_letter: str | None
) -> tuple[Any, str | None] | None:
    label = f.label
    y = _years_answer(label, master)
    if y is not None:
        if f.kind in ("text", "textarea") and not y[1]:
            return (
                f"{y[0]} anos"
                if job.language == "pt" or re.search(r"[ãçéê]|anos|experi[eê]ncia", label, re.I)
                else f"{y[0]} years"
            ), None
        return y[0], y[1]
    if LANG_RE.search(label) and re.search(r"(level|n[ií]vel|proficien|fluen|speak|fala)", label, re.I):
        lang = LANG_RE.search(label).group(1).lower()  # type: ignore[union-attr]
        key = "english" if lang.startswith(("engl", "ingl")) else "portuguese" if lang.startswith("portu") else "spanish"
        level = next((x.level for x in master.languages if x.name.lower() == key), None)
        if level:
            return level, None
    if SALARY_RE.search(label):
        regime = (job.salary or {}).get("regime") or job.regime or (prefs.regimes[0] if prefs.regimes else "CLT")
        currency = (job.salary or {}).get("currency") or ("BRL" if job.language == "pt" else "USD")
        exp = (prefs.salary_expectation.get(regime) or prefs.min_salary.get(regime) or {}).get(currency)
        if exp:
            return int(exp), None
        return None
    if NOTICE_RE.search(label):
        days = prefs.notice_period_days
        if f.kind == "number":
            return days, None
        return (f"{days} dias" if job.language == "pt" else f"{days} days"), None
    if BR_AUTH_RE.search(label):
        return "Yes", None
    if RELOCATE_RE.search(label):
        return ("Yes" if prefs.relocation else "No"), None
    if REMOTE_RE.search(label) and re.search(r"(comfortable|confort|willing|dispon|aceita|ok with)", label, re.I):
        return ("Yes" if "remote" in prefs.work_models else "No"), None
    if HYBRID_RE.search(label) and re.search(r"(comfortable|confort|willing|dispon|aceita|ok with|able)", label, re.I):
        return ("Yes" if {"hybrid", "onsite"} & set(prefs.work_models) else "No"), None
    if COVER_RE.search(label) and cover_letter:
        return cover_letter, None
    return None


OVERRIDE_MIN_SIM = 0.95  # correção só passa na frente do perfil quando a pergunta é praticamente a mesma


async def bank_lookup(session: AsyncSession, f: FormField, job: Job) -> tuple[AnswerBankEntry, float] | None:
    q = norm_q(f.label)
    regime = job.regime or ""
    rows = (await session.scalars(select(AnswerBankEntry))).all()

    def cond_ok(e: AnswerBankEntry) -> bool:
        if e.language not in ("any", job.language):
            return False
        c = e.conditions or {}
        return not (c.get("regime") and c["regime"] != regime)

    for e in rows:
        if cond_ok(e) and (norm_q(e.canonical_question) == q or q in {norm_q(a) for a in e.aliases or []}):
            return e, 1.0
    if SALARY_RE.search(f.label) and not CURRENT_SALARY_RE.search(normalize_text(f.label)):
        # pretensão que você já respondeu antes ("Pretensão salarial" = "Qual sua expectativa salarial?"), no mesmo regime
        lab = normalize_text(f.label)
        want = "pj" if re.search(r"\bpj\b", lab) else "clt" if re.search(r"\bclt\b", lab) else (regime or "").lower()
        cands = [
            e
            for e in rows
            if cond_ok(e)
            and SALARY_RE.search(e.canonical_question)
            and not CURRENT_SALARY_RE.search(normalize_text(e.canonical_question))
            and e.source == "user"
        ]

        def reg(e: AnswerBankEntry) -> str:
            qn = normalize_text(e.canonical_question)
            return "pj" if re.search(r"\bpj\b", qn) else "clt" if re.search(r"\bclt\b", qn) else ""

        same = [e for e in cands if reg(e) == want] or [e for e in cands if not reg(e)]
        if same:
            return max(same, key=lambda e: e.updated_at), 0.95
    emb = embed_one(f.label)
    best: tuple[AnswerBankEntry, float] | None = None
    for e in rows:
        if not cond_ok(e) or e.question_embedding is None:
            continue
        kind_ok = (
            e.field_kind == f.kind
            or {e.field_kind, f.kind} <= {"text", "textarea"}
            or {e.field_kind, f.kind} <= {"select", "radio", "typeahead"}
        )
        if not kind_ok:
            continue
        sim = cosine(emb, list(e.question_embedding))
        if sim >= 0.90 and (best is None or sim > best[1]):
            best = (e, sim)
    return best


def _finalize(f: FormField, value: Any, prefer: list[str] | None = None) -> Any | None:
    """Ajusta o valor ao tipo do campo; None se não couber nas opções."""
    if f.kind in ("select", "radio", "typeahead") and f.options:
        return choose_option(f.options, value, prefer)
    if f.kind == "checkbox":
        if f.options:
            vals = value if isinstance(value, list) else [value]
            chosen = [o for v in vals if (o := choose_option(f.options, v))]
            return chosen or None
        return normalize_text(str(value)) in ("yes", "sim", "true", "1", "aceito", "concordo")
    if f.kind == "number":
        m = re.search(r"\d+(?:[.,]\d+)?", str(value))
        return m.group(0).replace(",", ".") if m else None
    return fit_length(str(value), f.max_length)


def fit_length(text: str, limit: int | None) -> str:
    """Cabe no limite do site cortando no fim de uma frase (ou de uma palavra), nunca no meio."""
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    if end >= limit * 0.5:
        return cut[: end + 1].rstrip()
    return cut[: cut.rfind(" ")].rstrip() if " " in cut else cut


async def deterministic(
    session: AsyncSession,
    f: FormField,
    master: MasterProfile,
    prefs: PreferenceData,
    job: Job,
    cover_letter: str | None,
    user_answers: dict[str, Any],
    out: Resolution,
) -> str:
    """Resolve sem IA. Retorna "resolved", "sensitive" ou "unresolved"."""
    label_n = normalize_text(f.label)
    if f.kind == "file":
        out.values.append(Resolved(f.key, "__resume__", "profile"))
        return "resolved"
    if f.label in user_answers:
        v = _finalize(f, user_answers[f.label])
        if v is not None:
            out.values.append(Resolved(f.key, v, "user"))
            return "resolved"
    # resposta que você já corrigiu nesta pergunta vence perfil e regras (o assistente errou antes)
    hit = await bank_lookup(session, f, job)
    if hit and (hit[0].conditions or {}).get("override") and hit[1] >= OVERRIDE_MIN_SIM:
        entry, conf = hit
        v = _finalize(f, entry.answer.get("value"), entry.answer.get("prefer"))
        if v is not None:
            entry.use_count += 1
            entry.last_used_at = datetime.now(UTC)
            out.values.append(Resolved(f.key, v, "bank", conf, entry.id))
            return "resolved"
    # nascimento: só do seu perfil (Perfil → Preferências); sem cadastro, fica para você
    if BIRTHPLACE_RE.search(label_n) or BIRTHDATE_RE.search(label_n):
        pv = prefs.birthplace if BIRTHPLACE_RE.search(label_n) else prefs.birth_date
        v = _finalize(f, pv) if pv else None
        if v is not None:
            out.values.append(Resolved(f.key, v, "profile"))
            return "resolved"
        return "sensitive"
    if f.kind in ("select", "radio") and f.options and (lang_opt := unknown_language_option(f, label_n, master)):
        out.values.append(Resolved(f.key, lang_opt, "profile"))
        return "resolved"
    sid = self_id_value(f, label_n, prefs, job.language)
    if sid is not None:
        out.values.append(Resolved(f.key, sid, "profile"))
        return "resolved"
    if f.kind == "checkbox" and not f.options and CONSENT_RE.search(label_n):
        if MARKETING_RE.search(label_n) or not f.required:
            return "sensitive"  # opt-in de marketing / opcional: quem decide é você
        out.values.append(Resolved(f.key, True, "rule"))
        return "resolved"
    if f.kind not in ("radio", "checkbox", "select") and CURRENT_COMPANY_RE.search(label_n):
        comp = await settings_store.get(session, "current_compensation")
        name = comp.get("company") or next((e.organization for e in master.experience if e.end is None), None)
        if name:
            out.values.append(Resolved(f.key, name, "profile"))
            return "resolved"
    if CURRENT_SALARY_RE.search(label_n):
        # salário atual NUNCA sai da pretensão. Opcional ("se sentir confortável"): em branco ajuda na negociação
        if f.required:
            text = current_salary_text(await settings_store.get(session, "current_compensation"))
            v = _finalize(f, text) if text else None
            if v is not None:
                out.values.append(Resolved(f.key, v, "profile"))
                return "resolved"
        return "sensitive"
    pkind = next((k for pat, k in PROFILE_PATTERNS if re.search(pat, label_n)), None)
    if pkind and f.kind not in ("radio", "checkbox"):
        pv = location_text(master.contact.location, job.language) if pkind == "location" else profile_value(pkind, master)
        if pv:
            v = _finalize(f, pv)
            if v is not None:
                out.values.append(Resolved(f.key, v, "profile"))
                return "resolved"
    if hit:
        entry, conf = hit
        v = _finalize(f, entry.answer.get("value"), entry.answer.get("prefer"))
        if v is not None:
            entry.use_count += 1
            entry.last_used_at = datetime.now(UTC)
            if conf < 1.0 and f.label not in (entry.aliases or []):
                entry.aliases = [*(entry.aliases or []), f.label]
            out.values.append(Resolved(f.key, v, "bank", conf, entry.id))
            return "resolved"
    rule = await _rules(f, master, prefs, job, cover_letter)
    if rule is not None:
        value, gap = rule
        if gap:
            out.gaps.append(gap)
        prefer = (
            ["Advanced", "Fluent", "Avançado", "Fluente", "Professional"]
            if isinstance(value, str) and value.lower() in ("advanced", "avançado")
            else None
        )
        v = _finalize(f, value, prefer)
        if v is not None:
            out.values.append(Resolved(f.key, v, "rule"))
            return "resolved"
    if profile_service.is_sensitive(f.label):
        return "sensitive"
    return "unresolved"


CONDITIONAL = re.compile(
    r"^(?:\d+\W*\s*)?(?:caso|se)\b.{0,40}\b(?:voce|vc)?\s*(?:seja|for|tenha|ja|trabalh|possu)"
    r"|^(?:\d+\W*\s*)?if you (?:are|have|were|currently)"
)


class BatchItem(BaseModel):
    key: str
    value: str
    option_index: int | None = None
    confidence: float = Field(ge=0, le=1)


class BatchAnswers(BaseModel):
    answers: list[BatchItem]


async def ai_batch(session: AsyncSession, fields: list[FormField], master: MasterProfile, job: Job) -> dict[str, BatchItem]:
    """UMA chamada para todos os campos pendentes de um formulário (economia de tokens)."""
    if not fields:
        return {}
    qs = "\n".join(
        f'<question key="{f.key}" kind="{f.kind}" required="{f.required}"'
        + (f' max_chars="{f.max_length}"' if f.max_length else "")
        + f">{f.label}"
        + (" | opções: " + " ; ".join(f"{i}={o}" for i, o in enumerate(f.options)) if f.options else "")
        + "</question>"
        for f in fields
    )
    user = (
        f"<job>Vaga: {job.title or 'n/d'} · Senioridade: {job.seniority or 'n/d'} · Idioma: {job.language} · "
        f"Requisitos: {job.requirements or {}}</job>\n{qs}\n"
        "Responda cada pergunta pelo `key`."
    )
    try:
        res = await get_ai(sessionmaker_from(session)).structured(
            AIRequest(
                task="apply.answer",
                system=load_prompt("apply.answer", "v4"),
                context="<profile>\n" + master.model_dump_json(exclude_none=True) + "\n</profile>",
                user=user,
                effort="low",
                max_tokens=3000,
                entity_type="job",
                entity_id=job.id,
                cache_ttl_days=60,  # mesma pergunta + mesma vaga = sem nova chamada
                prompt_version="v3",
            ),
            BatchAnswers,
        )
    except AIError as exc:
        log.info("IA indisponível para %d campo(s): %s", len(fields), exc)
        return {}
    return {a.key: a for a in res.output.answers}


def _ai_value(f: FormField, a: BatchItem) -> Any | None:
    value: Any = f.options[a.option_index] if a.option_index is not None and 0 <= a.option_index < len(f.options) else a.value
    return _finalize(f, value)


# ---------- "Qual é o seu atual ou último trabalho?" (Empresa, Posição, De, Até, trabalho atual) ----------
COMPANY_LBL = re.compile(r"^(nome da )?(empresa|company|employer|empregador|organizacao|organization)( atual| name)?$")
POSITION_LBL = re.compile(r"^(posicao|cargo|position|job title|title|funcao|role|cargo atual)$")
FROM_LBL = re.compile(r"^(de|from|desde|inicio|data de inicio|start|start date|data inicial|mes de inicio)$")
TO_LBL = re.compile(r"^(ate|to|fim|termino|data de (fim|saida|termino)|end|end date|data final)$")
CURRENT_LBL = re.compile(
    r"(este e o meu trabalho atual|trabalho aqui atualmente|emprego atual|current (job|position|role)"
    r"|i currently work|currently work here)"
)
FIRST_JOB_RE = re.compile(
    r"(primeiro (emprego|trabalho)|first (job|paid job|employment)).{0,40}(ano|year)"
    r"|(ano|year).{0,40}(primeiro (emprego|trabalho)|first job)"
)


def _clean(label: str) -> str:
    return normalize_text(re.sub(r"[*:]+\s*$", "", label)).strip()


def current_job(master: MasterProfile) -> Any | None:
    jobs = [e for e in master.experience if e.type == "employment" and e.start]
    if not jobs:
        return None
    return max(jobs, key=lambda e: ((e.end is None), facts.parse_month(e.end) or facts.parse_month(e.start)))


def _title_for(exp: Any, job: Job) -> str:
    """Cargo do mestre (ou alternativa) que mais se parece com o da vaga."""
    opts = [exp.title, *(exp.title_alternatives or [])]
    words = set(normalize_text(job.title or "").split())
    return max(opts, key=lambda o: len(set(normalize_text(o).split()) & words)) if words else exp.title


def _date_value(value: str | None, kind: str) -> str | None:
    d = facts.parse_month(value)
    if d is None:
        return None
    if kind == "date":
        return f"01/{d.month:02d}/{d.year}"
    if kind == "month":
        return f"{d.year}-{d.month:02d}"
    if kind == "number":
        return str(d.year)
    return f"{d.month:02d}/{d.year}"


def job_history(fields: list[FormField], master: MasterProfile, job: Job) -> tuple[dict[str, Any], set[str]]:
    """Valores do bloco de emprego atual e campos que ficam vazios de propósito (ex.: "Até" do emprego atual)."""
    values: dict[str, Any] = {}
    leave_empty: set[str] = set()
    starts = [d for e in master.experience if e.type in facts.PROFESSIONAL_TYPES and (d := facts.parse_month(e.start))]
    for f in fields:
        if FIRST_JOB_RE.search(normalize_text(f.label)) and starts and f.kind not in ("radio", "checkbox"):
            values[f.key] = str(min(starts).year)
    if not any(COMPANY_LBL.match(_clean(f.label)) for f in fields):
        return values, leave_empty
    exp = current_job(master)
    if exp is None:
        return values, leave_empty
    is_current = exp.end is None
    for f in fields:
        lab = _clean(f.label)
        if COMPANY_LBL.match(lab) and f.kind in ("text", "typeahead"):
            values[f.key] = exp.organization_short or exp.organization
        elif POSITION_LBL.match(lab) and f.kind in ("text", "typeahead"):
            values[f.key] = _title_for(exp, job)
        elif FROM_LBL.match(lab):
            if (v := _date_value(exp.start, f.kind)) is not None:
                values[f.key] = v
        elif TO_LBL.match(lab):
            if is_current:
                leave_empty.add(f.key)
            elif (v := _date_value(exp.end, f.kind)) is not None:
                values[f.key] = v
        elif f.kind == "checkbox" and CURRENT_LBL.search(lab):
            values[f.key] = is_current
    return values, leave_empty


async def resolve_core(
    session: AsyncSession,
    fields: list[FormField],
    job: Job,
    cover_letter: str | None = None,
    user_answers: dict[str, Any] | None = None,
    allow_ai: bool = True,
) -> tuple[Resolution, dict[str, BatchItem]]:
    """Perfil → banco → regras → IA (em lote, só não sensíveis). Retorna também as sugestões de baixa confiança."""
    _, master = await profile_service.current_master(session)
    prefs = await profile_service.get_preferences(session)
    cfg = await settings_store.get(session, "answers")
    out = Resolution()
    user_answers = user_answers or {}
    unresolved: list[FormField] = []
    history, leave_empty = job_history(fields, master, job)
    for f in fields:
        if f.key in history:
            v = _finalize(f, history[f.key])
            if v is not None:
                out.values.append(Resolved(f.key, v, "profile"))
                continue
        if f.key in leave_empty:
            continue  # "Até" do emprego atual: fica vazio de propósito (nem você nem a IA precisam preencher)
        state = await deterministic(session, f, master, prefs, job, cover_letter, user_answers, out)
        if state == "sensitive":
            out.pending.append(f)
        elif state == "unresolved":
            if not f.required and f.current_value:
                out.values.append(Resolved(f.key, f.current_value, "profile", 0.9))
            else:
                unresolved.append(f)
    answers = await ai_batch(session, unresolved, master, job) if allow_ai else {}
    usable: dict[str, BatchItem] = {}  # só respostas que passaram no validador de fatos viram sugestão (regra 2)
    for f in unresolved:
        a = answers.get(f.key)
        v = _ai_value(f, a) if a else None
        if v is not None and f.kind in ("text", "textarea"):
            v = humanize(str(v))
            if a is not None:
                a = a.model_copy(update={"value": v})
        if v is not None and not str(v).strip():
            v = None  # a IA disse que não se aplica ou que o dado não está no perfil: fica para você
        ok_text = v is not None and (f.kind not in ("text", "textarea") or validate_free_text(str(v), master).passed)
        if a and ok_text:
            usable[f.key] = a
        # pergunta condicional ("Caso você seja…"): só preenche sozinho com certeza alta
        need = (
            max(float(cfg["min_confidence"]), 0.9)
            if CONDITIONAL.search(normalize_text(f.label))
            else float(cfg["min_confidence"])
        )
        if a and ok_text and a.confidence >= need:
            out.values.append(Resolved(f.key, v, "ai", a.confidence))
            continue
        out.pending.append(f.model_copy())
    return out, usable


async def resolve_fields(
    session: AsyncSession,
    app: Application,
    fields: list[FormField],
    step_index: int,
    user_answers: dict[str, Any] | None = None,
) -> Resolution:
    job = await session.get(Job, app.job_id)
    assert job is not None
    out, _ = await resolve_core(session, fields, job, app.cover_letter, user_answers)
    await persist(session, app, fields, out, step_index)
    return out


async def ai_answer(session: AsyncSession, f: FormField, master: MasterProfile, job: Job) -> FieldAnswer | None:
    items = await ai_batch(session, [f], master, job)
    a = items.get(f.key)
    if a is None:
        return None
    return FieldAnswer(value=a.value, option_index=a.option_index, confidence=a.confidence, rationale="")


async def persist(session: AsyncSession, app: Application, fields: list[FormField], res: Resolution, step: int) -> None:
    by_key = {f.key: f for f in fields}
    existing = (
        await session.scalars(
            select(ApplicationAnswer).where(ApplicationAnswer.application_id == app.id, ApplicationAnswer.step_index == step)
        )
    ).all()
    for e in existing:
        if not e.locked:
            await session.delete(e)
    for r in res.values:
        f = by_key[r.key]
        session.add(
            ApplicationAnswer(
                application_id=app.id,
                step_index=step,
                question_text=f.label,
                field_kind=f.kind,
                options=f.options,
                answer={"value": r.value},
                resolution=r.resolution,
                confidence=r.confidence,
                answer_bank_entry_id=r.bank_id,
            )
        )
    cfg = await settings_store.get(session, "answers")
    for f in res.pending:
        exists = await session.scalar(
            select(PendingQuestion).where(
                PendingQuestion.application_id == app.id,
                PendingQuestion.question_text == f.label,
                PendingQuestion.status == "open",
            )
        )
        if exists:
            continue
        session.add(
            PendingQuestion(
                application_id=app.id,
                question_text=f.label,
                field_kind=f.kind,
                options=f.options,
                context={"step": step, "key": f.key, "required": f.required},
                ai_suggestion={"value": f.current_value} if f.current_value else None,
                expires_at=datetime.now(UTC) + timedelta(hours=int(cfg["pending_expire_hours"])),
            )
        )
    await session.flush()
