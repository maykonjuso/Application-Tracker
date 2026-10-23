"""Schemas de vagas (API e IA)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracker.integrations import job_sources


class XSalary(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: Literal["month", "year", "hour"] | None = None
    regime: Literal["CLT", "PJ", "contract", "unknown"] | None = None


class JobNormalized(BaseModel):
    """Saída de `job.extract` (docs/08 §4)."""

    title: str
    company_name: str
    location: str | None = None
    work_model: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    regime: Literal["CLT", "PJ", "contract", "internship", "unknown"] = "unknown"
    seniority: Literal["intern", "junior", "mid", "senior", "staff", "unknown"] = "unknown"
    salary: XSalary | None = None
    requirements_must: list[str] = Field(description="tecnologias/competências obrigatórias, nomes curtos")
    requirements_nice: list[str] = Field(description="desejáveis")
    years_required: int | None = None
    languages_required: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    language: Literal["pt", "en"] = "en"
    apply_kind: Literal["easy_apply", "external", "email", "unknown"] = "unknown"
    recruiter_name: str | None = None
    hard_blockers: list[str] = Field(
        default_factory=list, description="cidadania, visto, diploma específico, presencial obrigatório etc."
    )
    scam_signals: list[str] = Field(default_factory=list)
    is_generic: bool = False


class JobImportIn(BaseModel):
    url: str | None = None
    text: str | None = None
    company_name: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _one(self) -> JobImportIn:
        if not self.url and not self.text:
            raise ValueError("informe url ou text")
        return self


class JobSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source: str
    external_id: str
    url: str | None
    seen_at: datetime


class JobQualityCheck(BaseModel):
    key: str
    label: str
    status: Literal["ok", "warning", "risk", "unknown"]
    detail: str
    scoreable: bool = True


class JobQualityOut(BaseModel):
    score: int
    summary: str
    score_explanation: str
    checks: list[JobQualityCheck]


class ScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    chance: float
    attractiveness: float
    priority: float
    quadrant: str
    suggested_action: str
    knockout: dict[str, Any]
    chance_breakdown: dict[str, Any]
    attractiveness_breakdown: dict[str, Any]
    explanation: dict[str, Any]
    best_variant_id: uuid.UUID | None
    variant_scores: dict[str, Any]
    computed_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str | None = None
    title: str
    location: str | None
    work_model: str | None
    regime: str | None
    seniority: str | None
    salary: dict[str, Any] | None
    requirements: dict[str, Any]
    language: str
    apply_kind: str
    ats_kind: str | None
    apply_url: str | None
    canonical_url: str | None
    posted_at: datetime | None
    applicants_count: int | None
    status: str
    triage: str
    repost_count: int
    first_seen_at: datetime
    score: ScoreOut | None = None
    application_id: uuid.UUID | None = None
    application_status: str | None = None


class JobDetail(JobOut):
    description_text: str
    benefits: list[str]
    recruiter: dict[str, Any] | None
    extra: dict[str, Any]
    quality: JobQualityOut
    sources: list[JobSourceOut] = Field(default_factory=list)
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class FeedbackIn(BaseModel):
    verdict: Literal["good", "not_interested"]
    reason: str | None = None


class CustomJobSource(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2048)


class SavedSearchIn(BaseModel):
    name: str
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=lambda: ["monitored"])
    schedule_cron: str = "0 9,15 * * 1-5"
    is_active: bool = True

    @model_validator(mode="after")
    def validate_custom_sources(self) -> SavedSearchIn:
        raw_sources = self.filters.get("custom_sources", [])
        if not isinstance(raw_sources, list):
            raise ValueError("filters.custom_sources deve ser uma lista")
        custom_sources = [CustomJobSource.model_validate(source) for source in raw_sources]
        for source in custom_sources:
            parsed = job_sources.parse_ats_url(source.url)
            if parsed is None or parsed[0] not in job_sources.FETCHERS:
                raise ValueError(
                    f"Fonte personalizada não suportada: {source.url}. "
                    "Use um board público Greenhouse, Lever, Ashby ou Gupy."
                )
        if custom_sources:
            self.filters["custom_sources"] = [source.model_dump() for source in custom_sources]
            if "custom" not in self.sources:
                self.sources.append("custom")
        return self


class SavedSearchOut(SavedSearchIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    last_run_at: datetime | None
