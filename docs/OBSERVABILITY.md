# Observability

Three signals, linked to each other:

| Signal | Tool | Answers |
| --- | --- | --- |
| Metrics | Prometheus (`/metrics`) + Grafana | Is it working? How fast, how often, how many failing? |
| Traces | OpenTelemetry → Jaeger | Where did this one request spend its time? |
| Logs | JSON lines on stdout | What exactly happened? |

Every log line written inside a traced request carries `trace_id` and `span_id` next to
`request_id`, so a slow trace can be looked up in the logs and a log line in Jaeger.

## Run it

```bash
make obs-up              # the stack + Prometheus, Grafana, Jaeger; tracing switched on
make smoke-observability # proves metrics, dashboard and traces work end to end
make obs-down
```

| What | Where |
| --- | --- |
| Grafana (dashboard "MirrorMarket overview", opens as the home page) | http://127.0.0.1:3001 |
| Prometheus (targets, alert rules) | http://127.0.0.1:9090 |
| Jaeger (traces, service `mirrormarket-api`) | http://127.0.0.1:16686 |

The three containers are in the Compose profile `observability`; `make up` does not start
them, and tracing stays off unless `OTEL_ENABLED=true`. All ports listen on 127.0.0.1 only.

## Metrics

Code: `backend/app/telemetry/metrics.py`. The API serves `/metrics`; the two workers have
no HTTP API and serve their own on `WORKER_METRICS_PORT` (9100 inside Compose).

| Metric | Type | Meaning |
| --- | --- | --- |
| `mm_build_info` | gauge | Running version (value is always 1). |
| `mm_http_requests_total` | counter | HTTP requests by method, route template and status code. |
| `mm_http_request_duration_seconds` | histogram | Time to handle an HTTP request, by method and route template. |
| `mm_http_requests_in_progress` | gauge | HTTP requests being handled right now. |
| `mm_agent_runs_total` | counter | Agent runs by agent, engine (rules or openai) and status. |
| `mm_agent_run_duration_seconds` | histogram | Time one agent run took. |
| `mm_agent_degraded_total` | counter | Agent runs that fell back to the offline engine because the model call failed. |
| `mm_llm_tokens_total` | counter | LLM tokens used, by agent. |
| `mm_search_duration_seconds` | histogram | Time one evidence search took, by mode. |
| `mm_search_degraded_total` | counter | Hybrid searches that fell back to full-text only. |
| `mm_ask_answers_total` | counter | Questions by outcome: answered with citations, or abstained. |
| `mm_evidence_sentences_withheld_total` | counter | Evidence sentences withheld because they look like instructions to an assistant. |
| `mm_rate_limited_total` | counter | Requests rejected by a rate limit, by limit. |
| `mm_cache_requests_total` | counter | Cache lookups by namespace and result (hit, miss, bypass). |
| `mm_ws_connections` | gauge | Open WebSocket connections on this instance. |
| `mm_audit_events_total` | counter | Security-relevant events by action and outcome. |
| `mm_db_pool_connections` | gauge | Pooled database connections: in use or idle. |
| `mm_db_pool_timeouts_total` | counter | Requests answered 503 because no database connection became free in time. |
| `mm_embedding_jobs_total` | counter | Embedding jobs by result. |
| `mm_outbox_pending_events` | gauge | Events committed but not yet published to Kafka. |
| `mm_outbox_oldest_pending_seconds` | gauge | Age of the oldest unpublished event. |
| `mm_events_relayed_total` | counter | Outbox events by publish result. |
| `mm_events_consumed_total` | counter | Consumed events by result: handled, duplicate, retry, dead_letter. |

Plus the standard `python_gc_*` metrics, and `process_*` (CPU, memory, open files) on Linux, which is what the containers run.

Rules the labels follow:

- A label is a small fixed set. The `route` label is the route as written in the code
  (`/api/v1/workspaces/{workspace_id}`), never the URL; paths that match no route share
  the value `unmatched`. No user id, workspace id, product id, email or model name is
  ever a label value.
- Scrapes of `/metrics` are not counted as requests.

## Dashboard and alerts

- Dashboard: `infrastructure/observability/grafana/dashboards/mirrormarket.json`, provisioned
  from files (edits in the UI are not saved). Rows: Is it working?, HTTP, Agents, search and
  answers, Events and background work, Security signals, Process.
- Alert rules: `infrastructure/observability/prometheus/alerts.yml`.

| Alert | Fires when |
| --- | --- |
| TargetDown | A service has not answered scrapes for a minute |
| HighErrorRate | More than 5% of API requests fail |
| SlowRequests | p95 latency of ordinary API requests is above 1 second |
| AgentRunsFailing | More than 20% of agent runs fail |
| ModelCallsDegraded | An agent fell back to the offline engine |
| SlowAgentRuns | p95 duration of an agent's runs is above 30 seconds |
| OutboxBacklog | Events wait more than a minute to be published |
| EventsDeadLettered | A consumer gave up on an event |
| EmbeddingJobsFailing | An embedding job failed for good |
| RefreshTokenReuse | A rotated refresh token was presented again |
| LoginFailuresSpike | More than one failed login per second for 5 minutes |
| InstructionsFoundInEvidence | Evidence contained text that looks like instructions |
| DatabasePoolExhausted | Every pooled connection has been in use for 5 minutes |
| RateLimitingSustained | A rate limit keeps rejecting requests |

The rules are loaded and evaluated by Prometheus. There is no Alertmanager in the local
stack, so nothing is sent anywhere: firing alerts are visible in Prometheus under Alerts.
The thresholds are starting points chosen by reasoning, not tuned on production traffic.

A test (`tests/api/test_metrics.py`) fails if the dashboard or an alert rule uses a metric
name the code does not expose.

## Traces

Code: `backend/app/telemetry/tracing.py`. Off by default (`OTEL_ENABLED=false`).

- One trace per HTTP request, continued from an incoming `traceparent` header.
- Child spans: each SQL statement, each outbound HTTP call (the language model, fetched
  web pages), and each agent step of an analysis (`agent.product_research`, `agent.risk`, ...).
- Health, readiness and `/metrics` requests are not traced.
- `OTEL_SAMPLE_RATIO` (default 1.0) is decided once per trace, so a trace is never partial.
- SQL spans record the statement text only. Statements are parameterised, so values
  (emails, password hashes, document text) are not in a span. Request and response bodies,
  headers and query parameters of model calls are not recorded.
- Spans are exported in batches over OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT`. If the
  collector is down, spans are dropped and requests are unaffected.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRICS_ENABLED` | `true` | Serve `/metrics` |
| `METRICS_TOKEN` | empty | If set, `/metrics` requires `Authorization: Bearer <token>` |
| `WORKER_METRICS_PORT` | empty | Port for a worker's own `/metrics` |
| `OTEL_ENABLED` | `false` | Record and export traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP collector |
| `OTEL_SERVICE_NAME` | `mirrormarket-api` | Service name shown in Jaeger |
| `OTEL_SAMPLE_RATIO` | `1.0` | Share of new traces recorded |

## Limits

- Metrics are per process and kept in memory. The API runs one worker process per
  container; several processes per container would need Prometheus multiprocess mode.
- Only the API is traced. A trace ends at the outbox: the event relay, the Kafka consumer
  and the embedding worker are measured by metrics, not joined to the request's trace.
- The frontend (Next.js) is not instrumented.
- No log aggregation: logs are on stdout (`make logs`); the trace id is the link.
- No Alertmanager and no SLO burn-rate alerts.
- Jaeger keeps traces in memory and loses them on restart.
