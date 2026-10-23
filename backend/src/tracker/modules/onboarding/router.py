"""Onboarding do primeiro uso: o que falta configurar, testes de cada integração e conclusão (cria a busca inicial)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from tracker.api.deps import SessionDep
from tracker.core import settings_store
from tracker.core.config import get_settings
from tracker.core.security import CurrentUser
from tracker.integrations import telegram
from tracker.integrations.claude.client import AIError, AIRequest, find_claude_code, get_ai, model_for, sessionmaker_from
from tracker.integrations.google import oauth
from tracker.modules.companies.models import Company
from tracker.modules.jobs.models import SavedSearch
from tracker.modules.notifications.telegram_bot import linked_chat
from tracker.modules.profile import service as profile_service
from tracker.modules.resumes.latex import find_latex_compiler

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

MIN_CLAUDE_CODE = (2, 1, 280)  # Opus 5.5 exige o CLI 2.1.280 ou mais novo
_cli_version: tuple[str | None, tuple[int, ...] | None] | None = None


async def claude_code_version() -> tuple[str | None, tuple[int, ...] | None]:
    global _cli_version
    if _cli_version is None:
        cmd = find_claude_code()
        if cmd is None:
            return None, None
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        except (OSError, TimeoutError):
            return cmd, None
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out.decode())
        _cli_version = (cmd, tuple(int(x) for x in m.groups()) if m else None)
    return _cli_version


async def ai_check() -> dict[str, Any]:
    s = get_settings()
    if s.ai_fake:
        return {"ok": True, "backend": "fake", "detail": "IA simulada (AI_FAKE=true)"}
    if s.ai_backend == "ollama":
        model = model_for("onboarding.check")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{s.ollama_base_url.rstrip('/')}/api/tags")
                response.raise_for_status()
        except httpx.TimeoutException:
            return {"ok": False, "backend": "ollama", "detail": "Ollama não respondeu em 5 segundos"}
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "backend": "ollama", "detail": f"Ollama respondeu HTTP {exc.response.status_code}"}
        except httpx.RequestError:
            return {
                "ok": False,
                "backend": "ollama",
                "detail": f"Não conectou em {s.ollama_base_url}; inicie o container Ollama",
            }
        models = response.json().get("models", [])
        names = {entry.get("name") for entry in models if isinstance(entry, dict)}
        if model not in names:
            return {
                "ok": False,
                "backend": "ollama",
                "detail": f"Modelo {model} não encontrado; baixe-o com `ollama pull {model}`",
            }
        return {"ok": True, "backend": "ollama", "detail": f"Ollama conectado; modelo {model} disponível"}
    if s.ai_backend == "api" or (s.ai_backend == "auto" and s.anthropic_api_key):
        return {"ok": bool(s.anthropic_api_key), "backend": "api", "detail": "chave de API configurada"}
    cmd, version = await claude_code_version()
    if cmd is None:
        return {
            "ok": False,
            "backend": "claude_code",
            "detail": "CLI `claude` não encontrado: instale o Claude Code e faça login",
        }
    if version is None:
        return {"ok": False, "backend": "claude_code", "detail": f"não consegui ler a versão de {cmd}"}
    shown = ".".join(map(str, version))
    if version < MIN_CLAUDE_CODE:
        return {"ok": False, "backend": "claude_code", "detail": f"Claude Code {shown} é antigo: rode `claude update`"}
    return {"ok": True, "backend": "claude_code", "detail": f"Claude Code {shown} (sua assinatura)"}


def latex_check() -> dict[str, Any]:
    compiler = find_latex_compiler()
    if compiler:
        return {"ok": True, "detail": compiler.detail}
    cmd = get_settings().latex_cmd
    return {
        "ok": False,
        "detail": f"`{cmd or 'tectonic'}` não encontrado: instale tectonic no sistema ou no WSL2 padrão para gerar PDFs",
    }


@router.get("/status")
async def status(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    s = get_settings()
    current = await profile_service.current_version(session)
    prefs = await profile_service.get_preferences(session)
    cfg = await settings_store.get(session, "onboarding")
    cred = await oauth.google_credential(session)
    ext = await settings_store.get(session, "extension")
    checks = {
        "resume": {"ok": current is not None, "detail": f"versão {current.version}" if current else "envie seu currículo"},
        "preferences": {"ok": bool(prefs.target_roles), "detail": ", ".join(prefs.target_roles[:4]) or "defina os cargos"},
        "ai": await ai_check(),
        "latex": latex_check(),
        "google": {
            "ok": bool(cred and cred.status == "active"),
            "configured": bool(s.google_client_id and s.google_client_secret),
            "account": cred.account_email if cred else None,
            "redirect_uris": [
                f"{s.api_base_url}/api/v1/auth/google/callback",
                f"{s.api_base_url}/api/v1/integrations/google/callback",
            ],
            "optional": True,
        },
        "telegram": {
            "ok": bool(s.telegram_bot_token and (s.telegram_allowed_chat_id or await linked_chat(session))),
            "configured": bool(s.telegram_bot_token),
            "optional": True,
        },
        "extension": {"ok": bool(ext.get("token_sha256")), "optional": True},
    }
    completed = bool(cfg.get("completed_at")) or (current is not None and bool(prefs.target_roles))
    return {
        "completed": completed,
        "checks": checks,
        "companies": await session.scalar(select(func.count()).select_from(Company)) or 0,
        "saved_searches": await session.scalar(select(func.count()).select_from(SavedSearch)) or 0,
    }


class _Ping(BaseModel):
    ok: bool
    message: str


@router.post("/test/ai")
async def test_ai(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Uma chamada mínima de IA para provar que a configuração funciona."""
    try:
        res = await get_ai(sessionmaker_from(session)).structured(
            AIRequest(task="onboarding.check", system="Responda em português.", effort="low", max_tokens=200,
                      user="Responda ok=true e uma frase curta de boas-vindas a quem está procurando emprego."),
            _Ping,
        )  # fmt: skip
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": res.output.ok, "message": res.output.message, "model": res.model}


