"""Prometheus metrics: what the service is doing, in numbers that can be alerted on.

Rules followed here:
- Labels are small, fixed sets (a route template, never a URL; an agent name, never a
  product id). One label value per user or per id would create a time series per user.
- Counters count events and only go up; rates are computed by Prometheus. Durations are
  histograms so percentiles can be aggregated across instances.
- Everything lives on one registry owned by this module, so tests can read it and no
  metric is registered twice when several apps exist in one process.

One process serves one registry. The API runs one uvicorn worker per container and is
scaled by adding containers; with several workers per container this would need
prometheus_client's multiprocess mode.
"""

from __future__ import annotations

from collections.abc import Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from app import __version__

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

# Buckets in seconds. API calls are milliseconds; agent runs with an LLM take seconds.
_FAST = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_SLOW = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

BUILD_INFO = Gauge(
    "mm_build_info", "Running version (value is always 1).", ["version"], registry=REGISTRY
)
BUILD_INFO.labels(version=__version__).set(1)

HTTP_REQUESTS = Counter(
    "mm_http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "mm_http_request_duration_seconds",
    "Time to handle an HTTP request, by method and route template.",
    ["method", "route"],
    buckets=_FAST,
    registry=REGISTRY,
)
HTTP_IN_PROGRESS = Gauge(
    "mm_http_requests_in_progress", "HTTP requests being handled right now.", registry=REGISTRY
)

AGENT_RUNS = Counter(
    "mm_agent_runs_total",
    "Agent runs by agent, engine (rules or openai) and status.",
    ["agent", "engine", "status"],
    registry=REGISTRY,
)
AGENT_DURATION = Histogram(
    "mm_agent_run_duration_seconds",
    "Time one agent run took.",
    ["agent"],
    buckets=_SLOW,
    registry=REGISTRY,
)
AGENT_DEGRADED = Counter(
    "mm_agent_degraded_total",
    "Agent runs that fell back to the offline engine because the model call failed.",
    ["agent"],
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "mm_llm_tokens_total", "LLM tokens used, by agent.", ["agent"], registry=REGISTRY
)

SEARCH_DURATION = Histogram(
    "mm_search_duration_seconds",
    "Time one evidence search took, by mode.",
    ["mode"],
    buckets=_FAST,
    registry=REGISTRY,
)
SEARCH_DEGRADED = Counter(
    "mm_search_degraded_total",
    "Hybrid searches that fell back to full-text only (embedding unavailable).",
    registry=REGISTRY,
)
ASK_ANSWERS = Counter(
    "mm_ask_answers_total",
    "Questions by outcome: answered with citations, or abstained.",
    ["outcome"],
    registry=REGISTRY,
)
EVIDENCE_WITHHELD = Counter(
    "mm_evidence_sentences_withheld_total",
    "Evidence sentences withheld because they look like instructions to an assistant.",
    registry=REGISTRY,
)

RATE_LIMITED = Counter(
    "mm_rate_limited_total",
    "Requests rejected by a rate limit, by limit.",
    ["limit"],
    registry=REGISTRY,
)
CACHE_REQUESTS = Counter(
    "mm_cache_requests_total",
    "Cache lookups by namespace and result (hit, miss, or bypass when Redis is unavailable).",
    ["namespace", "result"],
    registry=REGISTRY,
)
WS_CONNECTIONS = Gauge(
    "mm_ws_connections", "Open WebSocket connections on this instance.", registry=REGISTRY
)
AUDIT_EVENTS = Counter(
    "mm_audit_events_total",
    "Security-relevant events by action and outcome (logins, refresh reuse, ...).",
    ["action", "outcome"],
    registry=REGISTRY,
)

DB_POOL = Gauge(
    "mm_db_pool_connections",
    "Database connections of this instance's pool: in use (checked_out) or open and free (idle).",
    ["state"],
    registry=REGISTRY,
)

DB_POOL_TIMEOUTS = Counter(
    "mm_db_pool_timeouts_total",
    "Requests answered 503 because no database connection became free in time (overload).",
    registry=REGISTRY,
)
EMBEDDING_JOBS = Counter(
    "mm_embedding_jobs_total", "Embedding jobs by result.", ["result"], registry=REGISTRY
)
OUTBOX_PENDING = Gauge(
    "mm_outbox_pending_events",
    "Events committed but not yet published to Kafka (set by the relay worker).",
    registry=REGISTRY,
)
OUTBOX_OLDEST_SECONDS = Gauge(
    "mm_outbox_oldest_pending_seconds",
    "Age of the oldest unpublished event; how far behind the activity feed is.",
    registry=REGISTRY,
)
EVENTS_RELAYED = Counter(
    "mm_events_relayed_total",
    "Outbox events by publish result.",
    ["result"],
    registry=REGISTRY,
)
EVENTS_CONSUMED = Counter(
    "mm_events_consumed_total",
    "Consumed events by result: handled, duplicate, retry or dead_letter.",
    ["consumer", "result"],
    registry=REGISTRY,
)

UNMATCHED_ROUTE = "unmatched"


def engine_kind(engine: str) -> str:
    """ "openai:gpt-4.1-mini" -> "openai", "rules-v1" -> "rules": model names stay out of labels."""
    return "openai" if engine.startswith("openai") else "rules"


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(seconds)


def observe_agent_run(
    agent: str, engine: str, status: str, seconds: float, tokens: int, *, degraded: bool
) -> None:
    AGENT_RUNS.labels(agent=agent, engine=engine_kind(engine), status=status).inc()
    AGENT_DURATION.labels(agent=agent).observe(seconds)
    if tokens:
        LLM_TOKENS.labels(agent=agent).inc(tokens)
    if degraded:
        AGENT_DEGRADED.labels(agent=agent).inc()


def watch_pool(checked_out: Callable[[], float], idle: Callable[[], float]) -> None:
    """Report the pool's live numbers at scrape time instead of tracking every checkout."""
    DB_POOL.labels(state="checked_out").set_function(checked_out)
    DB_POOL.labels(state="idle").set_function(idle)


def start_metrics_server(port: int) -> None:
    """Serve /metrics from a background thread: for workers, which have no HTTP API."""
    start_http_server(port, registry=REGISTRY)


def render() -> tuple[bytes, str]:
    """(body, content type) in the Prometheus text format."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def metric_names() -> set[str]:
    """Every sample name this service can expose (for checking dashboards and alerts)."""
    names: set[str] = set()
    for collector in REGISTRY.collect():
        names.add(collector.name)
        names.update(sample.name for sample in collector.samples)
        # Histograms and counters expose suffixed series even before the first sample.
        if collector.type == "histogram":
            names.update(f"{collector.name}{suffix}" for suffix in ("_bucket", "_count", "_sum"))
        if collector.type == "counter":
            names.add(f"{collector.name}_total")
    return names
