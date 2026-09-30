from __future__ import annotations

import re

import pytest

from app.core.request_context import (
    get_request_id,
    new_request_id,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)

pytestmark = pytest.mark.unit

_UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


def test_new_request_id_is_uuid_hex() -> None:
    assert _UUID_HEX.fullmatch(new_request_id())


@pytest.mark.parametrize(
    "incoming",
    ["abc12345", "trace-2026.09.30_ABC", "a" * 128, "0f8fad5b-d9cb-469f-a165-70867728950e"],
)
def test_well_formed_incoming_id_is_reused(incoming: str) -> None:
    assert resolve_request_id(incoming) == incoming


@pytest.mark.parametrize(
    "incoming",
    [
        None,
        "",
        "short",  # below minimum length
        "a" * 129,  # above maximum length
        "abc12345\nlevel=CRITICAL forged",  # log injection attempt
        "abc 12345",  # whitespace
        "<script>alert(1)</script>",
    ],
)
def test_malformed_incoming_id_is_replaced(incoming: str | None) -> None:
    resolved = resolve_request_id(incoming)
    assert resolved != incoming
    assert _UUID_HEX.fullmatch(resolved)


def test_set_and_reset_restore_previous_value() -> None:
    assert get_request_id() is None
    token = set_request_id("request-1234")
    assert get_request_id() == "request-1234"
    reset_request_id(token)
    assert get_request_id() is None