@router.post("/test/telegram")
async def test_telegram(session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    s = get_settings()
    if not s.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN vazio no .env (reinicie a API depois de preencher)")
    chat = s.telegram_allowed_chat_id or await linked_chat(session)
    if not chat:
        raise HTTPException(400, "TELEGRAM_ALLOWED_CHAT_ID vazio: mande uma mensagem ao bot e pegue o id (veja o guia)")
    try:
        mid = await telegram.send_message("✅ Application Tracker conectado. As notificações vão chegar aqui.", chat_id=int(chat))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "message_id": mid}


class CompleteIn(BaseModel):
    seed_companies: bool = True
    create_search: bool = True
    run_now: bool = True


@router.post("/complete")
async def complete(body: CompleteIn, session: SessionDep, _user: CurrentUser) -> dict[str, Any]:
    """Fecha o onboarding: dados base, empresas verificadas e uma busca salva com os seus cargos-alvo."""
    from tracker.core.tasks import dispatch
    from tracker.modules.companies.router import seed_companies
    from tracker.modules.resumes.service import ensure_template
    from tracker.modules.scoring.service import current_weights

    if await profile_service.current_version(session) is None:
        raise HTTPException(409, "envie e confirme o seu currículo primeiro")
    prefs = await profile_service.get_preferences(session)
    if not prefs.target_roles:
        raise HTTPException(409, "defina pelo menos um cargo-alvo")
    await ensure_template(session)
    await current_weights(session)
    result: dict[str, Any] = {}
    if body.seed_companies:
        result["companies"] = await seed_companies(session)
    search_id = None
    if body.create_search and not await session.scalar(select(SavedSearch.id).limit(1)):
        ss = SavedSearch(name="Meus cargos-alvo", query=" | ".join(prefs.target_roles), sources=["monitored", "boards"],
                         schedule_cron="0 9,15 * * 1-5", filters={}, is_active=True)  # fmt: skip
        session.add(ss)
        await session.flush()
        search_id = str(ss.id)
        result["saved_search_id"] = search_id
    await settings_store.put(session, "onboarding", {"completed_at": datetime.now(UTC).isoformat()})
    await session.commit()
    if search_id and body.run_now:
        dispatch("ingest.run_saved_search", search_id=search_id)
        result["search_started"] = True
    return result
