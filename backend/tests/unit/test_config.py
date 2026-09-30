from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings, get_settings

pytestmark = pytest.mark.unit

_STRONG_SECRET = "x" * 48


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove variables a developer or CI may have exported, so defaults are tested."""
    for name in (
        "APP_ENV",
        "DEBUG",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "CORS_ALLOWED_ORIGINS",
        "DATABASE_URL",
        "REDIS_URL",
        "OPENAI_API_KEY",
        "KAFKA_ENABLED",
        "JWT_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_safe_for_local_development() -> None:
    s = _settings()
    assert s.app_env is Environment.LOCAL
    assert s.debug is False
    assert s.api_v1_prefix == "/api/v1"
    assert s.cors_allowed_origins == []
    assert s.kafka_enabled is False
    assert s.openai_configured is False
    assert s.database_url.get_secret_value().startswith("postgresql+asyncpg://")


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("JWT_SECRET_KEY", _STRONG_SECRET)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    s = _settings()
    assert s.app_env is Environment.STAGING
    assert s.log_level == "WARNING"
    assert s.kafka_enabled is True


def test_cors_origins_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000, https://app.example.com ,")
    assert _settings().cors_allowed_origins == [
        "http://localhost:3000",
        "https://app.example.com",
    ]


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        _settings(cors_allowed_origins=["*"])


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError, match="debug must be false"):
        _settings(app_env="production", debug=True, jwt_secret_key=_STRONG_SECRET)


def test_production_requires_json_logs() -> None:
    with pytest.raises(ValidationError, match="log_format must be 'json'"):
        _settings(app_env="production", log_format="console", jwt_secret_key=_STRONG_SECRET)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployed_environments_reject_default_jwt_secret(env: str) -> None:
    with pytest.raises(ValidationError, match="jwt_secret_key"):
        _settings(app_env=env)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployed_environments_reject_short_jwt_secret(env: str) -> None:
    with pytest.raises(ValidationError, match="jwt_secret_key"):
        _settings(app_env=env, jwt_secret_key="too-short")


def test_local_environment_allows_dev_jwt_secret() -> None:
    assert _settings().jwt_secret_key.get_secret_value()


def test_production_accepts_safe_configuration() -> None:
    s = _settings(app_env="production", jwt_secret_key=_STRONG_SECRET)
    assert s.is_production


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(log_level="VERBOSE")


def test_embedding_dimensions_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _settings(openai_embedding_dimensions=0)


def test_secrets_are_masked_in_repr() -> None:
    s = _settings(
        openai_api_key="sk-test-not-a-real-key",
        database_url="postgresql+asyncpg://user:hunter2@db:5432/x",
    )
    text = repr(s)
    assert "sk-test-not-a-real-key" not in text
    assert "hunter2" not in text


@pytest.mark.parametrize(
    ("key", "expected"),
    [(None, False), ("", False), ("sk-test-not-a-real-key", True)],
)
def test_openai_configured_reflects_key_presence(key: str | None, expected: bool) -> None:
    assert _settings(openai_api_key=key).openai_configured is expected


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_database_url_must_use_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        _settings(database_url="postgresql://user:pw@localhost/db")


def test_pool_settings_are_bounded() -> None:
    with pytest.raises(ValidationError):
        _settings(db_pool_size=0)
    with pytest.raises(ValidationError):
        _settings(readiness_timeout_seconds=0)
