"""Application configuration.

All runtime configuration comes from environment variables (optionally a local
`.env` file), parsed and validated once at startup by pydantic-settings.

Rules:
- Secrets are `SecretStr` so they never appear in `repr()`, logs or tracebacks.
- Nothing here opens a connection. Connections are created in the app lifespan
  (PostgreSQL since Phase 2); Redis, OpenAI and Kafka settings are consumed later.
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

    # --- PostgreSQL (primary system of record) -------------------------------
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket"
    )
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    # Server-side cap on any single statement; protects the pool from runaway queries.
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)
    db_echo: bool = False
    # Readiness probe budget for the database check.
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

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

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg_driver(cls, value: SecretStr) -> SecretStr:
        # The whole data layer is async; a sync driver URL would fail at first query.
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the 'postgresql+asyncpg://' scheme")
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
