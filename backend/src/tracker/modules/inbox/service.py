"""Inbox: pré-filtro, classificação, associação e efeitos (RF-ACO-03..07, RF-AGE-02..10)."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracker.core import audit, settings_store
from tracker.core.config import get_settings
from tracker.core.tasks import dispatch_after_commit
from tracker.integrations.claude.client import AIClient, AIRequest, load_prompt
from tracker.integrations.google.gmail import ParsedEmail
from tracker.modules.applications import service as app_service
from tracker.modules.applications.models import Application
from tracker.modules.applications.state_machine import ACTIVE, SENT
from tracker.modules.calendar import service as cal
from tracker.modules.calendar.models import CalendarEvent
from tracker.modules.companies.models import Company
from tracker.modules.companies.service import normalize_company
from tracker.modules.inbox.models import EmailMessage
from tracker.modules.inbox.schemas import EmailClassification, Extracted
from tracker.modules.interviews.models import Assessment, Interview
from tracker.modules.jobs.models import Job
from tracker.modules.notifications.service import notify
from tracker.modules.people.models import Person, PersonApplication

log = logging.getLogger(__name__)

ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "gupy.io",
    "myworkday.com",
    "workday.com",
    "smartrecruiters.com",
    "inhire.com.br",
    "inhire.app",
    "solides.com.br",
    "kenoby.com",
    "recrutei.com.br",
    "breezy.hr",
    "workable.com",
    "teamtailor.com",
    "pandape.com.br",
    "vagas.com.br",
    "jobvite.com",
    "icims.com",
    "bamboohr.com",
    "taleo.net",
    "successfactors.com",
    "hirebridge.com",
    "recruitee.com",
]
LINKEDIN_SENDERS = ["linkedin.com"]
KEYWORDS = [
    r"entrevista",
    r"interview",
    r"candidatura",
    r"application",
    r"processo seletivo",
    r"recrutamento",
    r"recruit",
    r"vaga",
    r"position",
    r"opportunit",
    r"oportunidade",
    r"proposta",
    r"offer",
    r"teste t[eé]cnico",
    r"desafio t[eé]cnico",
    r"assessment",
    r"take[- ]home",
    r"coding challenge",
    r"agendar",
    r"schedule",
    r"disponibilidade",
    r"availability",
    r"feedback",
    r"talent",
    r"hiring",
]
LABEL_TO_STATUS = {
    "confirmacao": "under_review",
    "rejeicao": "rejected",
    "convite_entrevista": "interviewing",
    "pedido_disponibilidade": "interviewing",
    "remarcacao": "interviewing",
    "teste_tecnico": "assessment",
    "oferta": "offer",
}
NOTIFY = {
    "convite_entrevista": "interview_invite",
    "pedido_disponibilidade": "availability_request",
    "teste_tecnico": "assessment",
    "oferta": "offer",
    "pedido_info": "availability_request",
    "recrutador_prospeccao": "top_job",
}


def sender_domain(addr: str) -> str:
    return addr.split("@")[-1].lower() if "@" in addr else ""


def prefilter(email: ParsedEmail, company_domains: set[str], company_names: set[str]) -> str | None:
    """Motivo para classificar (ou None = ignorar). Barato e amplo (docs/07 §1)."""
    dom = sender_domain(email.from_addr)
    if any(dom == d or dom.endswith("." + d) for d in ATS_DOMAINS):
        return "ats"
    if any(dom.endswith(d) for d in LINKEDIN_SENDERS):
        return "linkedin"
    if dom and any(dom == d or dom.endswith("." + d) for d in company_domains):
        return "company_domain"
    if "CATEGORY_PROMOTIONS" in email.labels or "CATEGORY_SOCIAL" in email.labels:
        return None
    text = f"{email.subject}\n{email.body_text[:3000]}".lower()
    norm_text = normalize_company(text)
    if any(n and len(n) > 3 and f" {n} " in f" {norm_text} " for n in company_names):
        if any(re.search(k, text) for k in KEYWORDS):
            return "company_mention"
    hits = sum(1 for k in KEYWORDS if re.search(k, text))
    return "keywords" if hits >= 2 else None


async def candidate_applications(session: AsyncSession, email: ParsedEmail) -> list[tuple[Application, Job, Company]]:
    rows = (
        await session.execute(
            select(Application, Job, Company)
            .join(Job, Job.id == Application.job_id)
            .join(Company, Company.id == Job.company_id)
            .where(Application.status.in_(SENT | ACTIVE))
        )
    ).all()
    if email.thread_id:
        same_thread = await session.scalar(
            select(EmailMessage.application_id)
            .where(EmailMessage.thread_id == email.thread_id, EmailMessage.application_id.is_not(None))
            .limit(1)
        )
        if same_thread:
            return [r for r in rows if r[0].id == same_thread]
    dom = sender_domain(email.from_addr)
    text = normalize_company(f"{email.from_name or ''} {email.subject} {email.body_text[:4000]}")
    scored = []
    for app, job, comp in rows:
        s = 0
        if dom and any(dom == d or dom.endswith("." + d) for d in comp.domains or []):
            s += 3
        if comp.normalized_name and f" {comp.normalized_name} " in f" {text} ":
            s += 2
        if job.normalized_title and job.normalized_title in text:
            s += 1
        if s:
            scored.append((s, app, job, comp))
    scored.sort(key=lambda x: (-x[0], -(x[1].last_activity_at.timestamp())))
    return [(a, j, c) for _, a, j, c in scored[:8]]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=cal.tz())


async def classify(
    ai: AIClient,
    email: ParsedEmail,
    candidates: list[tuple[Application, Job, Company]],
    email_row_id: uuid.UUID | None = None,
) -> EmailClassification:
    cands = (
        "\n".join(f'- id={a.id} empresa="{c.name}" vaga="{j.title}" status={a.status}' for a, j, c in candidates) or "(nenhuma)"
    )
    user = (
        f"Fuso do candidato: {get_settings().user_timezone}. Data de recebimento: {email.received_at.isoformat()}.\n"
        f"Candidaturas candidatas:\n{cands}\n\n"
        f"<email>\nDe: {email.from_name or ''} <{email.from_addr}>\nAssunto: {email.subject}\n\n"
        f"{email.body_text[:8000]}\n</email>"
    )
    res = await ai.structured(
        AIRequest(
            task="email.classify",
            system=load_prompt("email.classify"),
            user=user,
            effort="low",
            max_tokens=2000,
            entity_type="email_message",
            entity_id=email_row_id,
        ),
        EmailClassification,
    )
    return res.output


async def store_email(session: AsyncSession, email: ParsedEmail, reason: str | None) -> EmailMessage | None:
    if await session.scalar(select(EmailMessage.id).where(EmailMessage.gmail_id == email.gmail_id)):
        return None
    row = EmailMessage(
        gmail_id=email.gmail_id,
        thread_id=email.thread_id,
        from_addr=email.from_addr,
        from_name=email.from_name,
        to_addrs=email.to_addrs,
        subject=email.subject,
        snippet=email.snippet[:500],
        body_text=email.body_text[:20000] if reason else None,
        received_at=email.received_at,
        labels=email.labels,
        prefilter=reason,
        link_status="none",
        extracted={"message_id_header": email.message_id_header},
    )
    session.add(row)
    await session.flush()
    return row


async def ingest_linkedin_alert(session: AsyncSession, email: ParsedEmail) -> EmailMessage | None:
    """Alerta de vagas do LinkedIn: cada vaga do e-mail vira uma vaga em "Vagas" (sem IA para classificar)."""
    from tracker.core.tasks import dispatch_after_commit
    from tracker.modules.inbox.linkedin_alerts import parse_alert
    from tracker.modules.jobs import service as job_service
    from tracker.modules.jobs.normalize import RawJob

    row = await store_email(session, email, "linkedin_alert")
    if row is None:
        return None
    jobs = parse_alert(email)
    created = 0
    for a in jobs:
        url = f"https://www.linkedin.com/jobs/view/{a.external_id}/"
        job, is_new = await job_service.upsert_job(
            session,
            RawJob(source="linkedin", external_id=a.external_id, title=a.title, company_name=a.company, url=url,
                   location=a.location, apply_url=url, apply_kind="easy_apply" if a.easy_apply else "unknown"),
        )  # fmt: skip
        await session.flush()
        if is_new:
            created += 1
            dispatch_after_commit(session, "ai.process_job", job_id=str(job.id))
    row.classification, row.classification_confidence, row.link_status = "notificacao_linkedin", 1.0, "none"
    row.extracted = {**(row.extracted or {}), "summary": f"Alerta do LinkedIn: {len(jobs)} vaga(s), {created} nova(s)"}
    row.processed_at = datetime.now(UTC)
    return row


async def process_email(session: AsyncSession, ai: AIClient, email: ParsedEmail) -> EmailMessage | None:
    from tracker.modules.inbox.linkedin_alerts import is_linkedin_alert

    if is_linkedin_alert(email):
        return await ingest_linkedin_alert(session, email)
    companies = (await session.scalars(select(Company))).all()
    domains = {d for c in companies for d in (c.domains or [])}
    names = {c.normalized_name for c in companies}
    reason = prefilter(email, domains, names)
    row = await store_email(session, email, reason)
    if row is None or reason is None:
        if row is not None:
            row.processed_at = datetime.now(UTC)
        return row
    candidates = await candidate_applications(session, email)
    # nada bateu por domínio/nome: a IA escolhe entre as candidaturas recentes, mas só como proposta
    from_recent = not candidates
    if from_recent:
        candidates = await recent_applications(session)
    result = await classify(ai, email, candidates, row.id)
    row.classification = result.label
    row.classification_confidence = result.confidence
    row.extracted = {**row.extracted, **result.extracted.model_dump(), "summary": result.summary}
    cfg = await settings_store.get(session, "inbox")
    by_id = {str(a.id): (a, j, c) for a, j, c in candidates}
    match = by_id.get(result.application_match or "")
    # palpite pela única candidatura só com remetente autenticado (e-mail forjado não escolhe candidatura)
    if (
        match is None
        and not from_recent
        and email.authenticated
        and len(candidates) == 1
        and result.label != "recrutador_prospeccao"
    ):
        match = candidates[0]
    row.extracted = {**row.extracted, "sender_verified": email.authenticated}
    if result.label in ("irrelevante",):
        row.link_status = "none"
    elif not email.authenticated and result.label not in ("notificacao_linkedin",):
        # remetente não autenticado: nada automático (status, agenda, oportunidade); você confere no Inbox
        row.link_status = "ambiguous"
        await notify(
            session,
            "email_review",
            {"message": f"E-mail de remetente não verificado, confira: {email.subject}", "email_id": str(row.id)},
            telegram_too=result.label in NOTIFY,
        )
    elif match and not from_recent and result.confidence >= cfg["min_confidence"]:
        row.application_id = match[0].id
        row.link_status = "auto"
        await apply_outcome(session, row, result, match[0], match[1], match[2])
    elif result.label == "recrutador_prospeccao" and result.confidence >= cfg["min_confidence"]:
        await inbound_opportunity(session, row, result)
    elif result.label == "notificacao_linkedin":
        row.link_status = "none"
    else:
        row.link_status = "ambiguous"
        await notify(
            session,
            "email_review",
            {"message": f"E-mail precisa de revisão: {email.subject}", "email_id": str(row.id)},
            telegram_too=result.label in NOTIFY,
        )
    if row.link_status == "ambiguous":
        row.extracted = {**row.extracted, "suggestion": suggestion(result, match)}
    row.processed_at = datetime.now(UTC)
    return row


# rótulos que falam de um processo seletivo seu (dá para propor associar ou criar a candidatura)
PROCESS_LABELS = {
    "confirmacao", "rejeicao", "convite_entrevista", "pedido_disponibilidade", "teste_tecnico",
    "oferta", "pedido_info", "remarcacao", "cancelamento",
}  # fmt: skip


async def recent_applications(session: AsyncSession, limit: int = 15) -> list[tuple[Application, Job, Company]]:
    rows = (
        await session.execute(
            select(Application, Job, Company)
            .join(Job, Job.id == Application.job_id)
            .join(Company, Company.id == Job.company_id)
            .where(Application.status.in_(SENT | ACTIVE), Application.submitted_at.is_not(None))
            .order_by(Application.last_activity_at.desc())
            .limit(limit)
        )
    ).all()
    return [(a, j, c) for a, j, c in rows]


def suggestion(result: EmailClassification, match: tuple[Application, Job, Company] | None) -> dict[str, Any] | None:
    """O que a IA propõe para um e-mail que não foi associado sozinho. Você confirma no Inbox."""
    if match is not None:
        app, job, comp = match
        return {
            "kind": "link",
            "application_id": str(app.id),
            "company": comp.name,
            "job_title": job.title,
            "confidence": result.confidence,
        }
    x = result.extracted
    if result.label in PROCESS_LABELS and x.company_name:
        return {"kind": "create", "company": x.company_name, "job_title": x.job_title, "confidence": result.confidence}
    return None


async def create_application_from_email(session: AsyncSession, row: EmailMessage) -> Application:
    """Processo feito fora do tracker (a resposta chegou no e-mail): vira candidatura enviada e o e-mail é associado."""
    from tracker.modules.jobs import service as job_service
    from tracker.modules.jobs.normalize import RawJob

    x = row.extracted or {}
    company = (x.get("company_name") or row.from_name or sender_domain(row.from_addr).split(".")[0] or "Empresa").strip()
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="email",
            external_id=f"app:{row.gmail_id}",
            title=(x.get("job_title") or row.subject or "Vaga (pelo e-mail)")[:300],
            company_name=company,
            description_text=row.body_text or row.snippet,
        ),
    )
    app = await app_service.create_for_job(session, job, channel="external", actor="user")
    # o envio foi antes deste e-mail; a data exata não temos, a do e-mail é o limite superior
    await app_service.register_submission(session, app, actor="user", channel="external", submitted_at=row.received_at)
    await audit.record(
        session,
        actor="user",
        action="inbox.create_application",
        entity_type="email_message",
        entity_id=row.id,
        after={"application_id": str(app.id), "company": company},
    )
    await relink(session, row, app)
    return app


async def apply_outcome(
    session: AsyncSession, row: EmailMessage, result: EmailClassification, app: Application, job: Job, company: Company
) -> None:
    label = result.label
    x = result.extracted
    await app_service.add_event(
        session,
        app,
        "email_in",
        {"email_id": str(row.id), "subject": row.subject, "label": label, "summary": result.summary},
        occurred_at=row.received_at,
    )
    new_status = LABEL_TO_STATUS.get(label)
    if new_status and new_status != app.status:
        try:
            await app_service.transition(session, app, new_status, reason=f"e-mail: {result.summary}")
        except Exception as exc:
            log.info("transição ignorada (%s → %s): %s", app.status, new_status, exc)
    for p in x.people:
        await upsert_person(
            session, p.name, p.role, p.email, company, app, "recruiter" if not x.interview_kind else "interviewer"
        )
    title = f"{company.name}: {job.title}"
    if label in ("convite_entrevista", "remarcacao") and x.datetimes:
        await schedule_interview(session, row, app, company, job, result)
    elif label == "convite_entrevista" or label == "pedido_disponibilidade":
        from tracker.modules.inbox.drafts import availability_draft

        await availability_draft(session, row, app, company, job, x.duration_min or 60)
    elif label == "cancelamento":
        for ev in (
            await session.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.application_id == app.id,
                    CalendarEvent.kind == "interview",
                    CalendarEvent.status != "cancelled",
                    CalendarEvent.starts_at > datetime.now(UTC),
                )
            )
        ).all():
            await cal.cancel(session, ev)
    elif label == "teste_tecnico":
        deadline = _parse_dt(x.deadline)
        session.add(Assessment(application_id=app.id, instructions=result.summary, deadline_at=deadline))
        if deadline:
            await cal.create_event(
                session,
                application_id=app.id,
                kind="assessment_deadline",
                title=f"Prazo do teste: {title}",
                start=deadline - timedelta(minutes=30),
                end=deadline,
                description=result.summary,
                source_email_id=row.id,
            )
    elif label == "oferta":
        app.next_action = "registrar e comparar a proposta"
    elif label == "rejeicao":
        app.outcome_reason = result.summary
    if label in NOTIFY:
        await notify(
            session,
            NOTIFY[label],
            {"company": company.name, "title": job.title, "message": result.summary, "application_id": str(app.id)},
        )


async def schedule_interview(
    session: AsyncSession, row: EmailMessage, app: Application, company: Company, job: Job, result: EmailClassification
) -> None:
    x = result.extracted
    first = x.datetimes[0]
    start = _parse_dt(first.start)
    if start is None:
        return
    end = _parse_dt(first.end) or start + timedelta(minutes=x.duration_min or 60)
    # várias opções de horário ou baixa confiança → rascunho para confirmar (RF-AGE-09)
    ambiguous = len(x.datetimes) > 1 or result.confidence < 0.85
    if result.label == "remarcacao":
        existing = await session.scalar(
            select(CalendarEvent)
            .where(
                CalendarEvent.application_id == app.id,
                CalendarEvent.kind == "interview",
                CalendarEvent.status != "cancelled",
            )
            .order_by(CalendarEvent.starts_at.desc())
            .limit(1)
        )
        if existing:
            await cal.confirm(session, existing, start, end)
            return
    rounds = len((await session.scalars(select(Interview).where(Interview.application_id == app.id))).all())
    interview = Interview(
        application_id=app.id,
        round=rounds + 1,
        kind=x.interview_kind or "hr",
        starts_at=start,
        interviewers=[p.model_dump() for p in x.people],
    )
    session.add(interview)
    await session.flush()
    url = f"{get_settings().app_base_url}/entrevistas/{interview.id}"
    ev = await cal.create_event(
        session,
        application_id=app.id,
        kind="interview",
        title=f"Entrevista {interview.kind}: {company.name} ({job.title})",
        start=start,
        end=end,
        description=f"{result.summary}\nDossiê: {url}",
        link=x.meeting_link,
        attendees=[p.model_dump() for p in x.people],
        source_email_id=row.id,
        draft=ambiguous,
        meta={"interview_id": str(interview.id)},
    )
    interview.scheduled_event_id = ev.id
    dispatch_after_commit(session, "ai.build_dossier", application_id=str(app.id), interview_id=str(interview.id))
    if not ambiguous:
        await cal.ensure_study_block(session, ev, url)


async def upsert_person(
    session: AsyncSession,
    name: str,
    role: str | None,
    email: str | None,
    company: Company | None,
    app: Application | None,
    kind: str,
) -> Person:
    person = None
    if email:
        person = await session.scalar(select(Person).where(Person.email == email.lower()))
    if person is None and company is not None:
        person = await session.scalar(select(Person).where(Person.full_name == name, Person.company_id == company.id))
    if person is None:
        person = Person(
            full_name=name,
            role=role,
            email=email.lower() if email else None,
            company_id=company.id if company else None,
            kind=kind,
        )
        session.add(person)
        await session.flush()
    person.last_contact_at = datetime.now(UTC)
    if app is not None:
        exists = await session.scalar(
            select(PersonApplication.id).where(
                PersonApplication.person_id == person.id,
                PersonApplication.application_id == app.id,
                PersonApplication.relation == kind,
            )
        )
        if not exists:
            session.add(PersonApplication(person_id=person.id, application_id=app.id, relation=kind))
    return person


async def inbound_opportunity(session: AsyncSession, row: EmailMessage, result: EmailClassification) -> None:
    """Recrutador procurou sem candidatura → oportunidade nova (RF-ACO-10)."""
    from tracker.modules.jobs import service as job_service
    from tracker.modules.jobs.normalize import RawJob

    x = result.extracted
    company_name = x.company_name or (row.from_name or sender_domain(row.from_addr).split(".")[0]).strip()
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="email",
            external_id=row.gmail_id,
            title=x.job_title or "Oportunidade (contato de recrutador)",
            company_name=company_name,
            description_text=row.body_text or row.snippet,
            company_domain=sender_domain(row.from_addr)
            if not any(
                sender_domain(row.from_addr).endswith(d)
                for d in ATS_DOMAINS + LINKEDIN_SENDERS + ["gmail.com", "outlook.com", "hotmail.com"]
            )
            else None,
        ),
    )
    app = await app_service.create_for_job(session, job, channel="inbound", status="discovered")
    app.next_action = "responder o recrutador"
    row.application_id = app.id
    row.link_status = "auto"
    company = await session.get(Company, job.company_id)
    for p in x.people:
        await upsert_person(session, p.name, p.role, p.email, company, app, "recruiter")
    await app_service.add_event(
        session, app, "email_in", {"email_id": str(row.id), "label": result.label, "summary": result.summary}
    )
    dispatch_after_commit(session, "ai.process_job", job_id=str(job.id))
    await notify(
        session,
        "top_job",
        {
            "company": company_name,
            "title": job.title,
            "message": f"Recrutador entrou em contato: {result.summary}",
            "application_id": str(app.id),
        },
    )


async def relink(session: AsyncSession, row: EmailMessage, app: Application, actor: str = "user") -> None:
    """Resolução manual de ambiguidade: associa e aplica o efeito."""
    job = await session.get(Job, app.job_id)
    assert job is not None
    company = await session.get(Company, job.company_id)
    assert company is not None
    row.application_id = app.id
    row.link_status = "user"
    if row.classification:
        result = EmailClassification(
            label=row.classification,  # type: ignore[arg-type]
            confidence=1.0,
            summary=row.extracted.get("summary", row.subject),
            extracted=Extracted.model_validate({k: v for k, v in row.extracted.items() if k in Extracted.model_fields}),
        )
        await apply_outcome(session, row, result, app, job, company)


def text_has_followup_reply(body: str) -> bool:
    return bool(re.search(r"(?i)(obrigad|thank|agrade)", body))


__all__ = ["Any"]
