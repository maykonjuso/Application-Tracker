"""Vagas: upsert com deduplicação, snapshots e aplicação da extração por IA (RF-VAG-06..08)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracker.core import events
from tracker.core.tasks import dispatch_after_commit
from tracker.integrations.embeddings import cosine, embed_one
from tracker.modules.companies import service as companies
from tracker.modules.jobs import normalize as nz
from tracker.modules.jobs.models import Job, JobSnapshot, JobSource
from tracker.modules.jobs.schemas import JobNormalized

DUP_THRESHOLD = 0.92


def job_text_for_embedding(title: str, text: str) -> str:
    return f"{title}\n{text[:4000]}"


async def snapshot(session: AsyncSession, job: Job, reason: str, html: str | None = None) -> JobSnapshot:
    digest = hashlib.sha256(job.description_text.encode()).hexdigest()
    if reason == "collected":
        last = await session.scalar(
            select(JobSnapshot).where(JobSnapshot.job_id == job.id).order_by(JobSnapshot.captured_at.desc()).limit(1)
        )
        if last is not None and last.sha256 == digest:
            return last
    snap = JobSnapshot(
        job_id=job.id,
        reason=reason,
        description_text=job.description_text,
        description_html_sanitized=nz.sanitize_html(html) if html else None,
        sha256=digest,
        data={
            "title": job.title,
            "requirements": job.requirements,
            "salary": job.salary,
            "location": job.location,
            "work_model": job.work_model,
        },
    )
    session.add(snap)
    await session.flush()
    return snap


def posted_at_of(raw: nz.RawJob, text: str, now: datetime) -> datetime | None:
    """Data da fonte; sem ela, a que aparece no anúncio ("há 3 dias", "Publicada em 12/10/2026")."""
    return nz.parse_date(raw.posted_at, now) or nz.find_posted_at(f"{raw.title}\n{text}", now)


async def _fill_missing(session: AsyncSession, job: Job, raw: nz.RawJob, now: datetime) -> None:
    """Vaga já conhecida: completa o que faltava (data de publicação, descrição vazia). Não reescreve nada."""
    text = raw.description_text or nz.html_to_text(raw.description_html or "")
    if job.posted_at is None:
        job.posted_at = posted_at_of(raw, text, now)
    if len(job.description_text or "") < 50 and len(text) > len(job.description_text or ""):
        job.description_text = text
        job.embedding = embed_one(job_text_for_embedding(job.title, text))
        job.extra = {**(job.extra or {}), "needs_extraction": True}
        await snapshot(session, job, "collected", raw.description_html)
        dispatch_after_commit(session, "ai.process_job", job_id=str(job.id))


async def upsert_job(session: AsyncSession, raw: nz.RawJob, saved_search_id: object = None) -> tuple[Job, bool]:
    """Retorna (vaga canônica, criada?)."""
    now = datetime.now(UTC)
    src = await session.scalar(select(JobSource).where(JobSource.source == raw.source, JobSource.external_id == raw.external_id))
    if src is not None:
        job = await session.get(Job, src.job_id)
        assert job is not None
        job.last_seen_at = now
        src.seen_at = now
        if raw.applicants_count is not None:
            job.applicants_count = raw.applicants_count
        await _fill_missing(session, job, raw, now)
        if job.status == "closed":
            job.status = "open"
        return job, False

    company = await companies.get_or_create(session, raw.company_name, domain=raw.company_domain)
    text = raw.description_text or nz.html_to_text(raw.description_html or "")
    ntitle = nz.normalize_title(raw.title)
    emb = embed_one(job_text_for_embedding(raw.title, text))

    candidates = (await session.scalars(select(Job).where(Job.company_id == company.id, Job.normalized_title == ntitle))).all()
    for cand in candidates:
        if cosine(emb, list(cand.embedding) if cand.embedding is not None else None) >= DUP_THRESHOLD:
            same_source = await session.scalar(
                select(JobSource.id).where(JobSource.job_id == cand.id, JobSource.source == raw.source)
            )
            if same_source:
                cand.repost_count += 1  # mesma fonte, id novo: repost (sinal de vaga fantasma)
            session.add(
                JobSource(
                    job_id=cand.id,
                    source=raw.source,
                    external_id=raw.external_id,
                    url=raw.url,
                    saved_search_id=saved_search_id,
                    raw=raw.raw,
                )
            )
            cand.last_seen_at = now
            if cand.status == "closed":
                cand.status = "open"
            await _fill_missing(session, cand, raw, now)
            await session.flush()
            return cand, False

    full = f"{raw.title}\n{raw.location or ''}\n{text}"
    apply_kind, ats_kind = (
        (raw.apply_kind, raw.ats_kind) if raw.apply_kind else nz.detect_apply_kind(raw.apply_url or raw.url, text)
    )
    job = Job(
        company_id=company.id,
        title=raw.title.strip(),
        normalized_title=ntitle,
        location=raw.location,
        work_model=raw.work_model or nz.detect_work_model(full),
        regime=nz.detect_regime(full),
        seniority=nz.detect_seniority(raw.title, text),
        salary=raw.salary or nz.detect_salary(text),
        requirements={"must": [], "nice": []},
        language=nz.detect_language(text),
        apply_kind=apply_kind or "unknown",
        ats_kind=ats_kind or raw.ats_kind,
        apply_url=raw.apply_url or raw.url,
        posted_at=posted_at_of(raw, text, now),
        applicants_count=raw.applicants_count,
        canonical_url=raw.url,
        description_text=text,
        recruiter=raw.recruiter,
        embedding=emb,
        first_seen_at=now,
        last_seen_at=now,
        extra={"needs_extraction": True},
    )
    session.add(job)
    await session.flush()
    session.add(
        JobSource(
            job_id=job.id,
            source=raw.source,
            external_id=raw.external_id,
            url=raw.url,
            saved_search_id=saved_search_id,
            raw=raw.raw,
        )
    )
    await snapshot(session, job, "collected", raw.description_html)
    return job, True


def apply_extraction(job: Job, x: JobNormalized) -> None:
    def keep(v: str | None) -> str | None:
        return None if v in (None, "unknown") else v

    job.work_model = keep(x.work_model) or job.work_model
    job.regime = keep(x.regime) or job.regime
    job.seniority = keep(x.seniority) or job.seniority
    if x.location:
        job.location = x.location
    if x.salary and (x.salary.min or x.salary.max):
        job.salary = {**x.salary.model_dump(), "estimated": False}
    job.requirements = {
        "must": x.requirements_must,
        "nice": x.requirements_nice,
        "years": x.years_required,
        "languages": x.languages_required,
    }
    job.benefits = x.benefits
    job.language = x.language
    if job.apply_kind in ("unknown", None) and x.apply_kind != "unknown":
        job.apply_kind = x.apply_kind
    if x.recruiter_name:
        job.recruiter = {**(job.recruiter or {}), "name": x.recruiter_name}
    job.extra = {
        **(job.extra or {}),
        "needs_extraction": False,
        "hard_blockers": x.hard_blockers,
        "scam_signals": x.scam_signals,
        "is_generic": x.is_generic,
    }


async def publish_job(job: Job, type_: str = "job.updated") -> None:
    await events.apublish("jobs", type_, {"job_id": str(job.id), "title": job.title})
