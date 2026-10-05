"""OpenTelemetry tracing: follow one request through the API, the database and the model.

Off by default. When `OTEL_ENABLED=true`, every HTTP request becomes a trace with child
spans for SQL statements, outbound HTTP calls (the LLM, fetched URLs) and each agent
step, exported over OTLP/HTTP to whatever collector `OTEL_EXPORTER_OTLP_ENDPOINT` names
(Jaeger in the local stack). Log lines carry the trace id, so a slow request found in a
dashboard can be looked up in the logs and the other way round.

No global tracer provider is installed: the provider belongs to the application that
created it, which keeps tests and several apps in one process independent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy import event
from sqlalchemy.engine import Engine, ExceptionContext
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Probes and scrapes would be most of the traces and say nothing.
EXCLUDED_URLS = "/api/v1/health,/api/v1/ready,/metrics"
_TRACER_NAME = "mirrormarket"
_provider: TracerProvider | None = None


def setup_tracing(
    app: FastAPI, settings: Settings, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Instrument `app`. Returns the provider, or None when tracing is off.

    `exporter` is for tests (spans are then exported synchronously, in order).
    """
    global _provider  # noqa: PLW0603 - the process-wide handle used by `span()`
    if not settings.otel_enabled:
        return None
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": __version__,
                "deployment.environment": settings.app_env.value,
            }
        ),
        # Follow the caller's decision when a request arrives with a trace; otherwise
        # sample a fixed share, decided once per trace so traces are never partial.
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=EXCLUDED_URLS,
        # One span per ASGI message says nothing and triples the size of a trace.
        exclude_spans=["receive", "send"],
    )
    _provider = provider
    logger.info(
        "tracing enabled",
        extra={
            "otlp_endpoint": settings.otel_exporter_otlp_endpoint,
            "sample_ratio": settings.otel_sample_ratio,
        },
    )
    return provider


_SPAN_KEY = "_mm_span"
_instrumented: list[tuple[Engine, Any, Any, Any]] = []


def instrument_clients(provider: TracerProvider, engine: AsyncEngine) -> None:
    """Trace SQL on this engine and outbound HTTP calls. Call once the engine exists."""
    _trace_sql(provider, engine.sync_engine)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)


def _trace_sql(provider: TracerProvider, engine: Engine) -> None:
    """One client span per statement, from SQLAlchemy's own cursor events.

    Written here because the OpenTelemetry SQLAlchemy package does not support
    SQLAlchemy 2.1. Only the statement text is recorded: it is parameterised, so the
    values (emails, password hashes, document text) never reach a span.
    """
    tracer = provider.get_tracer(_TRACER_NAME, __version__)
    url = engine.url
    base = {
        "db.system": "postgresql",
        "db.name": url.database or "",
        "server.address": url.host or "",
    }

    def before(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> None:
        operation = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else "SQL"
        started = tracer.start_span(
            operation,
            kind=trace.SpanKind.CLIENT,
            attributes={**base, "db.operation": operation, "db.statement": statement[:2000]},
        )
        if context is not None:
            setattr(context, _SPAN_KEY, started)

    def after(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> None:
        started = getattr(context, _SPAN_KEY, None)
        if started is not None:
            started.end()

    def failed(context: ExceptionContext) -> None:
        started = getattr(context.execution_context, _SPAN_KEY, None)
        if started is not None:
            error = context.original_exception
            started.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
            started.end()

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
    _instrumented.append((engine, before, after, failed))


def shutdown_tracing(provider: TracerProvider) -> None:
    """Flush what is buffered and undo the process-wide instrumentation."""
    global _provider  # noqa: PLW0603
    while _instrumented:
        engine, before, after, failed = _instrumented.pop()
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "handle_error", failed)
    HTTPXClientInstrumentor().uninstrument()
    provider.shutdown()
    if _provider is provider:
        _provider = None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A child span around a unit of work (an agent step). Does nothing when tracing is off."""
    if _provider is None:
        yield trace.INVALID_SPAN
        return
    tracer = _provider.get_tracer(_TRACER_NAME, __version__)
    with tracer.start_as_current_span(name, attributes=attributes) as current:
        yield current


def current_trace_ids() -> tuple[str, str] | None:
    """(trace id, span id) of the active span as hex, for log correlation."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"
