from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tracker.core.db import Base, IdMixin, TimestampMixin, utcnow
from tracker.core.types import EMBEDDING_DIM, TextArray


class SavedSearch(IdMixin, TimestampMixin, Base):
    __tablename__ = "saved_search"
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sources: Mapped[list[str]] = mapped_column(TextArray, default=lambda: ["monitored"])
    schedule_cron: Mapped[str] = mapped_column(Text, default="0 9,15 * * 1-5")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_linkedin_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(IdMixin, TimestampMixin, Base):
    __tablename__ = "job"
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("company.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text, index=True)
    location: Mapped[str | None] = mapped_column(Text)
    work_model: Mapped[str | None] = mapped_column(Text)  # remote|hybrid|onsite
    regime: Mapped[str | None] = mapped_column(Text)  # CLT|PJ|contract|internship|unknown
    seniority: Mapped[str | None] = mapped_column(Text)
    salary: Mapped[dict[str, Any] | None]
    requirements: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {must:[], nice:[]}
    benefits: Mapped[list[str]] = mapped_column(TextArray, default=list)
    language: Mapped[str] = mapped_column(Text, default="en")
    apply_kind: Mapped[str] = mapped_column(Text, default="unknown")
    ats_kind: Mapped[str | None] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applicants_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="open", index=True)
    triage: Mapped[str] = mapped_column(Text, default="discovered", index=True)  # discovered|new|approved|skipped
    canonical_url: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text, default="")
    recruiter: Mapped[dict[str, Any] | None]
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    repost_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)


class JobSource(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_source"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    saved_search_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_search.id", ondelete="SET NULL")
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)


class JobSnapshot(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_snapshot"
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)  # collected | applied
    description_text: Mapped[str] = mapped_column(Text)
    description_html_sanitized: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sha256: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


class JobFeedback(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_feedback"
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[str] = mapped_column(Text)  # good | not_interested
    reason: Mapped[str | None] = mapped_column(Text)
