# Architecture

This document describes the architecture **as built**, followed by the target
design it is growing toward. Sections are updated at the end of each phase.

## 1. Current state (Phase 2)

```
                 ┌──────────────────────────── docker compose (127.0.0.1 only) ─┐
                 │  postgres (pgvector 0.8.6, PG17) ◀── healthy ──┐              │
                 │        ▲                                       │              │
                 │        │ alembic upgrade head   migrate (one-shot, exits 0)   │
                 │        │ asyncpg pool                          │ completed    │
  client ──HTTP──▶  api (FastAPI / uvicorn, non-root, read-only FS) ◀────────────┘
  X-Request-ID   │   └── redis 7.4 (started; not used by the API until Phase 19) │
                 └───────────────────────────────────────────────────────────────┘
```

Startup order: `postgres` healthy → `migrate` applies migrations and exits 0 →
`api` starts. The API never serves traffic against an old schema.

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

### Database access (Phase 2)

- **Engine:** `app/core/database.py`. SQLAlchemy 2.x `AsyncEngine` on asyncpg,
  created in the lifespan and disposed on shutdown. The pool connects lazily.
  Settings:
  - `pool_pre_ping`, `pool_recycle`, and a connect timeout.
  - A server-side `statement_timeout`, 15 s by default.
  - `application_name=mirrormarket-api`, which shows up in `pg_stat_activity`.
- **Sessions:** one `AsyncSession` per request (`get_db_session`), with
  `expire_on_commit=False` and `autoflush=False`. The dependency closes the session
  and rolls back on error. It never commits.
- **Transactions:** repositories only add, flush and query. The service layer
  owns the transaction boundary and calls `commit()`.
- **Models:** `app/models/base.py` holds `Base` with a constraint naming
  convention, plus two mixins: `UUIDPrimaryKeyMixin` (app-generated UUIDv4) and
  `TimestampMixin` (timestamptz `created_at`/`updated_at` with server defaults).
- **Repositories:** `app/repositories/base.py` has a generic `Repository[Model]`
  (`get`, `add`, `delete`, paginated `list`) and `PageRequest`, whose page size is
  capped at 100. Workspace filters will live in domain repository methods.
- **Migrations:** Alembic lives in `backend/migrations/`, with an async `env.py`
  that reads the URL from Settings. `0001` enables pgvector, which is needed on
  RDS, where the init script doesn't run.

### Probes

| Endpoint | Checks | Fails with |
| --- | --- | --- |
| `/api/v1/health` | process is up | never checks dependencies |
| `/api/v1/ready` | `SELECT 1` within 2 s; `alembic_version` equals the code's head revision | 503 with `reason` of `unreachable`, `timeout`, `not_migrated` or `schema_mismatch` |

Readiness reasons come from a fixed vocabulary, so raw exception text, which could
include hosts or credentials, never reaches clients. The details are logged
server-side.

### Identity, tenancy and authorization (Phase 3)

```
Organization (tenant) 1──* ComparisonWorkspace 1──* WorkspaceMember *──1 User
Organization          1──* OrganizationMember  *──1 User
```

- **Registration:** creates a `User` (email lower-cased, Argon2id hash) and a
  personal `Organization`, with the user as OWNER, in one transaction.
- **Login:** email + password → HS256 JWT access token (15 min). Claims:
  `sub, iss, aud, iat, nbf, exp, jti, typ=access`. Decoding pins the algorithm
  and requires every claim. Unknown emails still run a dummy Argon2 verify, so
  timing doesn't reveal which accounts exist.
- **`get_current_user`:** takes the Bearer token, verifies it, and loads an
  active user; otherwise 401 with `WWW-Authenticate: Bearer`.
- **Workspace authorization:** `WorkspaceService.authorize(workspace_id, user, minimum_role)`.
  A non-member gets **404** (existence is hidden), and a member whose role is too
  low gets **403**. Roles rank OWNER > EDITOR > MEMBER > VIEWER
  (`app/domain/roles.py`).
- **Isolation by construction:** the repositories have no "get any workspace
  by id" query. Every workspace read joins through `workspace_members` for the
  requesting user.
- **Errors:** services raise `AppError` subclasses. `app/core/errors.py` turns
  them into the standard envelope, along with validation errors (422, which
  never echo submitted values) and 404/405 responses.

### Backend module layout

| Package | Responsibility | Introduced |
| --- | --- | --- |
| `app/main.py` | `create_app()` factory, lifespan, middleware + router wiring | 1 |
| `app/core/config.py` | `Settings` (pydantic-settings), validation of unsafe combinations | 1 |
| `app/core/logging.py` | JSON/console formatters, single stdout handler for all loggers | 1 |
| `app/core/request_context.py` | request-ID contextvar and validation | 1 |
| `app/core/middleware.py` | correlation, access logging, safe 500s (pure ASGI) | 1 |
| `app/core/database.py` | async engine, session factory, ping, current revision | 2 |
| `app/core/migrations.py` | Alembic config and expected head revision | 2 |
| `migrations/` | Alembic env + versioned migrations | 2 |
| `app/api/` | routers, shared dependencies (`deps.py`) | 1 |
| `app/schemas/` | Pydantic API contracts, separate from ORM models | 1 |
| `app/models/`, `app/repositories/` | `Base` + mixins; generic repository (domain tables from Phase 3) | 2 |
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
