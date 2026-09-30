"""Application configuration.

All runtime configuration comes from environment variables (optionally a local
`.env` file), parsed and validated once at startup by pydantic-settings.

Rules:
- Secrets are `SecretStr` so they never appear in `repr()`, logs or tracebacks.
- Nothing here opens a connection. Phase 1 only *declares* PostgreSQL, Redis,
  OpenAI and Kafka settings; later phases consume them.
- Unsafe combinations (e.g. debug in production) fail fast at startup instead of
  silently running in a weaker mode.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # the shared .env also holds docker-compose-only variables
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "MirrorMarket AI"
    app_env: Environment = Environment.LOCAL
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Logging -------------------------------------------------------------
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    # --- HTTP ----------------------------------------------------------------
    # NoDecode: read the raw env string and split it ourselves, so .env can use
    # a plain comma-separated list instead of JSON.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- PostgreSQL (primary system of record; wired up in Phase 2) ----------
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket"
    )

    # --- Redis (cache/coordination; wired up in Phase 19) --------------------
    redis_url: SecretStr = SecretStr("redis://localhost:6380/0")

    # --- OpenAI (placeholders; used from Phase 6 onward) ---------------------
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0, le=4096)
    openai_request_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- Kafka (placeholders; introduced in Phase 21) ------------------------
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "mirrormarket-api"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        # A wildcard is incompatible with credentialed requests (cookies/JWT in
        # later phases) and is never what we want for a multi-tenant app.
        if "*" in value:
            raise ValueError("wildcard CORS origin '*' is not allowed; list origins explicitly")
        return value

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Self:
        if self.app_env is Environment.PRODUCTION:
            if self.debug:
                raise ValueError("debug must be false when app_env=production")
            if self.log_format != "json":
                raise ValueError("log_format must be 'json' when app_env=production")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION

    @property
    def openai_configured(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (parsed once, then cached).

    Tests call `get_settings.cache_clear()` or pass explicit `Settings` objects
    to `create_app()` instead of mutating environment variables globally.
    """
    return Settings()
