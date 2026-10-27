"""Schema do currículo mestre (docs/11-curriculos.md §2) e DTOs do perfil."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _as_str(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


class Contact(BaseModel):
    model_config = ConfigDict(extra="allow")
    full_name: str
    display_name: str | None = None
    email: str
    phone: str | None = None
    location: str | None = None
    remote: str | None = None
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None


class Experience(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    title: str
    title_alternatives: list[str] = Field(default_factory=list)
    organization: str
    organization_short: str | None = None
    org_line_en: str | None = None
    org_line_pt: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    type: str = "employment"
    bullets_en: list[str] = Field(default_factory=list)
    bullets_pt: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    extra_facts: list[str] = Field(default_factory=list)
    confirmar: list[str] | str | None = None

    _norm_dates = field_validator("start", "end", mode="before")(_as_str)

    @field_validator("confirmar", mode="before")
    @classmethod
    def _clean_confirm(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [x for x in v if x] or None
        return v or None


class Project(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    url: str | None = None
    description_en: str = ""
    description_pt: str | None = None
    skills: list[str] = Field(default_factory=list)


class Education(BaseModel):
    model_config = ConfigDict(extra="allow")
    degree: str
    institution: str
    location: str | None = None
    start: str | None = None
    end: str | None = None
    status: str | None = None
    show_by_default: bool = True

    _norm_dates = field_validator("start", "end", mode="before")(_as_str)


class Certification(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    issuer: str | None = None
    year: int | None = None
    show_by_default: bool = True


class Language(BaseModel):
    name: str
    level: str | None = None


class MasterProfile(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1
    contact: Contact
    headlines: dict[str, str] = Field(default_factory=dict)
    summaries: dict[str, str] = Field(default_factory=dict)  # chave: <perfil>_<idioma>
    summary_facts: dict[str, Any] = Field(default_factory=dict)
    skills: dict[str, list[str]] = Field(default_factory=dict)
    skill_aliases: dict[str, list[str]] = Field(default_factory=dict)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)

    def experience_by_id(self, exp_id: str) -> Experience | None:
        return next((e for e in self.experience if e.id == exp_id), None)

    def pending_confirmations(self) -> list[dict[str, Any]]:
        out = []
        for e in self.experience:
            if e.confirmar:
                items = e.confirmar if isinstance(e.confirmar, list) else [e.confirmar]
                out.append({"experience_id": e.id, "items": items})
        return out


class MasterVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: int
    source: str
    change_note: str | None
    is_current: bool
    created_at: datetime


class MasterOut(BaseModel):
    version: int
    data: MasterProfile
    pending_confirmations: list[dict[str, Any]]
    total_years: float


class MasterIn(BaseModel):
    data: MasterProfile
    change_note: str | None = None


class DiffEntry(BaseModel):
    path: str
    before: Any = None
    after: Any = None


class PreferenceData(BaseModel):
    model_config = ConfigDict(extra="allow")
    target_roles: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=lambda: ["mid", "senior"])
    stacks: list[str] = Field(default_factory=list)
    work_models: list[str] = Field(default_factory=lambda: ["remote", "hybrid"])
    locations: list[str] = Field(default_factory=list)
    regimes: list[str] = Field(default_factory=lambda: ["CLT", "PJ"])
    min_salary: dict[str, dict[str, float]] = Field(default_factory=dict)
    salary_expectation: dict[str, dict[str, float]] = Field(default_factory=dict)
    sectors_preferred: list[str] = Field(default_factory=list)
    sectors_avoided: list[str] = Field(default_factory=list)
    company_sizes: list[str] = Field(default_factory=list)
    relocation: bool = False
    travel: bool = True
    notice_period_days: int = 30
    timezone: str = "America/Sao_Paulo"
    international: bool = True
    linkedin_risk_accepted: bool = False
    # dados pessoais que formulários pedem (preenchidos só a partir daqui, nunca pela IA)
    birth_date: str | None = None  # dd/mm/aaaa
    birthplace: str | None = None  # "Cidade, UF"
    # autodeclaração (só preenche formulário quando perguntam; nunca vai para a IA nem para o CV)
    gender_identity: str | None = None  # ex.: "Homem cisgênero"
    sexual_orientation: str | None = None  # ex.: "Bissexual"
    race: str | None = None  # ex.: "Parda"
    lgbtqia: bool | None = None
    disability: bool | None = None  # pessoa com deficiência (PcD)


class AnswerIn(BaseModel):
    canonical_question: str
    field_kind: str = "text"
    answer: dict[str, Any]
    conditions: dict[str, Any] = Field(default_factory=dict)
    language: str = "any"
    sensitive: bool = False
    aliases: list[str] = Field(default_factory=list)


class AnswerOut(AnswerIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source: str
    use_count: int
    last_used_at: datetime | None


class BlockIn(BaseModel):
    kind: str = Field(pattern="^(company|domain|recruiter|keyword)$")
    value: str
    reason: str | None = None


class BlockOut(BlockIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class StarIn(BaseModel):
    title: str
    experience_id: str | None = None
    situation: str
    task: str
    action: str
    result: str
    competencies: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    language: str = "en"


class StarOut(StarIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class ReferenceIn(BaseModel):
    name: str
    relation: str
    contact: str
    consent: bool = False


class ReferenceOut(ReferenceIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    last_used_at: datetime | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    title: str
    mime: str
    sha256: str
    expires_at: datetime | None
    created_at: datetime


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    task_id: uuid.UUID | None = None
    filename: str
    status: str
    extracted: dict[str, Any] | None
    error: str | None
    created_at: datetime
