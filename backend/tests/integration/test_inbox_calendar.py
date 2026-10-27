from __future__ import annotations

import base64
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests import factories
from tracker.integrations import telegram
from tracker.integrations.claude.client import FakeAIClient
from tracker.integrations.google import gmail as gmail_mod
from tracker.integrations.google.gmail import Gmail, ParsedEmail, parse_message
from tracker.modules.applications.models import Application
from tracker.modules.calendar.models import CalendarEvent
from tracker.modules.calendar.service import free_slots
from tracker.modules.inbox import service as inbox
from tracker.modules.inbox.followups import suggest_followups
from tracker.modules.inbox.models import Draft, EmailMessage
from tracker.modules.inbox.tasks import gmail_sync
from tracker.modules.interviews.models import Interview
from tracker.modules.notifications.service import in_quiet_hours
from tracker.modules.notifications.telegram_bot import handle_update
from tracker.modules.platform.models import Notification

TZ = ZoneInfo("America/Sao_Paulo")
SRC = Path(__file__).resolve().parents[2] / "src"


def test_code_never_sends_email() -> None:
    """ADR-009: nenhuma chamada de envio do Gmail no código."""
    offenders = []
    for f in SRC.rglob("*.py"):
        text = f.read_text()
        if re.search(r"messages/send|drafts/send|\.send_email\(|messages\.send\(", text):
            offenders.append(str(f))
    assert offenders == []


def test_free_slots_respects_busy_window_and_buffer() -> None:
    monday = date(2026, 10, 19)
    busy = [(datetime(2026, 10, 19, 9, 0, tzinfo=TZ), datetime(2026, 10, 19, 11, 0, tzinfo=TZ))]
    window = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}
    slots = free_slots(busy, monday, 7, window, 60, 30, TZ, 3, now=datetime(2026, 10, 18, 12, 0, tzinfo=TZ))
    assert len(slots) == 3
    assert slots[0][0] == datetime(2026, 10, 19, 11, 30, tzinfo=TZ)  # 30 min de folga após o compromisso
    assert all(s.weekday() < 5 and s.hour >= 9 and e.hour <= 18 for s, e in slots)
    assert len({s.date() for s, _ in slots}) >= 2  # espalhados em dias diferentes


def test_quiet_hours() -> None:
    cfg = {"quiet_hours": {"start": "22:00", "end": "07:00"}}
    assert in_quiet_hours(cfg, datetime(2026, 10, 19, 23, 0, tzinfo=TZ))
    assert in_quiet_hours(cfg, datetime(2026, 10, 19, 6, 59, tzinfo=TZ))
    assert not in_quiet_hours(cfg, datetime(2026, 10, 19, 12, 0, tzinfo=TZ))


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_message(mid: str, frm: str, subject: str, body: str, labels=None, auth: bool = True) -> dict:
    domain = frm.split("@")[-1].strip(">").strip()
    auth_header = (
        [
            {
                "name": "Authentication-Results",
                "value": f"mx.google.com; dkim=pass header.i=@{domain}; spf=pass; dmarc=pass header.from={domain}",
            }
        ]
        if auth
        else [{"name": "Authentication-Results", "value": "mx.google.com; dkim=none; spf=softfail; dmarc=fail"}]
    )
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "snippet": body[:50],
        "labelIds": labels or ["INBOX"],
        "internalDate": "1760900000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": frm},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 19 Oct 2026 10:00:00 -0300"},
                {"name": "Message-ID", "value": f"<{mid}@mail>"},
                *auth_header,
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {"mimeType": "text/html", "body": {"data": _b64(f"<p>{body}</p>")}},
            ],
        },
    }


def test_parse_message() -> None:
    p = parse_message(gmail_message("m1", "Ana RH <ana@globex.com>", "Entrevista", "Olá! Vamos conversar?"))
    assert p.from_addr == "ana@globex.com" and p.from_name == "Ana RH"
    assert p.body_text == "Olá! Vamos conversar?" and p.received_at.tzinfo is not None
    assert p.message_id_header == "<m1@mail>"


