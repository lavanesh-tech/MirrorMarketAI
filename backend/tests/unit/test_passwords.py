from __future__ import annotations

import pytest

from app.security.passwords import hash_password, needs_rehash, verify_password

pytestmark = pytest.mark.unit


def test_hash_is_argon2id_and_salted() -> None:
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert first.startswith("$argon2id$")
    assert first != second  # random salt per hash
    assert "correct horse battery" not in first


def test_verify_accepts_correct_and_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong horse battery", hashed)


def test_verify_without_hash_is_false_even_for_dummy_password() -> None:
    assert not verify_password("mirrormarket-timing-equaliser", None)
    assert not verify_password("anything", None)


def test_verify_rejects_malformed_hash() -> None:
    assert not verify_password("anything", "not-a-real-hash")


def test_fresh_hash_does_not_need_rehash() -> None:
    assert not needs_rehash(hash_password("correct horse battery"))
