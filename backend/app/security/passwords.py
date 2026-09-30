"""Password hashing with Argon2id (the OWASP-recommended default)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2-cffi defaults: Argon2id, RFC 9106 low-memory profile

# Verified against when the email is unknown, so "no such user" and "wrong
# password" take the same time and can't be told apart (user enumeration).
_DUMMY_HASH = _hasher.hash("mirrormarket-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)  # raises on mismatch
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older parameters and should be upgraded."""
    return _hasher.check_needs_rehash(password_hash)