def email(
    frm: str = "talent@globex.com",
    subject: str = "Next steps",
    body: str = "We'd like to interview you",
    labels=None,
    mid: str = "m1",
    auth: bool = True,
) -> ParsedEmail:
    return parse_message(gmail_message(mid, frm, subject, body, labels, auth))


def test_prefilter() -> None:
    assert inbox.prefilter(email("no-reply@greenhouse.io"), set(), set()) == "ats"
    assert inbox.prefilter(email("x@globex.com"), {"globex.com"}, set()) == "company_domain"
    assert inbox.prefilter(email("promo@shop.com", "Black Friday", "50% off", ["CATEGORY_PROMOTIONS"]), set(), set()) is None
    assert inbox.prefilter(email("joe@corp.com", "Processo seletivo", "agendar entrevista"), set(), set()) == "keywords"
    assert inbox.prefilter(email("mom@family.com", "Almoço", "domingo?"), set(), set()) is None


async def _app_for_globex(session, status: str = "submitted"):
    await factories.seed_master(session)
    comp = await factories.company(session, "Globex", domains=["globex.com"])
    job = await factories.job(session, comp)
    app = Application(job_id=job.id, status=status, submitted_at=datetime.now(UTC) - timedelta(days=3))
    session.add(app)
    await session.commit()
    return app, job, comp


def classification(label: str, app_id=None, **extracted) -> dict:
    return {
        "label": label,
        "confidence": 0.95,
        "application_match": str(app_id) if app_id else None,
        "summary": f"resumo {label}",
        "extracted": extracted,
    }


