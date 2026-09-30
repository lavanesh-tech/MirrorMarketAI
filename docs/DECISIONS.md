# Architecture Decision Records

Short records of significant decisions: context, decision, consequences.
Newest at the bottom. A superseded decision is marked, not deleted.

---

## ADR-001: PostgreSQL + pgvector as the single primary and vector database

- **Status:** Accepted (Phase 1)
- **Context:** The product needs relational integrity (workspaces, memberships,
  prices, citations) *and* semantic search over product evidence. Retrieval must
  be filtered by workspace/product with no cross-tenant leakage.
- **Decision:** One PostgreSQL database with the pgvector extension. Full-text
  search (`tsvector`) plus vector similarity gives hybrid retrieval in the same
  engine, in the same transaction, under the same access filters.
- **Consequences:** One system to back up, migrate and secure; tenant filters
  are plain `WHERE` clauses. A dedicated vector database is reconsidered only if
  benchmarks (Phase 29) show pgvector can't meet latency/recall targets.

## ADR-002: FastAPI application factory with explicit lifespan

- **Status:** Accepted (Phase 1)
- **Decision:** `create_app(settings)` builds the app; resources (DB engine,
  Redis, HTTP clients) will be created in `lifespan` and closed after it.
  Uvicorn runs `app.main:create_app --factory`.
- **Consequences:** No import-time side effects or global connections. Tests
  build isolated apps with custom settings. Startup order is explicit.

## ADR-003: uv for Python dependency management, with a committed lockfile

- **Status:** Accepted (Phase 1)
- **Decision:** `pyproject.toml` declares minimum versions; `uv.lock` pins the
  exact resolved versions and hashes. Mac, CI and Docker all run
  `uv sync --locked`, which fails if the lock is stale.
- **Consequences:** Reproducible installs. Upgrading is a deliberate
  `uv lock --upgrade` reviewed in a commit. The backend is an application
  (`package = false`), not a published library.

## ADR-004: Standard-library logging with a custom JSON formatter

- **Status:** Accepted (Phase 1)
- **Alternatives:** structlog, loguru.
- **Decision:** Keep `logging` and format at the handler. Every third-party
  library already logs through it, so one handler gives all output the same
  JSON shape and request ID with no extra dependency.
- **Consequences:** Callers use `logger.info("msg", extra={...})`. If we need
  processor pipelines later, structlog can sit on top of the same handler.

## ADR-005: Pure ASGI middleware for request correlation and safe errors

- **Status:** Accepted (Phase 1)
- **Alternatives:** `BaseHTTPMiddleware`, a third-party correlation-ID package.
- **Decision:** A small hand-written ASGI middleware that resolves the request
  ID, stores it in a `contextvar`, echoes it in `X-Request-ID`, logs one access
  line and converts unhandled exceptions into a generic JSON 500.
- **Consequences:** No response buffering (needed later for streaming), no
  extra dependency, and the behaviour is fully covered by tests. Incoming IDs
  are validated (8-128 chars, `[A-Za-z0-9._-]`) to prevent log injection.
- **Note:** The error body shape `{"error": {"code", "message", "request_id"}}`
  is the seed for the API-wide error format standardised in Phase 23.

## ADR-006: Liveness now, readiness in Phase 2

- **Status:** Accepted (Phase 1)
- **Decision:** `GET /api/v1/health` reports process liveness only and never
  touches dependencies. A readiness endpoint that checks PostgreSQL (and later
  Redis) arrives with the database layer in Phase 2.
- **Consequences:** A database outage won't make an orchestrator restart every
  healthy API container.

## ADR-007: Local infrastructure image choices

- **Status:** Accepted (Phase 1)
- **Decision:**
  - `pgvector/pgvector:0.8.6-pg17-trixie`: PostgreSQL 17 with pgvector
    preinstalled, pinned to an exact extension version.
  - `redis:7.4-alpine`, with persistence disabled. Redis is only a cache and
    coordination layer; PostgreSQL holds durable state. The 7.x line was chosen
    because it's closest to what AWS ElastiCache runs (see the note below).
  - Host ports 5433/6380, bound to `127.0.0.1`, to avoid clashing with other
    local databases and to avoid exposing services on the network.
- **Consequences:** Version bumps are explicit. Before Phase 31 we will confirm
  which PostgreSQL and pgvector versions AWS RDS supports in the target region,
  and whether ElastiCache for Redis OSS or Valkey is the better fit, then align
  the local versions.

## ADR-008: Strict typing and linting from the first commit

- **Status:** Accepted (Phase 1)
- **Decision:** mypy `strict = true` (with the Pydantic plugin) covering both
  `app/` and `tests/`. Ruff with bugbear, bandit (`S`), async, pyupgrade and
  pytest rules. Warnings are treated as errors in pytest.
- **Consequences:** Adding strictness later is much harder than starting with
  it. Where code needs an exception, it gets a narrowly scoped
  `# type: ignore[code]` or a per-file ignore with a comment explaining why.
