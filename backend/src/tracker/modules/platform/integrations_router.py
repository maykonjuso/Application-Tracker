"""Conexão de integrações: Google (Gmail + Calendar)."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from tracker.api.deps import SessionDep
from tracker.core import audit
from tracker.core.config import get_settings
from tracker.core.security import CurrentUser, read_state, sign_state
from tracker.core.tasks import dispatch
from tracker.integrations.google import oauth
from tracker.modules.platform.models import IntegrationState

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
async def integrations(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    s = get_settings()
    cred = await oauth.google_credential(session)
    states = {st.name: st for st in (await session.scalars(select(IntegrationState))).all()}
    gmail = states.get("gmail")
    return {
        "google": {
            "configured": bool(s.google_client_id and s.google_client_secret),
            "connected": bool(cred and cred.status == "active"),
            "status": cred.status if cred else None,
            "account": cred.account_email if cred else s.gmail_account,
            "scopes": cred.scopes if cred else [],
            "push": bool(s.google_pubsub_topic),
            "push_endpoint": f"{s.api_base_url}/api/v1/webhooks/gmail/<GMAIL_PUSH_SECRET>",
            "last_sync": gmail.last_success_at.isoformat() if gmail and gmail.last_success_at else None,
            "last_error": gmail.last_error if gmail else None,
        },
        "anthropic": {
            "configured": bool(s.anthropic_api_key) or s.ai_fake,
            "fake": s.ai_fake,
            "model_default": s.ai_model_default,
            "budget_usd": s.ai_monthly_budget_usd,
        },
        "telegram": {"bot_configured": bool(s.telegram_bot_token)},
        "agent": {"token_set": bool(s.agent_device_token)},
    }


@router.get("/google/connect")
async def google_connect(_user: CurrentUser) -> RedirectResponse:
    state = sign_state({"purpose": "integrations", "nonce": secrets.token_urlsafe(16)})
    try:
        return RedirectResponse(oauth.build_auth_url("integrations", state))
    except oauth.OAuthError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/google/callback")
async def google_callback(code: str, state: str, session: SessionDep, _user: CurrentUser) -> RedirectResponse:
    data = read_state(state)
    if not data or data.get("purpose") != "integrations":
        raise HTTPException(400, "state inválido")
    tokens = await oauth.exchange_code(code, "integrations")
    info = await oauth.userinfo(tokens["access_token"])
    expected = get_settings().gmail_account
    if expected and info.get("email") != expected:
        raise HTTPException(403, f"conecte a conta {expected}")
    await oauth.save_credential(session, info["email"], tokens)
    await audit.record(session, actor="user", action="integration.google.connect", after={"scopes": tokens.get("scope")})
    await session.commit()
    dispatch("ingest.gmail_watch_renew")
    dispatch("ingest.gmail_sync")
    return RedirectResponse(get_settings().app_base_url + "/config?tab=integracoes&google=ok")


@router.post("/google/disconnect")
async def google_disconnect(session: SessionDep, _user: CurrentUser) -> dict[str, str]:
    email = await oauth.connected_email(session)
    if email:
        await oauth.revoke(session, email)
    await audit.record(session, actor="user", action="integration.google.disconnect")
    await session.commit()
    return {"status": "disconnected"}