async def test_interview_invite_creates_interview_event_and_dossier(session, ai: FakeAIClient, dispatched) -> None:
    app, job, comp = await _app_for_globex(session)
    start = (datetime.now(TZ) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    ai.on(
        "email.classify",
        lambda req: classification(
            "convite_entrevista",
            app.id,
            datetimes=[{"start": start.isoformat(), "timezone_explicit": True}],
            meeting_link="https://meet.google.com/abc",
            interview_kind="technical",
            duration_min=45,
            people=[{"name": "Bia Tech", "role": "Tech Lead", "email": "bia@globex.com"}],
        ),
    )
    row = await inbox.process_email(session, ai, email())
    await session.commit()
    assert "<email>" in ai.calls[0].user and "Next steps" in ai.calls[0].user
    assert row.application_id == app.id and row.link_status == "auto"
    await session.refresh(app)
    assert app.status == "interviewing"
    iv = await session.scalar(select(Interview).where(Interview.application_id == app.id))
    assert iv.kind == "technical" and iv.interviewers[0]["name"] == "Bia Tech"
    ev = await session.scalar(select(CalendarEvent).where(CalendarEvent.kind == "interview"))
    assert ev.status == "confirmed" and ev.location_or_link == "https://meet.google.com/abc"
    assert (ev.ends_at - ev.starts_at) == timedelta(minutes=45)
    assert ev.meta.get("local_only") is True  # Google não conectado
    assert any(n == "ai.build_dossier" for n, _ in dispatched)
    assert await session.scalar(select(CalendarEvent).where(CalendarEvent.kind == "study_block"))
    assert await session.scalar(select(Notification).where(Notification.kind == "interview_invite"))


async def test_availability_request_creates_draft_with_fallback(session, ai: FakeAIClient) -> None:
    app, job, comp = await _app_for_globex(session, "under_review")
    ai.on("email.classify", lambda req: classification("pedido_disponibilidade", app.id))
    row = await inbox.process_email(session, ai, email(subject="Disponibilidade"))
    await session.commit()
    d = await session.scalar(select(Draft).where(Draft.kind == "availability"))
    assert d is not None and d.to == "talent@globex.com" and d.subject == "Re: Disponibilidade"
    assert len(d.meta["slots"]) == 3 and d.body.count("- ") >= 3  # texto de fallback (sem IA para draft.reply)
    assert row.classification == "pedido_disponibilidade"


async def test_rejection_and_ambiguous_then_manual_link(client: AsyncClient, session, ai: FakeAIClient) -> None:
    app, job, comp = await _app_for_globex(session)
    ai.on("email.classify", lambda req: {**classification("rejeicao"), "confidence": 0.5})
    row = await inbox.process_email(session, ai, email("noreply@greenhouse.io", "Update", "Unfortunately..."))
    await session.commit()
    assert row.link_status == "ambiguous"
    r = await client.post(f"/api/v1/inbox/{row.id}/link", json={"application_id": str(app.id)})
    assert r.json()["link_status"] == "user"
    await session.refresh(app)
    assert app.status == "rejected"


async def test_recruiter_inbound_creates_opportunity(session, ai: FakeAIClient, dispatched) -> None:
    await factories.seed_master(session)
    ai.on(
        "email.classify",
        lambda req: classification(
            "recrutador_prospeccao",
            company_name="Hooli",
            job_title="Data Engineer",
            people=[{"name": "Gavin", "role": "Recruiter", "email": "gavin@hooli.com"}],
        ),
    )
    row = await inbox.process_email(
        session, ai, email("gavin@hooli.com", "Oportunidade", "Vaga de engenheiro de dados, posição remota")
    )
    await session.commit()
    app = await session.get(Application, row.application_id)
    assert app.channel == "inbound" and app.next_action == "responder o recrutador"
    assert any(n == "ai.process_job" for n, _ in dispatched)


async def test_gmail_sync_history_and_404_resync(session, sessionmaker, ai: FakeAIClient) -> None:
    app, job, comp = await _app_for_globex(session)
    ai.on("email.classify", lambda req: classification("confirmacao", app.id))
    calls = {"history": 0, "modify": []}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p.endswith("/labels") and req.method == "GET":
            return httpx.Response(200, json={"labels": [{"id": "INBOX", "name": "INBOX"}]})
        if p.endswith("/labels") and req.method == "POST":
            return httpx.Response(200, json={"id": "Label_1", "name": "Vagas/Confirmação"})
        if p.endswith("/modify"):
            calls["modify"].append(p)
            return httpx.Response(200, json={})
        if p.endswith("/history"):
            calls["history"] += 1
            return httpx.Response(404, json={"error": "invalid history"})
        if p.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if p.endswith("/messages/m1"):
            return httpx.Response(
                200,
                json=gmail_message("m1", "talent@globex.com", "Recebemos", "candidatura recebida, processo seletivo"),
            )
        if p.endswith("/messages/m2"):
            return httpx.Response(200, json=gmail_message("m2", "friend@x.com", "oi", "bora jogar?"))
        if p.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "999"})
        return httpx.Response(500)

    gmail_mod.set_transport(httpx.MockTransport(handler))
    try:
        from tracker.modules.platform.models import IntegrationState

        session.add(IntegrationState(name="gmail", cursor={"history_id": "100"}))
        await session.commit()
        res = await gmail_sync(sessionmaker, Gmail("tok"))
        assert calls["history"] == 1 and res["seen"] == 2 and res["classified"] == 1
        assert res["labeled"] == 1 and calls["modify"] == ["/gmail/v1/users/me/messages/m1/modify"]
        st = await session.scalar(select(IntegrationState).where(IntegrationState.name == "gmail"))
        await session.refresh(st)
        assert st.cursor["history_id"] == "999"
        emails = (await session.scalars(select(EmailMessage).order_by(EmailMessage.gmail_id))).all()
        assert [e.prefilter for e in emails] == ["company_domain", None]
        assert emails[1].body_text is None  # irrelevante: não guarda corpo
        res2 = await gmail_sync(sessionmaker, Gmail("tok"))  # idempotente
        assert res2["processed"] == 0
    finally:
        gmail_mod.set_transport(None)


