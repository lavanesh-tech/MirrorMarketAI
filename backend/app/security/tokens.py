"""JWT access tokens (HS256).

Deterministic code owns authentication: tokens are signed and verified here,
with an explicit algorithm allow-list (never trusting the token's own `alg`
header, which is how "alg=none" attacks work), and issuer/audience/expiry checks.

Key rotation: tokens are signed with `jwt_secret_key`; `jwt_previous_secret_key`
is still accepted for verification, so a secret can be rotated without logging
everyone out (deploy the new key with the old one as "previous", wait one access
token lifetime, then drop the old one).

Revocation: access tokens are short-lived and stateless. "Log out everywhere" and
password changes bump `users.token_version`; `resolve_user` in `app.api.deps`
rejects tokens carrying an older `ver` claim. Refresh tokens live in `app.security.refresh`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings
from app.core.errors import AuthenticationError

TOKEN_TYPE_ACCESS = "access"  # noqa: S105 - claim value, not a secret


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    token_id: str
    expires_at: datetime
    issued_at: datetime
    token_version: int


def create_access_token(
    user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None, version: int = 0
) -> AccessToken:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
        "typ": TOKEN_TYPE_ACCESS,
        "ver": version,
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return AccessToken(token=token, expires_at=expires_at)


def _decode(token: str, secret: str, settings: Settings) -> dict[str, object]:
    payload: dict[str, object] = jwt.decode(
        token,
        secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "exp", "iat", "iss", "aud", "jti", "typ"]},
        leeway=5,
    )
    return payload


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    """Verify signature, algorithm, issuer, audience, expiry and token type."""
    try:
        try:
            payload = _decode(token, settings.jwt_secret_key.get_secret_value(), settings)
        except jwt.InvalidSignatureError:
            previous = settings.jwt_previous_secret_key
            if previous is None:
                raise
            payload = _decode(token, previous.get_secret_value(), settings)
        if payload.get("typ") != TOKEN_TYPE_ACCESS:
            raise AuthenticationError("Invalid token.")
        return TokenClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            token_id=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(int(str(payload["exp"])), tz=UTC),
            issued_at=datetime.fromtimestamp(int(str(payload["iat"])), tz=UTC),
            token_version=int(str(payload.get("ver", 0))),
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AuthenticationError("Invalid token.") from exc
