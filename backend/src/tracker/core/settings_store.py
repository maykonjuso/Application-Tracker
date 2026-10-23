"""Configurações de negócio no banco, com padrões em código (docs/04 §6).

Inclui feature flags (RF-PLA-08), limites, políticas de automação (RF-PLA-02) e
limiares. `get` mescla o valor salvo sobre o padrão.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracker.modules.platform.models import Setting

DEFAULTS: dict[str, dict[str, Any]] = {
    "flags": {
        "gmail": True,
        "gmail_labels": True,
        "calendar": True,
        "telegram": True,
        "job_sources": True,
        "company_research": True,
    },
    "automation_policy": {
        "easy_apply": {
            "mode": "review",
            "dry_run": True,
            "follow_company": False,
            "auto": {
                "min_quadrant": "top",
                "min_chance": 0.7,
                "min_answer_confidence": 0.9,
                "allow_tailored_resume": False,
                "max_per_day": 3,
            },
        },
        "email": {"send": "never"},
        "linkedin_messages": {"send": "never"},
    },
    "limits": {
        "easy_apply_per_day": 20,
        "linkedin_page_views_per_day": 150,
        "linkedin_searches_per_day": 10,
        "between_applications_s": [45, 180],
        "long_pause_every": 5,
        "window": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "19:00"},
        "per_company_per_month": 3,
    },
    "resume": {
        "match_threshold": 0.75,
        "tailored_requires_approval": True,
        # CV sob medida: "medium" responde em ~metade do tempo de "high" com o mesmo validador de fatos
        "tailor_effort": "medium",
        # ao abrir o formulário de uma vaga top/aposta, a extensão já começa a gerar o CV sob medida
        "auto_tailor_on_apply": True,
        "default_template": "classic",
    },
    "tracking": {"ghost_after_days": 21, "followup_after_days": 8},
    "answers": {"min_confidence": 0.8, "pending_expire_hours": 72},
    "inbox": {"min_confidence": 0.7, "body_retention_days": 90},
    "calendar": {
        "windows": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"},
        "buffer_min": 30,
        "study_block_min": 60,
        "assessment_reminders_h": [48, 24],
    },
    "notifications": {
        "quiet_hours": {"start": "22:00", "end": "07:00"},
        "daily_digest": "08:00",
        "weekly_report": {"weekday": 0, "time": "08:00"},
    },
    "knockout": {"reapply_cooldown_days": 90},
    "goals": {"applications_per_week": 40, "followups_per_week": 10, "study_hours_per_week": 5},
    "telegram_chat": {"chat_id": None, "link_code": None, "link_expires": None},
    # Extensão do navegador (assistente): token guardado só como hash
    "onboarding": {"completed_at": None},
    "extension": {"token_sha256": None, "auto_analyze_known_ats": True, "ignored_domains": []},
    # Remuneração atual: linha de base das ofertas (editável no dashboard; valores pessoais ficam no banco)
    "current_compensation": {
        "company": None,
        "regime": "CLT",
        "monthly_gross": 0,
        "meal_monthly": 0,
        "health_monthly_estimate": 0,
        "health_employee_cost_monthly": 0,
        "wellhub_monthly_estimate": 0,
        "other_monthly": 0,
        "plr_salaries": 0,
        "pension_employee_rate": 0,
        "pension_match_rate": 0,
        "pension_match_cap": 0,
        "non_monetary": [],
    },
}

IMMUTABLE_PATHS = {("automation_policy", "email", "send"), ("automation_policy", "linkedin_messages", "send")}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingError(ValueError):
    pass


def validate(key: str, value: dict[str, Any]) -> None:
    if key not in DEFAULTS:
        raise SettingError(f"configuração desconhecida: {key}")
    for path in IMMUTABLE_PATHS:
        if path[0] != key:
            continue
        node: Any = value
        for part in path[1:]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None and node != "never":
            raise SettingError("e-mails e mensagens do LinkedIn nunca são enviados automaticamente (ADR-009)")


async def get(session: AsyncSession, key: str) -> dict[str, Any]:
    row = await session.scalar(select(Setting).where(Setting.key == key))
    base = DEFAULTS.get(key, {})
    return _merge(base, row.value) if row else copy.deepcopy(base)


async def put(session: AsyncSession, key: str, value: dict[str, Any]) -> dict[str, Any]:
    validate(key, value)
    row = await session.scalar(select(Setting).where(Setting.key == key))
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    await session.flush()
    return _merge(DEFAULTS[key], value)


async def flag(session: AsyncSession, name: str) -> bool:
    flags = await get(session, "flags")
    return bool(flags.get(name, False))
