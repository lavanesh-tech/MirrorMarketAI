"""OpenTelemetry tracing: one trace per request, with the SQL it ran underneath it."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.logging import RequestIdFilter
from app.main import create_app
from app.telemetry import tracing
from tests.conftest import SettingsFactory

pytestmark = [pytest.mark.db, pytest.mark.api]

LOGIN = {"email": "nobody-traced@example.com", "password": "not-a-real-password"}
PARENT_TRACE = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{PARENT_TRACE}-b7ad6b7169203331-01"


class Traced:
    def __init__(self, http: httpx.AsyncClient, exporter: InMemorySpanExporter) -> None:
        self.http = http
        self.exporter = exporter

    def spans(self) -> list[ReadableSpan]:
        return list(self.exporter.get_finished_spans())

    def servers(self) -> list[ReadableSpan]:
        return [s for s in self.spans() if s.kind is trace.SpanKind.SERVER]


@pytest.fixture
async def traced(
    make_settings: SettingsFactory, migrated_database_url: str, request: pytest.FixtureRequest
) -> AsyncIterator[Traced]:
    """A real app (own engine, so SQL is traced) exporting spans into memory."""
    overrides = getattr(request, "param", {})
    exporter = InMemorySpanExporter()
    app = create_app(
        make_settings(database_url=migrated_database_url, otel_enabled=True, **overrides),
        span_exporter=exporter,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as http,
    ):
        yield Traced(http, exporter)


async def test_a_request_becomes_a_trace_with_its_sql_underneath(traced: Traced) -> None:
    response = await traced.http.post("/api/v1/auth/login", json=LOGIN)
    assert response.status_code == 401

    [server] = traced.servers()
    assert server.name == "POST /api/v1/auth/login"
    assert server.attributes is not None
    assert server.attributes["http.route"] == "/api/v1/auth/login"
    assert server.resource.attributes["service.name"] == "mirrormarket-api"
    assert server.resource.attributes["deployment.environment"] == "test"

    sql = [s for s in traced.spans() if s.kind is trace.SpanKind.CLIENT]
    assert sql, "no SQL span was recorded"
    assert server.context is not None
    for child in sql:
        assert child.attributes is not None
        assert child.attributes["db.system"] == "postgresql"
        assert child.name == child.attributes["db.operation"]
        # Parameterised text only: the email being looked up is not in the statement.
        assert LOGIN["email"] not in str(child.attributes["db.statement"])
        assert child.context is not None
        assert child.context.trace_id == server.context.trace_id


async def test_credentials_never_reach_a_span(traced: Traced) -> None:
    await traced.http.post("/api/v1/auth/login", json=LOGIN)
    recorded = " ".join(
        f"{s.name} {dict(s.attributes or {})} {[dict(e.attributes or {}) for e in s.events]}"
        for s in traced.spans()
    )
    assert LOGIN["password"] not in recorded
    assert LOGIN["email"] not in recorded


async def test_a_failing_statement_is_marked_as_an_error(traced: Traced) -> None:
    engine = traced.http._transport.app.state.database.engine  # type: ignore[attr-defined]
    with pytest.raises(DBAPIError):
        async with engine.connect() as connection:
            await connection.execute(text("SELECT * FROM table_that_does_not_exist"))
    failed = [s for s in traced.spans() if not s.status.is_ok]
    assert [s.name for s in failed] == ["SELECT"]
    assert failed[0].end_time is not None


async def test_an_incoming_trace_is_continued(traced: Traced) -> None:
    await traced.http.get("/api/v1/auth/me", headers={"traceparent": TRACEPARENT})
    [server] = traced.servers()
    assert server.context is not None
    assert f"{server.context.trace_id:032x}" == PARENT_TRACE
    assert server.parent is not None
    assert f"{server.parent.span_id:016x}" == "b7ad6b7169203331"


async def test_probes_and_scrapes_are_not_traced(traced: Traced) -> None:
    for path in ("/api/v1/health", "/api/v1/ready", "/metrics"):
        assert (await traced.http.get(path)).status_code == 200
    assert traced.servers() == []


@pytest.mark.parametrize("traced", [{"otel_sample_ratio": 0.0}], indirect=True)
async def test_sample_ratio_zero_records_nothing_unless_the_caller_sampled(traced: Traced) -> None:
    await traced.http.get("/api/v1/auth/me")
    assert traced.spans() == []
    # The caller already decided to sample this trace: follow it, so it is never partial.
    await traced.http.get("/api/v1/auth/me", headers={"traceparent": TRACEPARENT})
    assert len(traced.servers()) == 1


async def test_work_inside_a_request_gets_its_own_span(traced: Traced) -> None:
    with tracing.span("agent.risk", **{"mm.agent": "risk"}) as current:
        assert current.get_span_context().is_valid
        ids = tracing.current_trace_ids()
    assert ids is not None
    [recorded] = traced.spans()
    assert recorded.name == "agent.risk"
    assert recorded.attributes is not None
    assert recorded.attributes["mm.agent"] == "risk"
    assert recorded.context is not None
    assert ids == (f"{recorded.context.trace_id:032x}", f"{recorded.context.span_id:016x}")


async def test_log_lines_carry_the_trace_id(traced: Traced) -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)
    with tracing.span("agent.value"):
        RequestIdFilter().filter(record)
    [recorded] = traced.spans()
    assert recorded.context is not None
    assert record.trace_id == f"{recorded.context.trace_id:032x}"  # type: ignore[attr-defined]
    assert record.span_id == f"{recorded.context.span_id:016x}"  # type: ignore[attr-defined]


async def test_tracing_is_off_by_default(client: httpx.AsyncClient) -> None:
    assert client._transport.app.state.tracer_provider is None  # type: ignore[attr-defined]
    with tracing.span("agent.risk") as current:
        assert current is trace.INVALID_SPAN
    assert tracing.current_trace_ids() is None
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)
    RequestIdFilter().filter(record)
    assert not hasattr(record, "trace_id")


async def test_shutdown_removes_the_instrumentation(
    make_settings: SettingsFactory, migrated_database_url: str
) -> None:
    exporter = InMemorySpanExporter()
    app = create_app(
        make_settings(database_url=migrated_database_url, otel_enabled=True),
        span_exporter=exporter,
    )
    async with app.router.lifespan_context(app):
        with tracing.span("agent.risk"):
            pass
    assert len(exporter.get_finished_spans()) == 1
    with tracing.span("agent.risk") as current:
        assert current is trace.INVALID_SPAN
