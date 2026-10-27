"""Ingestão do Gmail (RF-ACO-02, RNF-11), renovação do watch e retenção (RNF-37)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from tracker.core import settings_store
from tracker.core.config import get_settings
from tracker.integrations.claude.client import AIError, get_ai
from tracker.integrations.google import oauth
from tracker.integrations.google.gmail import Gmail, GmailError
from tracker.modules.inbox import service
from tracker.modules.inbox.models import EmailMessage
from tracker.modules.notifications.service import notify
from tracker.modules.platform.models import IntegrationState
from tracker.workers.celery_app import celery_app
from tracker.workers.runtime import SM, run

log = logging.getLogger(__name__)
STATE = "gmail"

# Marcadores aplicados no próprio Gmail (filtro visual dos e-mails de vagas)
GMAIL_LABELS = {
    "confirmacao": "Vagas/Confirmação",
    "rejeicao": "Vagas/Rejeição",
    "convite_entrevista": "Vagas/Entrevista",
    "pedido_disponibilidade": "Vagas/Entrevista",
    "remarcacao": "Vagas/Entrevista",
    "cancelamento": "Vagas/Entrevista",
    "teste_tecnico": "Vagas/Teste técnico",
    "oferta": "Vagas/Oferta",
    "pedido_info": "Vagas/Pedido de informação",
    "recrutador_prospeccao": "Vagas/Recrutador",
    "notificacao_linkedin": "Vagas/LinkedIn",
}


async def _state(s: Any) -> IntegrationState:
    st = await s.scalar(select(IntegrationState).where(IntegrationState.name == STATE))
    if st is None:
        st = IntegrationState(name=STATE, cursor={})
        s.add(st)
        await s.flush()
    return st


async def gmail_sync(sm: SM, gmail: Gmail | None = None) -> dict[str, Any]:
    async with sm() as s:
        if not await settings_store.flag(s, "gmail"):
            return {"skipped": "flag"}
        if gmail is None:
            try:
                gmail = Gmail(await oauth.get_access_token(s))
            except oauth.OAuthError as exc:
                # token vencido/revogado: sem isso o Gmail para em silêncio e os alertas do LinkedIn somem
                st = await _state(s)
                first = not st.consecutive_errors
                st.last_error_at, st.last_error = datetime.now(UTC), str(exc)[:500]
                st.consecutive_errors = (st.consecutive_errors or 0) + 1
                if first:
                    await notify(s, "integration_error", {"message": f"Gmail parado: {str(exc)[:200]}"})
                await s.commit()
                return {"skipped": str(exc)}
            await s.commit()
        st = await _state(s)
        cursor = dict(st.cursor or {})
        await s.commit()
    ids: list[str] = []
    latest: str | None = None
    try:
        if cursor.get("history_id"):
            try:
                ids, latest = await gmail.history(cursor["history_id"])
            except GmailError as exc:
                if exc.status != 404:
                    raise
                ids = await gmail.list_ids("newer_than:7d")  # lacuna: ressincroniza (RNF-11)
        else:
            ids = await gmail.list_ids("newer_than:14d")
        if latest is None:
            latest = str((await gmail.profile()).get("historyId", ""))
        # e-mails que ficaram para trás numa rodada anterior (IA fora do ar) entram de novo
        ids = list(dict.fromkeys([*cursor.get("retry_ids", []), *ids]))
    except Exception as exc:
        async with sm() as s:
            st = await _state(s)
            st.last_error_at, st.last_error = datetime.now(UTC), str(exc)[:500]
            st.consecutive_errors += 1
            if st.consecutive_errors == 3:
                await notify(s, "integration_error", {"message": f"Gmail falhando: {str(exc)[:200]}"})
            await s.commit()
        raise
    processed = classified = labeled = 0
    label_cache: dict[str, str] | None = None
    async with sm() as s:
        use_labels = await settings_store.flag(s, "gmail_labels")
    async with sm() as s:
        own = ((await oauth.connected_email(s)) or "").lower()
    retry: list[str] = []
    for mid in ids:
        async with sm() as s:
            if await s.scalar(select(EmailMessage.id).where(EmailMessage.gmail_id == mid)):
                continue
        try:
            email = await gmail.get(mid)
        except GmailError as exc:
            if exc.status == 404:
                continue  # apagado no Gmail
            retry.append(mid)
            continue
        if own and email.from_addr == own:
            continue  # enviados pelo próprio usuário
        async with sm() as s:
            try:
                row = await service.process_email(s, get_ai(sm), email)
            except AIError as exc:
                log.warning("classificação adiada (%s): %s", mid, exc)
                await s.rollback()
                retry.append(mid)  # o cursor avança, mas este e-mail é tentado de novo na próxima rodada
                continue
            await s.commit()
            processed += 1
            classified += int(bool(row and row.classification))
        label = GMAIL_LABELS.get(row.classification or "") if row else None
        if use_labels and label:
            try:
                if label_cache is None:
                    label_cache = await gmail.labels()
                await gmail.add_labels(mid, [await gmail.ensure_label(label, label_cache)])
                labeled += 1
            except GmailError as exc:  # sem escopo gmail.modify: segue sem marcar
                log.info("marcador não aplicado: %s", exc)
                use_labels = exc.status != 403
    async with sm() as s:
        st = await _state(s)
        st.cursor = {**(st.cursor or {}), "history_id": latest or cursor.get("history_id"), "retry_ids": retry[-500:]}
        st.last_success_at, st.consecutive_errors = datetime.now(UTC), 0
        await s.commit()
    return {"seen": len(ids), "processed": processed, "classified": classified, "labeled": labeled, "deferred": len(retry)}


@celery_app.task(name="ingest.gmail_sync")
def gmail_sync_task(history_id: str | None = None) -> dict[str, Any]:
    # uma sincronização por vez (push do Gmail + polling + botão "sincronizar" podem chegar juntos)
    import redis

    lock = redis.Redis.from_url(get_settings().redis_url).lock("tracker:gmail_sync", timeout=900, blocking=False)
    try:
        got = lock.acquire()
    except redis.RedisError:
        got, lock = True, None
    if not got:
        return {"skipped": "sincronização já em andamento"}
    try:
        return run(lambda sm: gmail_sync(sm))
    finally:
        if lock is not None:
            try:
                lock.release()
            except redis.RedisError:
                pass


@celery_app.task(name="ingest.gmail_watch_renew")
def gmail_watch_renew() -> dict[str, Any]:
    async def body(sm: SM) -> dict[str, Any]:
        topic = get_settings().google_pubsub_topic
        if not topic:
            return {"skipped": "sem tópico Pub/Sub (usando polling)"}
        async with sm() as s:
            try:
                token = await oauth.get_access_token(s)
            except oauth.OAuthError as exc:
                return {"skipped": str(exc)}
            res = await Gmail(token).watch(topic)
            st = await _state(s)
            st.cursor = {
                **(st.cursor or {}),
                "watch_expiration": res.get("expiration"),
                "history_id": (st.cursor or {}).get("history_id") or res.get("historyId"),
            }
            await s.commit()
            return res

    return run(body)


@celery_app.task(name="inbox.apply_retention")
def apply_retention() -> int:
    async def body(sm: SM) -> int:
        async with sm() as s:
            cfg = await settings_store.get(s, "inbox")
            cutoff = datetime.now(UTC) - timedelta(days=int(cfg["body_retention_days"]))
            rows = (
                await s.scalars(
                    select(EmailMessage).where(EmailMessage.received_at < cutoff, EmailMessage.body_text.is_not(None))
                )
            ).all()
            for r in rows:
                r.body_text = None
            await s.commit()
            return len(rows)

    return run(body)


async def _recruiter_note(sm: SM, task_run_id: str | None, application_id: str, person_id: str | None = None) -> dict[str, Any]:
    from tracker.modules.inbox.followups import recruiter_note

    return {"draft_id": await recruiter_note(sm, application_id, person_id)}


async def _thank_you(sm: SM, task_run_id: str | None, interview_id: str) -> dict[str, Any]:
    from tracker.modules.inbox.followups import thank_you_draft

    return {"draft_id": await thank_you_draft(sm, interview_id)}


from tracker.workers.runtime import tracked  # noqa: E402

celery_app.task(name="ai.recruiter_note")(tracked("ai.recruiter_note")(_recruiter_note))
celery_app.task(name="ai.thank_you")(tracked("ai.thank_you")(_thank_you))