async def test_suggest_followups(session, sessionmaker, ai: FakeAIClient) -> None:
    app, job, comp = await _app_for_globex(session)
    app.last_activity_at = datetime.now(UTC) - timedelta(days=10)
    await session.commit()
    ai.on("draft.followup", lambda req: {"subject": "Follow-up: Senior Data Engineer", "body": "Olá, ..."})
    assert await suggest_followups(sessionmaker) == 1
    assert await suggest_followups(sessionmaker) == 0  # não duplica
    await session.refresh(app)
    assert app.next_action.startswith("enviar follow-up")


@pytest.fixture
def tg(monkeypatch):
    from tracker.core.config import get_settings

    monkeypatch.setattr(get_settings(), "telegram_bot_token", "123:abc")
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _j

        sent.append((req.url.path.split("/")[-1], _j.loads(req.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    telegram.set_transport(httpx.MockTransport(handler))
    yield sent
    telegram.set_transport(None)


async def test_telegram_link_commands_and_callbacks(client: AsyncClient, session, tg) -> None:
    code = (await client.post("/api/v1/integrations/telegram/link")).json()["code"]
    assert await handle_update(session, {"message": {"chat": {"id": 42}, "text": "/start errado"}}) is None
    reply = await handle_update(session, {"message": {"chat": {"id": 42}, "text": f"/start {code}"}})
    assert "vinculado" in reply
    assert await handle_update(session, {"message": {"chat": {"id": 7}, "text": "/status"}}) is None  # outro chat
    reply = await handle_update(session, {"message": {"chat": {"id": 42}, "text": "/hoje"}})
    assert reply.startswith("*Hoje*") and "Candidaturas na semana" in reply
    reply = await handle_update(session, {"message": {"chat": {"id": 42}, "text": "/pendentes"}})
    assert "Nada pendente" in reply or "⏳" in reply
    reply = await handle_update(session, {"message": {"chat": {"id": 42}, "text": "/stop"}})
    assert "/hoje" in reply  # comandos do robô não existem mais: mostra a ajuda


async def test_telegram_webhook_requires_secret(anon_client: AsyncClient) -> None:
    r = await anon_client.post("/api/v1/webhooks/telegram/wrong", json={})
    assert r.status_code == 404


async def test_telegram_polling_mode_for_local_runs(monkeypatch) -> None:
    """Sem URL pública: getUpdates (long polling) com o mesmo tratamento do webhook."""
    import asyncio
    import json as _j

    from tracker.core.config import get_settings
    from tracker.modules.notifications.telegram_bot import poll_updates

    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(s, "telegram_allowed_chat_id", 42)
    assert s.telegram_polling() is False  # nunca em testes
    monkeypatch.setattr(s, "app_env", "dev")
    assert s.telegram_polling() is True  # API_BASE_URL local → polling
    monkeypatch.setattr(s, "api_base_url", "https://tracker.exemplo.com")
    assert s.telegram_polling() is False  # URL pública → webhook

    stop = asyncio.Event()
    calls: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        method, body = req.url.path.split("/")[-1], _j.loads(req.content)
        calls.append((method, body))
        if method == "getUpdates":
            if "offset" in body:
                stop.set()
                return httpx.Response(200, json={"ok": True, "result": []})
            update = {"update_id": 900, "message": {"chat": {"id": 42}, "text": "/ajuda"}}
            return httpx.Response(200, json={"ok": True, "result": [update]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    telegram.set_transport(httpx.MockTransport(handler))
    try:
        await asyncio.wait_for(poll_updates(stop), timeout=10)
    finally:
        telegram.set_transport(None)
    methods = [m for m, _ in calls]
    assert methods[0] == "deleteWebhook" and methods.count("getUpdates") == 2
    assert calls[-1][1]["offset"] == 901  # confirma o update processado
    reply = next(b for m, b in calls if m == "sendMessage")
    assert reply["chat_id"] == 42 and "/hoje" in reply["text"]


def test_sender_authentication_header() -> None:
    from tracker.integrations.google.gmail import sender_authenticated

    assert sender_authenticated("mx.google.com; dkim=pass header.i=@globex.com; dmarc=pass", "ana@globex.com")
    # alinhamento relaxado (DMARC): assinatura de subdomínio do remetente vale
    assert sender_authenticated("mx.google.com; dkim=pass header.d=mail.globex.com", "ana@globex.com")
    assert sender_authenticated("mx.google.com; dkim=pass header.i=@evil.com; spf=pass", "ana@globex.com") is False
    assert sender_authenticated("mx.google.com; dkim=pass header.d=globex.com.evil.com", "ana@globex.com") is False
    assert sender_authenticated("", "ana@globex.com") is False


async def test_forged_email_never_acts_automatically(session, ai: FakeAIClient) -> None:
    """E-mail sem autenticação dizendo "rejeitado": não muda status nem mexe na agenda; fica para revisão."""
    app, _job, _comp = await _app_for_globex(session)
    ai.on("email.classify", lambda req: {"label": "rejeicao", "confidence": 0.99, "summary": "rejeitado",
                                         "application_match": str(app.id), "extracted": {}})  # fmt: skip
    row = await inbox.process_email(session, ai, email(body="Unfortunately we will not move forward", auth=False, mid="fx"))
    await session.commit()
    await session.refresh(app)
    assert app.status == "submitted" and row is not None and row.link_status == "ambiguous"
    assert row.extracted["sender_verified"] is False


async def test_unknown_sender_ai_picks_recent_application_as_suggestion_only(session, ai: FakeAIClient) -> None:
    """Sem pista de domínio/nome: a IA vê as candidaturas recentes e propõe, mas não associa sozinha."""
    app, job, comp = await _app_for_globex(session)
    ai.on("email.classify", lambda req: classification("rejeicao", app.id))
    row = await inbox.process_email(
        session,
        ai,
        email(
            "talentos@recrutadora-x.com.br",
            "Processo seletivo",
            "Agradecemos sua candidatura. Infelizmente não seguiremos no processo seletivo.",
        ),
    )
    await session.commit()
    assert f"id={app.id}" in ai.calls[0].user  # a candidatura recente foi mostrada à IA
    assert row.application_id is None and row.link_status == "ambiguous"
    assert row.extracted["suggestion"]["kind"] == "link"
    assert row.extracted["suggestion"]["application_id"] == str(app.id)
    await session.refresh(app)
    assert app.status == "submitted"  # nada mudou sem a sua confirmação


async def test_create_application_from_email_outside_tracker(client: AsyncClient, session, ai: FakeAIClient) -> None:
    await factories.seed_master(session)
    ai.on("email.classify", lambda req: {**classification("rejeicao", company_name="iFood", job_title="Estágio iFuture")})
    row = await inbox.process_email(
        session,
        ai,
        email(
            "no-reply@ifood.com.br",
            "Feedback da sua participação",
            "Obrigado pela sua candidatura. Infelizmente você não seguirá no processo seletivo.",
        ),
    )
    await session.commit()
    assert row.link_status == "ambiguous"
    assert row.extracted["suggestion"] == {
        "kind": "create",
        "company": "iFood",
        "job_title": "Estágio iFuture",
        "confidence": 0.95,
    }
    r = await client.post(f"/api/v1/inbox/{row.id}/create-application")
    assert r.status_code == 200, r.text
    assert r.json()["link_status"] == "user" and r.json()["application_id"]
    app = await session.get(Application, __import__("uuid").UUID(r.json()["application_id"]))
    await session.refresh(app)
    assert app.status == "rejected" and app.submitted_at is not None and app.applied_snapshot_id is not None
    again = await client.post(f"/api/v1/inbox/{row.id}/create-application")
    assert again.status_code == 409
