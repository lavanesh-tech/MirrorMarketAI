"""Per-request context shared across the call stack.

`contextvars` give every asyncio task its own value, so concurrent requests
never see each other's request ID, and code deep inside services or
repositories can log with the right ID without threading it through every
function signature.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

REQUEST_ID_HEADER = "X-Request-ID"

# Accept a caller-supplied ID only if it is short and made of safe characters.
# This stops log injection (newlines, control chars) and unbounded header values
# from reaching our logs. Anything else is replaced with a fresh UUID.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,128}$")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> Token[str | None]:
    """Set the ID for the current context; keep the token to restore it later."""
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def new_request_id() -> str:
    return uuid.uuid4().hex


def resolve_request_id(incoming: str | None) -> str:
    """Reuse a well-formed incoming ID (for cross-service correlation) or mint one."""
    if incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return new_request_id()
