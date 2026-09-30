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

## ADR-009: Session per request; the service layer owns commits

- **Status:** Accepted (Phase 2)
- **Decision:** `get_db_session` yields one `AsyncSession` per request, closes
  it, and rolls back on error, but never commits. Repositories add, flush and
  query only. Services call `commit()` once a use-case has fully succeeded.
  `expire_on_commit=False`, because async code cannot lazily reload attributes.
- **Consequences:** Several repository calls form one atomic unit of work.
  Nothing is persisted just because a handler returned. Tests bind sessions to
  an outer transaction with `join_transaction_mode="create_savepoint"`, so code
  that commits still leaves no trace in the database.

## ADR-010: Migrations ship in the API image and run as a one-shot job

- **Status:** Accepted (Phase 2)
- **Decision:** `alembic.ini` and `migrations/` are copied into the API image.
  Compose runs a `migrate` service (`alembic upgrade head`) that must exit 0
  before `api` starts. On AWS (Phase 31) the same image runs as a one-off
  ECS task before the service is deployed.
- **Alternatives:** Migrating on API startup. Rejected: with N replicas, N
  processes race to migrate, and a failed migration crash-loops the API.
- **Consequences:** The schema version and code version always travel together.
  Migrations must stay backward compatible with the previous release while
  rolling deploys overlap.
- **Note:** The folder is named `migrations/` rather than `alembic/` so it can
  never shadow the `alembic` library on `sys.path`.

## ADR-011: Readiness verifies the schema revision, not just connectivity

- **Status:** Accepted (Phase 2)
- **Decision:** `/api/v1/ready` returns 200 only when `SELECT 1` succeeds within
  a time budget *and* `alembic_version` matches the head revision bundled
  with the code. Failures return 503 with reasons from a fixed vocabulary.
- **Consequences:** A load balancer won't route traffic to an instance whose
  code expects tables that don't exist yet. `/health` stays dependency-free
  (ADR-006).

## ADR-012: Real PostgreSQL in tests (Testcontainers), never SQLite

- **Status:** Accepted (Phase 2)
- **Decision:** DB tests run against `pgvector/pgvector:0.8.6-pg17-trixie`,
  started by Testcontainers. Setting `TEST_DATABASE_URL` points them at an
  existing server instead. Migration tests each get a brand-new database; all
  other DB tests share one migrated database and roll back after every test.
- **Consequences:** Tests exercise the real SQL dialect, constraints,
  transactional DDL and pgvector. `make test-unit` runs only the tests that
  don't need Docker. A schema-drift test fails whenever a model changes
  without a matching migration.

## ADR-013: Stateless JWT access tokens now; refresh and revocation in Phase 22

- **Status:** Accepted (Phase 3)
- **Decision:** Short-lived (15 min) HS256 JWTs signed with `JWT_SECRET_KEY`.
  The verifier pins the algorithm, requires `iss`/`aud`/`exp`/`jti`/`typ`, and
  rejects `alg=none`. Staging and production refuse to start with the dev
  secret or any secret under 32 characters. Passwords use Argon2id, capped at
  128 characters to prevent hash-DoS.
- **Alternatives:** Server-side sessions in Redis; RS256 with a key pair.
- **Consequences:** No per-request session lookup beyond loading the user,
  which also enforces `is_active`. Logout and revocation need refresh tokens and
  a deny-list (Phase 22). RS256 becomes worthwhile once other services verify
  tokens.

## ADR-014: 404 for non-members, 403 for insufficient role

- **Status:** Accepted (Phase 3)
- **Decision:** A user who isn't a member of a workspace gets 404
  (`workspace_not_found`), whether or not the workspace exists. A member whose
  role is too low gets 403.
- **Consequences:** Guessing workspace IDs (IDOR probing) can't confirm that a
  workspace exists. Members still get an honest "you lack permission".

## ADR-015: One error envelope for every failure

- **Status:** Accepted (Phase 3)
- **Decision:** Every error response has the shape
  `{"error": {"code", "message", "request_id", ...}}`. Services raise typed
  `AppError`s with no dependency on HTTP. Validation errors list their location
  and reason, but never the submitted values, which may include passwords.
- **Consequences:** Clients can branch on a stable `code`, and support can
  trace any error through `request_id`.

## ADR-016: Roles are VARCHAR + CHECK, not native PostgreSQL enums

- **Status:** Accepted (Phase 3)
- **Decision:** Role columns are `VARCHAR(16)` with a named CHECK constraint
  (`ck_<table>_role_valid`), mapped to Python `StrEnum`s.
- **Consequences:** Adding a role is an ordinary migration that replaces the
  constraint, with no `ALTER TYPE`. The database still rejects unknown values.
