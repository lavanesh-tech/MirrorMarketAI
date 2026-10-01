from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.security.tokens import create_access_token, decode_access_token
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.unit


@pytest.fixture
def auth_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(jwt_secret_key="k" * 48, jwt_access_token_ttl_minutes=15)


def test_round_trip(auth_settings: Settings) -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, auth_settings)
    claims = decode_access_token(token.token, auth_settings)
    assert claims.user_id == user_id
    assert abs((claims.expires_at - token.expires_at).total_seconds()) < 1


def test_expired_token_is_rejected(auth_settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    token = create_access_token(uuid.uuid4(), auth_settings, now=past)
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(token.token, auth_settings)


def test_wrong_signing_key_is_rejected(
    auth_settings: Settings, make_settings: SettingsFactory
) -> None:
    token = create_access_token(uuid.uuid4(), make_settings(jwt_secret_key="z" * 48))
    with pytest.raises(AuthenticationError, match="Invalid token"):
        decode_access_token(token.token, auth_settings)


def test_wrong_audience_and_issuer_are_rejected(
    auth_settings: Settings, make_settings: SettingsFactory
) -> None:
    for override in ({"jwt_audience": "other-api"}, {"jwt_issuer": "someone-else"}):
        token = create_access_token(
            uuid.uuid4(), make_settings(jwt_secret_key="k" * 48, **override)
        )
        with pytest.raises(AuthenticationError):
            decode_access_token(token.token, auth_settings)


def test_alg_none_token_is_rejected(auth_settings: Settings) -> None:
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": auth_settings.jwt_issuer,
            "aud": auth_settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": "x",
            "typ": "access",
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(forged, auth_settings)


def test_token_missing_required_claims_is_rejected(auth_settings: Settings) -> None:
    token = jwt.encode(
        {"sub": str(uuid.uuid4())}, auth_settings.jwt_secret_key.get_secret_value(), "HS256"
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, auth_settings)


def test_non_access_token_type_is_rejected(auth_settings: Settings) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iss": auth_settings.jwt_issuer,
            "aud": auth_settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": "x",
            "typ": "refresh",
        },
        auth_settings.jwt_secret_key.get_secret_value(),
        "HS256",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, auth_settings)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_is_rejected(auth_settings: Settings, garbage: str) -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token(garbage, auth_settings)


def test_token_carries_the_users_token_version(auth_settings: Settings) -> None:
    user_id = uuid.uuid4()
    assert (
        decode_access_token(
            create_access_token(user_id, auth_settings).token, auth_settings
        ).token_version
        == 0
    )
    bumped = create_access_token(user_id, auth_settings, version=3)
    claims = decode_access_token(bumped.token, auth_settings)
    assert claims.token_version == 3
    assert abs((claims.issued_at - datetime.now(UTC)).total_seconds()) < 5


def test_previous_key_is_accepted_only_while_configured(make_settings: SettingsFactory) -> None:
    old = make_settings(jwt_secret_key="o" * 48)
    token = create_access_token(uuid.uuid4(), old).token

    rotating = make_settings(jwt_secret_key="n" * 48, jwt_previous_secret_key="o" * 48)
    assert decode_access_token(token, rotating).token_version == 0
    # New tokens are signed with the new key only.
    fresh = create_access_token(uuid.uuid4(), rotating).token
    with pytest.raises(AuthenticationError):
        decode_access_token(fresh, old)

    rotated = make_settings(jwt_secret_key="n" * 48)
    with pytest.raises(AuthenticationError):
        decode_access_token(token, rotated)
    stranger = make_settings(jwt_secret_key="n" * 48, jwt_previous_secret_key="x" * 48)
    with pytest.raises(AuthenticationError):
        decode_access_token(token, stranger)
    assert make_settings(jwt_previous_secret_key="").jwt_previous_secret_key is None
