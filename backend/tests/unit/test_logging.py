from __future__ import annotations

import json
import logging
import sys

import pytest

from app.core.logging import ConsoleFormatter, JsonFormatter, RequestIdFilter, configure_logging
from app.core.request_context import reset_request_id, set_request_id

pytestmark = pytest.mark.unit


def _record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    RequestIdFilter().filter(record)
    return record


def test_json_formatter_emits_single_line_json_with_core_fields() -> None:
    line = JsonFormatter().format(_record("product added"))
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "product added"
    assert payload["timestamp"].endswith("+00:00")
    assert "request_id" not in payload  # no active request


def test_json_formatter_includes_request_id_and_extras() -> None:
    token = set_request_id("req-abcdef12")
    try:
        record = _record(workspace_id="ws_1", count=3)
    finally:
        reset_request_id(token)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "req-abcdef12"
    assert payload["workspace_id"] == "ws_1"
    assert payload["count"] == 3


def test_json_formatter_survives_unserialisable_extras() -> None:
    payload = json.loads(JsonFormatter().format(_record(obj=object())))
    assert payload["obj"].startswith("<object object")


def test_json_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exception"]


def test_console_formatter_uses_dash_without_request() -> None:
    record = _record("hi")
    assert "[-]" in ConsoleFormatter().format(record)


def test_console_formatter_appends_extras() -> None:
    line = ConsoleFormatter().format(_record("request completed", http_status=200))
    assert line.endswith("request completed http_status=200")


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    original_handlers, original_level = root.handlers[:], root.level
    try:
        configure_logging("INFO", "json")
        configure_logging("WARNING", "console")
        assert len(root.handlers) == 1
        assert root.level == logging.WARNING
        assert isinstance(root.handlers[0].formatter, ConsoleFormatter)
        assert logging.getLogger("uvicorn.access").disabled is True
    finally:
        root.handlers[:] = original_handlers
        root.setLevel(original_level)
