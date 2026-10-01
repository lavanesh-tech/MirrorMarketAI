"""Single-use, expiring tokens (OAuth `state` + PKCE verifier, email links, ...).

`issue` stores a JSON payload under a random token and returns the token;
`consume` uses GETDEL, so a token can be redeemed exactly once even when two
requests race. Only a SHA-256 of the token is used as the key, so a Redis dump
does not reveal live tokens.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any

from redis.asyncio import Redis

TOKEN_BYTES = 32


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OneTimeTokenStore:
    def __init__(self, redis: Redis, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, purpose: str, token: str) -> str:
        return f"{self._prefix}once:{purpose}:{hashlib.sha256(token.encode()).hexdigest()}"

    async def issue(self, purpose: str, payload: dict[str, Any], ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self._redis.set(self._key(purpose, token), json.dumps(payload), ex=ttl_seconds)
        return token

    async def consume(self, purpose: str, token: str) -> dict[str, Any] | None:
        """The payload, or None if unknown, expired or already used."""
        raw = await self._redis.getdel(self._key(purpose, token))
        if raw is None:
            return None
        payload: dict[str, Any] = json.loads(raw)
        return payload
