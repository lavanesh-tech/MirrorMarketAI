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


# Publicly known: only acceptable for local development and tests.
_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"  # noqa: S105
MIN_JWT_SECRET_LENGTH = 32
# Size of the pgvector column (migration 0005). Must match the embedding model.
EMBEDDING_DIMENSIONS = 1536

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

    # --- Authentication -----------------------------------------------------
    # HS256 signing key for access tokens. The default is for local/test only;
    # staging/production refuse to start unless a strong secret is supplied.
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "mirrormarket"
    jwt_audience: str = "mirrormarket-api"
    jwt_access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)

    # --- Source ingestion (URL fetch + uploads) ------------------------------
    ingestion_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    ingestion_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    ingestion_max_redirects: int = Field(default=5, ge=0, le=10)
    ingestion_allowed_ports: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [80, 443]
    )
    ingestion_user_agent: str = (
        "MirrorMarketBot/0.1 (+https://github.com/lavanesh-tech/MirrorMarketAI)"
    )
    ingestion_max_pdf_pages: int = Field(default=200, ge=1, le=2000)

    # --- Redis (cache/coordination; wired up in Phase 19) --------------------
    redis_url: SecretStr = SecretStr("redis://localhost:6380/0")

    # --- OpenAI (placeholders; used from Phase 6 onward) ---------------------
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = Field(default=1536, gt=0, le=4096)
    openai_request_timeout_seconds: float = Field(default=30.0, gt=0)
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Chunking + embeddings ------------------------------------------------
    # "hashing" is a deterministic, offline embedder (lexical feature hashing):
    # the demo and CI work without an API key. Use "openai" for real semantics.
    embedding_provider: Literal["openai", "hashing"] = "hashing"
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)
    embedding_max_retries: int = Field(default=3, ge=0, le=8)
    embedding_job_max_attempts: int = Field(default=3, ge=1, le=10)
    chunk_target_chars: int = Field(default=2000, ge=200, le=20_000)
    chunk_overlap_chars: int = Field(default=200, ge=0, le=5_000)
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0, le=300)

    # --- Requirements ----------------------------------------------------------
    # "rules" is a deterministic offline extractor; "openai" uses structured outputs.
    requirements_extractor: Literal["openai", "rules"] = "rules"
    requirements_max_text_chars: int = Field(default=4000, ge=200, le=20_000)

    # --- Agents --------------------------------------------------------------------
    # "rules" = deterministic offline agents; "openai" = OPENAI_CHAT_MODEL with citations.
    agent_engine: Literal["openai", "rules"] = "rules"
    agent_evidence_per_criterion: int = Field(default=3, ge=1, le=10)
    agent_max_review_chunks: int = Field(default=60, ge=1, le=500)

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

    @field_validator("ingestion_allowed_ports", mode="before")
    @classmethod
    def _split_ports(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(p) for p in value.split(",") if p.strip()]
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
    def _check_embedding_config(self) -> Self:
        if self.openai_embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"openai_embedding_dimensions must be {EMBEDDING_DIMENSIONS} "
                "(the pgvector column size); changing it requires a migration"
            )
        if self.chunk_overlap_chars >= self.chunk_target_chars // 2:
            raise ValueError("chunk_overlap_chars must be less than half of chunk_target_chars")
        if self.embedding_provider == "openai" and not self.openai_configured:
            raise ValueError("embedding_provider=openai requires OPENAI_API_KEY")
        if self.requirements_extractor == "openai" and not self.openai_configured:
            raise ValueError("requirements_extractor=openai requires OPENAI_API_KEY")
        if self.agent_engine == "openai" and not self.openai_configured:
            raise ValueError("agent_engine=openai requires OPENAI_API_KEY")
        return self

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Self:
        if self.app_env in (Environment.STAGING, Environment.PRODUCTION):
            secret = self.jwt_secret_key.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"jwt_secret_key must be a unique secret of at least "
                    f"{MIN_JWT_SECRET_LENGTH} characters outside local/test"
                )
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
