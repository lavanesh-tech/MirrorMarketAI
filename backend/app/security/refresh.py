"""Opaque refresh tokens: random, high-entropy, stored only as a hash."""

from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 48


def new_refresh_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    # A fast hash is right here (unlike passwords): the input already has 384 bits of
    # entropy, so there is nothing to brute-force, and the lookup must be an index hit.
    return hashlib.sha256(token.encode()).hexdigest()
