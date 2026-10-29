"""Application settings loaded from environment variables and an optional .env file.

Secrets are never given default values here. Hosted-provider API keys are read
from the environment variable whose *name* is configured, so the key itself is
never written to configuration files or the database.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
SOFTWARE_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"),),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", alias="AEGIS_ENV")
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="AEGIS_DATA_DIR")
    database_url: str = Field(default="", alias="AEGIS_DATABASE_URL")
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173", alias="AEGIS_CORS_ORIGINS")
    session_ttl_minutes: int = Field(default=720, alias="AEGIS_SESSION_TTL_MINUTES", ge=5, le=10080)
    approval_ttl_seconds: int = Field(default=600, alias="AEGIS_APPROVAL_TTL_SECONDS", ge=10, le=86400)
    max_concurrent_runs: int = Field(default=2, alias="AEGIS_MAX_CONCURRENT_RUNS", ge=1, le=16)
    default_tool_timeout_seconds: float = Field(default=3.0, alias="AEGIS_TOOL_TIMEOUT_SECONDS", gt=0, le=120)
    agent_run_timeout_seconds: float = Field(default=120.0, alias="AEGIS_AGENT_RUN_TIMEOUT_SECONDS", gt=0, le=3600)
    max_request_bytes: int = Field(default=1_048_576, alias="AEGIS_MAX_REQUEST_BYTES", ge=1024)
    log_level: str = Field(default="INFO", alias="AEGIS_LOG_LEVEL")
    # Simulated upstream delay used by the inventory fixture's "timeout" fault (seconds).
    simulated_timeout_delay_seconds: float = Field(default=5.0, alias="AEGIS_SIMULATED_TIMEOUT_DELAY", gt=0, le=60)

    # Local model integration (optional)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_timeout_seconds: float = Field(default=60.0, alias="OLLAMA_TIMEOUT_SECONDS", gt=0, le=600)

    # Hosted OpenAI-compatible provider (optional). The key is read from the env var named here.
    hosted_base_url: str = Field(default="", alias="AEGIS_HOSTED_BASE_URL")
    hosted_api_key_env: str = Field(default="AEGIS_HOSTED_API_KEY", alias="AEGIS_HOSTED_API_KEY_ENV")
    hosted_timeout_seconds: float = Field(default=60.0, alias="AEGIS_HOSTED_TIMEOUT_SECONDS", gt=0, le=600)

    # E-commerce agent platform (optional remote agent). The token authenticates AEGIS to the
    # e-commerce backend's internal API; it lives only in .env (see `make link-ecommerce`).
    ecommerce_base_url: str = Field(default="http://127.0.0.1:8100", alias="AEGIS_ECOMMERCE_BASE_URL")
    ecommerce_token: str = Field(default="", alias="AEGIS_ECOMMERCE_TOKEN", repr=False)
    ecommerce_ui_url: str = Field(default="http://127.0.0.1:5174", alias="AEGIS_ECOMMERCE_UI_URL")
    remote_agent_timeout_seconds: float = Field(default=240.0, alias="AEGIS_REMOTE_AGENT_TIMEOUT_SECONDS", gt=0, le=1800)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v):  # type: ignore[no-untyped-def]
        p = Path(v)
        if not p.is_absolute():
            p = (REPO_ROOT / p).resolve()
        return p

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'aegis.db').as_posix()}"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sandbox_dir(self) -> Path:
        return self.data_dir / "sandboxes"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
