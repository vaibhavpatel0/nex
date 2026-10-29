"""Settings for the e-commerce agent platform. Secrets come only from the environment or .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = BACKEND_DIR.parent
VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(PLATFORM_ROOT / ".env"),), env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Field(default=PLATFORM_ROOT / "data", alias="SHOP_DATA_DIR")
    database_url: str = Field(default="", alias="SHOP_DATABASE_URL")
    cors_origins: str = Field(default="http://localhost:5174,http://127.0.0.1:5174", alias="SHOP_CORS_ORIGINS")
    session_ttl_minutes: int = Field(default=720, alias="SHOP_SESSION_TTL_MINUTES", ge=5, le=10080)
    demo_password: str = Field(default="", alias="SHOP_DEMO_PASSWORD", repr=False)
    log_level: str = Field(default="INFO", alias="SHOP_LOG_LEVEL")

    # ---------------------------------------------------------------- model provider
    model_provider: str = Field(default="ollama", alias="SHOP_MODEL_PROVIDER")  # ollama | openai_compatible
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3:8b", alias="OLLAMA_MODEL")
    ollama_api_key: str = Field(default="", alias="OLLAMA_API_KEY", repr=False)
    model_timeout_seconds: float = Field(default=90.0, alias="SHOP_MODEL_TIMEOUT_SECONDS", gt=0, le=600)
    openai_base_url: str = Field(default="", alias="SHOP_OPENAI_BASE_URL")
    openai_model: str = Field(default="", alias="SHOP_OPENAI_MODEL")
    openai_api_key: str = Field(default="", alias="SHOP_OPENAI_API_KEY", repr=False)
    max_model_rounds: int = Field(default=6, alias="SHOP_MAX_MODEL_ROUNDS", ge=1, le=12)
    tool_timeout_seconds: float = Field(default=5.0, alias="SHOP_TOOL_TIMEOUT_SECONDS", gt=0, le=60)
    run_timeout_seconds: float = Field(default=240.0, alias="SHOP_RUN_TIMEOUT_SECONDS", gt=0, le=1800)
    # Staff tools are exposed to the model by default so SENTINEL's denial can be observed.
    # Set to false for least privilege (the model will not see them at all).
    expose_staff_tools: bool = Field(default=True, alias="SHOP_EXPOSE_STAFF_TOOLS_TO_AGENT")

    # ---------------------------------------------------------------- AEGIS / SENTINEL
    aegis_base_url: str = Field(default="http://127.0.0.1:8000", alias="AEGIS_BASE_URL")
    aegis_api_token: str = Field(default="", alias="AEGIS_API_TOKEN", repr=False)
    aegis_agent_key: str = Field(default="shop-assistant", alias="AEGIS_AGENT_KEY")
    aegis_ui_url: str = Field(default="http://127.0.0.1:5173", alias="AEGIS_UI_URL")
    sentinel_timeout_seconds: float = Field(default=8.0, alias="SENTINEL_TIMEOUT_SECONDS", gt=0, le=60)
    # Token AEGIS presents when it drives the agent for security tests.
    internal_token: str = Field(default="", alias="SHOP_INTERNAL_TOKEN", repr=False)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _abs(cls, v):  # type: ignore[no-untyped-def]
        p = Path(v)
        return p if p.is_absolute() else (PLATFORM_ROOT / p).resolve()

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'shop.db').as_posix()}"

    @property
    def sandbox_dir(self) -> Path:
        return self.data_dir / "sandboxes"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def model_name(self) -> str:
        return self.ollama_model if self.model_provider == "ollama" else self.openai_model

    @property
    def ollama_is_cloud(self) -> bool:
        return self.ollama_base_url.rstrip("/").startswith("https://ollama.com")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
