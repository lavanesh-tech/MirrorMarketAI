"""Structured logging built on the standard library.

Why stdlib instead of a logging framework: every library we use (uvicorn,
SQLAlchemy, httpx, the OpenAI SDK) already logs through `logging`. Formatting
at the handler level gives *all* of that output one consistent JSON shape with
the current request ID attached, without extra dependencies.

Usage:
    logger = logging.getLogger(__name__)
    logger.info("product added", extra={"workspace_id": ws_id, "product_id": p_id})

Every key passed in `extra` becomes a top-level JSON field.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.request_context import get_request_id
from app.telemetry.tracing import current_trace_ids

# Attributes every LogRecord has. Anything else on a record came from `extra=`.
# `color_message` is an ANSI-coloured duplicate uvicorn attaches to its records.
_RESERVED_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "color_message"}
)


class RequestIdFilter(logging.Filter):
    """Attach the current request ID and trace IDs (if any) to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        # Inside a traced request, tie the log line to its trace (and back).
        ids = current_trace_ids()
        if ids is not None:
            record.trace_id, record.span_id = ids
        return True


class JsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in vars(record).items():
            if key not in _RESERVED_RECORD_ATTRS and key != "request_id":
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str: never let an unserialisable extra crash logging.
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly single-line format for local development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        line = super().format(record)
        extras = " ".join(
            f"{key}={value}"
            for key, value in vars(record).items()
            if key not in _RESERVED_RECORD_ATTRS and key != "request_id"
        )
        return f"{line} {extras}" if extras else line


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Route all logging (ours, uvicorn's, libraries') through one stdout handler.

    Idempotent: calling it again replaces the handler instead of duplicating it,
    which matters for tests and uvicorn's reloader.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers; make its loggers propagate to ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    # Our middleware emits one structured access log per request, so uvicorn's
    # plain-text access log would be a duplicate.
    logging.getLogger("uvicorn.access").disabled = True
