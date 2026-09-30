# Architecture

This document describes the architecture **as built**, followed by the target
design it is growing toward. Sections are updated at the end of each phase.

## 1. Current state (Phase 1)

```
                 ┌────────────────────────── docker compose (127.0.0.1 only) ─┐
  client ──HTTP──▶  api  (FastAPI / uvicorn, non-root, read-only FS)         │
  X-Request-ID   │   │                                                        │
                 │   ├── postgres  pgvector/pgvector:0.8.6-pg17  (volume)     │
                 │   └── redis     redis:7.4-alpine  (no persistence)         │
                 └────────────────────────────────────────────────────────────┘
```

In Phase 1 the API **does not connect** to PostgreSQL or Redis yet. Compose
starts them (healthy, pgvector enabled) so Phase 2 can wire in the database
without touching infrastructure.

### Request lifecycle

```
uvicorn
  └─ RequestContextMiddleware      (outermost)
       1. resolve X-Request-ID: reuse if well-formed, else uuid4
       2. store in a contextvar → every log line in this request carries it
       3. call inner app ─────────────▶ CORSMiddleware (only if origins configured)
                                          └─ FastAPI router /api/v1 → endpoint
       4. add X-Request-ID to response headers
       5. unhandled exception → log with traceback, return generic JSON 500
       6. emit one structured "request completed" log line (method, path, status, ms)
```

### Backend module layout

| Package | Responsibility | Introduced |
| --- | --- | --- |
| `app/main.py` | `create_app()` factory, lifespan, middleware + router wiring | 1 |
| `app/core/config.py` | `Settings` (pydantic-settings), validation of unsafe combinations | 1 |
| `app/core/logging.py` | JSON/console formatters, single stdout handler for all loggers | 1 |
| `app/core/request_context.py` | request-ID contextvar and validation | 1 |
| `app/core/middleware.py` | correlation, access logging, safe 500s (pure ASGI) | 1 |
| `app/api/` | routers, shared dependencies (`deps.py`) | 1 |
| `app/schemas/` | Pydantic API contracts, separate from ORM models | 1 |
| `app/models/`, `app/repositories/` | SQLAlchemy models and the only code that runs SQL | 2 |
| `app/domain/`, `app/services/` | business rules (pure) and use-cases (orchestration) | 3+ |
| `app/security/` | auth, RBAC, SSRF and prompt-injection defences | 3, 22 |
| `app/ingestion/`, `app/retrieval/` | source snapshots → chunks → embeddings → hybrid search | 5-7 |
| `app/agents/`, `app/providers/` | bounded agents, orchestrator, OpenAI adapters | 10-15 |
| `app/realtime/`, `app/events/`, `app/workers/` | WebSockets, outbox + Kafka, consumers | 20-21 |
| `app/telemetry/` | OpenTelemetry, Prometheus | 28 |

Dependency direction (enforced by review now, possibly by import-linter later):

```
api → services → domain
          ↘ repositories → models
          ↘ providers (OpenAI, etc.)
core is importable by everything; nothing imports api.
```

### Configuration

- One `Settings` object, parsed from environment variables (and `.env` when
  running on the host), created once and stored on `app.state.settings`.
- Endpoints get it through the `get_app_settings` dependency, so tests can
  inject custom settings via `create_app(settings)`.
- Secrets (`DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`) are `SecretStr`:
  masked in `repr`, logs and tracebacks.
- Startup fails on unsafe config: `debug=true` or non-JSON logs in
  production, or a `*` CORS origin anywhere.

### Logging

- All loggers (app, uvicorn, httpx, and later SQLAlchemy/OpenAI) go through a
  single stdout handler. Containers log to stdout; the platform (Docker,
  CloudWatch) collects it.
- `LOG_FORMAT=json` (default, required in production): one JSON object per line
  with `timestamp` (UTC), `level`, `logger`, `message`, `request_id` and any
  `extra={...}` fields.
- `LOG_FORMAT=console`: readable single-line output for local development.
- Health-probe requests log at DEBUG so probes don't flood INFO logs.
- Query strings are never logged (they can carry tokens).

### Container

- Multi-stage build: `uv sync --locked --no-dev` in the builder; the runtime
  image contains only the virtualenv and `app/`.
- Runs as UID 10001; source owned by root; Compose adds `read_only`,
  `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp`.
- Image `HEALTHCHECK` calls `/api/v1/health` using the Python standard library
  (no curl in the image).

## 2. Target architecture

```
Next.js UI ──REST/WebSocket──▶ FastAPI API ──▶ PostgreSQL + pgvector (system of record)
                                   │   ├────▶ Redis (cache, rate limits, presence pub/sub)
                                   │   └────▶ outbox table ──▶ Kafka ──▶ workers
                                   │                                     ├ ingestion / embeddings
                                   │                                     ├ research orchestrator + agents
                                   │                                     └ notifications
                                   └──▶ OpenAI (chat, embeddings) through providers/
```

The core product flow, from the project brief:

```
workspace → requirements → products → sources → ingestion → chunking → embeddings
→ pgvector → hybrid retrieval → evidence pack → specialised agents
→ citation validation → comparison engine → realtime collaborative UI
```

This section is refined as each phase lands. Design decisions are recorded in
[DECISIONS.md](DECISIONS.md).
