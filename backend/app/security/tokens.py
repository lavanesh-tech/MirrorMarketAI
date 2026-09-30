"""JWT access tokens (HS256).

Deterministic code owns authentication: tokens are signed and verified here,
with an explicit algorithm allow-list (never trusting the token's own `alg`
header, which is how "alg=none" attacks work), and issuer/audience/expiry checks.

Refresh tokens and revocation arrive in Phase 22.
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


def create_access_token(
    user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None
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
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return AccessToken(token=token, expires_at=expires_at)


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    """Verify signature, algorithm, issuer, audience, expiry and token type."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "jti", "typ"]},
            leeway=5,
        )
        if payload.get("typ") != TOKEN_TYPE_ACCESS:
            raise AuthenticationError("Invalid token.")
        return TokenClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            token_id=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AuthenticationError("Invalid token.") from exc
