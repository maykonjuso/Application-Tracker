"""Configuração da aplicação (variáveis de ambiente).

Configurações de negócio (limites, pesos, políticas) ficam no banco (`setting`),
não aqui. Ver docs/04-arquitetura.md §6.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_base_url: str = "http://localhost:3100"
    api_base_url: str = "http://localhost:8000"
    secret_key: str = "dev-insecure-secret-change-me"
    encryption_key: str = Field(
        default="ZGV2LWluc2VjdXJlLWtleS0zMi1ieXRlcy1sb25nISE=",
        description="32 bytes em base64 (AES-256-GCM)",
    )
    # Único e-mail que pode entrar no dashboard (login Google). Em dev/test, sem valor, usa um e-mail local.
    auth_allowed_email: str = ""
    auth_dev_login: bool = False
    user_timezone: str = "America/Sao_Paulo"

    database_url: str = "postgresql+asyncpg://tracker:tracker@localhost:5433/tracker"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: Path = REPO_ROOT / "data" / "storage"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "auto"

    anthropic_api_key: str | None = None
    ai_monthly_budget_usd: float = 30.0
    ai_model_default: str = "claude-opus-5-5"
    ai_fake: bool = False
    # "api" (Anthropic), "claude_code", "ollama" (local) ou "auto".
    ai_backend: Literal["auto", "api", "claude_code", "ollama"] = "auto"
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_s: int = 900
    agent_device_token: str | None = None
    # Tarefas em paralelo no worker (threads; o modelo de embeddings fica carregado uma vez só)
    worker_concurrency: int = 3
    claude_code_cmd: str = "claude"
    claude_code_timeout_s: int = 900

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_pubsub_topic: str | None = None
    google_pubsub_audience: str | None = None
    # Conta do Gmail/Agenda. Vazio: vale a conta que você conectar em Configurações → Integrações.
    gmail_account: str | None = None
    gmail_push_secret: str = "dev-gmail-push-secret"

    telegram_bot_token: str | None = None
    # "webhook" (API pública), "polling" (getUpdates, para rodar só no PC) ou "auto": polling se API_BASE_URL for local
    telegram_mode: str = "auto"
    telegram_webhook_secret: str = "dev-telegram-secret"
    telegram_allowed_chat_id: int | None = None

    latex_cmd: str = "tectonic"

    backup_pg_dump_cmd: str = "pg_dump"
    backup_retention_days: int = 30

    sentry_dsn: str | None = None
    cors_origins: list[str] = ["http://localhost:3100"]
    # Hosts aceitos no cabeçalho Host (contra DNS rebinding). Os de APP_BASE_URL/API_BASE_URL entram sozinhos.
    allowed_hosts: list[str] = []

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.auth_dev_login and self.app_env == "prod":
            raise ValueError("AUTH_DEV_LOGIN não pode ser usado em produção")
        if not self.auth_allowed_email:
            if self.app_env == "prod":
                raise ValueError("defina AUTH_ALLOWED_EMAIL (o e-mail que pode entrar no dashboard)")
            self.auth_allowed_email = "voce@localhost"
        if self.app_env == "prod":
            if self.secret_key.startswith("dev-"):
                raise ValueError("SECRET_KEY de desenvolvimento em produção")
            if self.encryption_key == "ZGV2LWluc2VjdXJlLWtleS0zMi1ieXRlcy1sb25nISE=":
                raise ValueError("ENCRYPTION_KEY de desenvolvimento em produção")
        if len(self.encryption_key_bytes) != 32:
            raise ValueError("ENCRYPTION_KEY precisa ter 32 bytes em base64")
        return self

    @property
    def encryption_key_bytes(self) -> bytes:
        return base64.b64decode(self.encryption_key)

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def trusted_hosts(self) -> list[str]:
        from urllib.parse import urlparse

        hosts = {"localhost", "127.0.0.1", "::1", "[::1]", "api", *self.allowed_hosts}
        hosts |= {h for u in (self.app_base_url, self.api_base_url) if (h := urlparse(u).hostname)}
        if self.app_env == "test":
            hosts.add("test")
        return sorted(hosts)

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    def telegram_polling(self) -> bool:
        if not self.telegram_bot_token or self.app_env == "test":
            return False
        if self.telegram_mode == "auto":
            return any(h in self.api_base_url for h in ("localhost", "127.0.0.1"))
        return self.telegram_mode == "polling"


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("TRACKER_NO_DOTENV"):
        return Settings(_env_file=None)  # type: ignore[call-arg]
    return Settings()
