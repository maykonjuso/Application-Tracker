"""Rotas do inbox, rascunhos e webhook do Gmail."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from tracker.api.deps import SessionDep, TaskAccepted, get_or_404
from tracker.core import audit
from tracker.core.config import get_settings
from tracker.core.security import CurrentUser
from tracker.core.tasks import dispatch, enqueue
from tracker.integrations.google import oauth
from tracker.modules.applications.models import Application
from tracker.modules.inbox import service
from tracker.modules.inbox.drafts import push_to_gmail
from tracker.modules.inbox.models import Draft, EmailMessage, LinkedinMessage
from tracker.modules.inbox.schemas import DraftOut, DraftUpdate, EmailOut, LinkIn, ReclassifyIn

router = APIRouter(prefix="/inbox", tags=["inbox"])
drafts_router = APIRouter(prefix="/drafts", tags=["inbox"])
webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("", response_model=list[EmailOut])
async def list_inbox(
    session: SessionDep,
    _user: CurrentUser,
    classification: str | None = None,
    link_status: str | None = None,
    only_relevant: bool = True,
    limit: int = 100,
) -> list[EmailMessage]:
    stmt = select(EmailMessage).order_by(EmailMessage.received_at.desc()).limit(min(limit, 300))
    if classification:
        stmt = stmt.where(EmailMessage.classification == classification)
    if link_status:
        stmt = stmt.where(EmailMessage.link_status == link_status)
    if only_relevant:
        stmt = stmt.where(EmailMessage.prefilter.is_not(None))
    return list((await session.scalars(stmt)).all())


@router.get("/linkedin-messages")
async def linkedin_messages(session: SessionDep, _user: CurrentUser) -> list[dict]:
    rows = (await session.scalars(select(LinkedinMessage).order_by(LinkedinMessage.received_at.desc()).limit(100))).all()
    return [
        {
            "id": str(m.id),
            "from": m.from_name,
            "text": m.text,
            "received_at": m.received_at.isoformat(),
            "classification": m.classification,
            "application_id": str(m.application_id) if m.application_id else None,
        }
        for m in rows
    ]


@router.get("/{email_id}")
async def get_email(email_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> dict:
    e = await get_or_404(session, EmailMessage, email_id)
    return {**EmailOut.model_validate(e).model_dump(mode="json"), "body_text": e.body_text}


@router.post("/{email_id}/link", response_model=EmailOut)
async def link(email_id: uuid.UUID, body: LinkIn, session: SessionDep, _user: CurrentUser) -> EmailMessage:
    e = await get_or_404(session, EmailMessage, email_id)
    app = await get_or_404(session, Application, body.application_id)
    await service.relink(session, e, app)
    await session.commit()
    return e


@router.post("/{email_id}/create-application", response_model=EmailOut)
async def create_application(email_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> EmailMessage:
    """Resposta de um processo feito fora do tracker: cria a candidatura e associa o e-mail."""
    e = await get_or_404(session, EmailMessage, email_id)
    if e.application_id:
        raise HTTPException(409, "este e-mail já está associado a uma candidatura")
    await service.create_application_from_email(session, e)
    await session.commit()
    return e


@router.post("/{email_id}/reclassify", response_model=EmailOut)
async def reclassify(email_id: uuid.UUID, body: ReclassifyIn, session: SessionDep, _user: CurrentUser) -> EmailMessage:
    """Correção manual: vira exemplo rotulado para os evals (docs/13 §5)."""
    e = await get_or_404(session, EmailMessage, email_id)
    before = e.classification
    e.classification = body.label
    e.classification_confidence = 1.0
    e.extracted = {**(e.extracted or {}), "corrected_from": before}
    await audit.record(
        session,
        actor="user",
        action="inbox.reclassify",
        entity_type="email_message",
        entity_id=e.id,
        before={"label": before},
        after={"label": body.label},
    )
    if e.application_id and body.label != before:
        app = await session.get(Application, e.application_id)
        if app:
            await service.relink(session, e, app)
    await session.commit()
    return e


@router.post("/sync", status_code=202)
async def sync(_user: CurrentUser) -> dict[str, str]:
    dispatch("ingest.gmail_sync")
    return {"status": "queued"}


# ---------- Rascunhos ----------
@drafts_router.get("", response_model=list[DraftOut])
async def list_drafts(
    session: SessionDep, _user: CurrentUser, status: str | None = "proposed", application_id: uuid.UUID | None = None
) -> list[Draft]:
    stmt = select(Draft).order_by(Draft.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Draft.status == status)
    if application_id:
        stmt = stmt.where(Draft.application_id == application_id)
    return list((await session.scalars(stmt)).all())


@drafts_router.put("/{draft_id}", response_model=DraftOut)
async def update_draft(draft_id: uuid.UUID, body: DraftUpdate, session: SessionDep, _user: CurrentUser) -> Draft:
    d = await get_or_404(session, Draft, draft_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    await session.commit()
    return d


@drafts_router.post("/{draft_id}/push-to-gmail", response_model=DraftOut)
async def push(draft_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> Draft:
    d = await get_or_404(session, Draft, draft_id)
    if d.kind in ("linkedin_note", "linkedin_message"):
        raise HTTPException(422, "rascunho do LinkedIn: copie e envie pelo LinkedIn")
    if not d.to:
        raise HTTPException(422, "informe o destinatário antes de criar o rascunho no Gmail")
    try:
        await push_to_gmail(session, d)
    except oauth.OAuthError as exc:
        raise HTTPException(409, str(exc)) from exc
    await audit.record(session, actor="user", action="draft.push_gmail", entity_type="draft", entity_id=d.id)
    await session.commit()
    return d


@drafts_router.post("/{draft_id}/mark-sent", response_model=DraftOut)
async def mark_sent(draft_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> Draft:
    d = await get_or_404(session, Draft, draft_id)
    d.status = "sent_by_user"
    if d.application_id:
        app = await session.get(Application, d.application_id)
        if app and d.kind == "followup":
            app.next_action, app.next_action_at = None, None
    await session.commit()
    return d


@drafts_router.post("/{draft_id}/discard", response_model=DraftOut)
async def discard(draft_id: uuid.UUID, session: SessionDep, _user: CurrentUser) -> Draft:
    d = await get_or_404(session, Draft, draft_id)
    d.status = "discarded"
    await session.commit()
    return d


class NoteRequest(BaseModel):
    application_id: uuid.UUID
    person_id: uuid.UUID | None = None


@drafts_router.post("/recruiter-note", response_model=TaskAccepted, status_code=202)
async def recruiter_note(body: NoteRequest, session: SessionDep, _user: CurrentUser) -> TaskAccepted:
    tr = await enqueue(
        session,
        "ai.recruiter_note",
        application_id=str(body.application_id),
        person_id=str(body.person_id) if body.person_id else None,
    )
    return TaskAccepted(task_id=tr.id)


# ---------- Webhook do Gmail (Pub/Sub push) ----------
@webhooks_router.post("/gmail/{secret}", status_code=204)
async def gmail_push(secret: str, request: Request) -> Response:
    import hmac

    if not hmac.compare_digest(secret, get_settings().gmail_push_secret):
        raise HTTPException(404)
    await request.body()
    dispatch("ingest.gmail_sync")
    return Response(status_code=204)
