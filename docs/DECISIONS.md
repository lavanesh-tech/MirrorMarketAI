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

## ADR-017: Global product catalog, workspace-scoped usage

- **Status:** Accepted (Phase 4)
- **Decision:** `products` and their variants, identifiers and specs form one
  shared catalog. A workspace references products through `workspace_products`,
  which is where tenant data such as notes lives. A product that any workspace
  uses can't be hard-deleted (FK `RESTRICT`).
- **Alternatives:** A private copy of each product per workspace. Rejected: it
  would duplicate evidence and embeddings (Phases 5–7) for every workspace.
- **Consequences:** Retrieval and evidence can be shared across workspaces,
  while collaboration data stays isolated. Catalog edits are limited to the
  product's creator until sources and moderation exist.

## ADR-018: Specifications are typed values, never free text only

- **Status:** Accepted (Phase 4)
- **Decision:** `product_specifications` stores `value_number NUMERIC(18,6)`
  **xor** `value_text`, plus a `unit`. Keys are snake_case and unique per
  (product, variant); `NULLS NOT DISTINCT` makes product-level keys unique too.
  The API uses decimals (no floats) and rejects NaN and Infinity.
- **Consequences:** Hard constraints and scoring ("RAM ≥ 16 GB") are exact SQL
  or Python comparisons. The LLM never does arithmetic on specs.

## ADR-019: Hand-rolled SSRF-safe fetcher with IP pinning

- **Status:** Accepted (Phase 5)
- **Decision:** Use a small, fully tested fetcher (`SafeFetcher`) instead of
  plain `httpx.get`. It resolves DNS itself, requires *every* resolved address
  to be public, connects to the validated IP, and sends the original `Host`
  header and TLS SNI, so certificate checks still apply to the hostname.
  Redirects are followed by hand and each hop is re-validated. It also enforces
  size, time and content-type limits.
- **Consequences:** Defends against internal-network access, cloud-metadata
  theft, DNS rebinding and redirect tricks. The trade-off is that sites which
  need other ports, or which are reachable only through a proxy, are
  intentionally unsupported.

## ADR-020: Immutable, content-addressed snapshots

- **Status:** Accepted (Phase 5)
- **Decision:** Each distinct set of bytes becomes one `source_snapshots` row,
  unique by `(source_id, sha256)`, with the raw bytes kept. A re-fetch that
  returns the same bytes reuses the snapshot and bumps `last_seen_at`. Parsed
  text lives in `source_documents`, one per snapshot.
- **Consequences:**
  - Unchanged pages aren't re-parsed or re-embedded.
  - Any document can be re-parsed later from its original bytes.
  - Citations (Phase 9) can point to the exact version of the evidence.
  - Raw bytes sit in PostgreSQL until the S3 move in Phase 31.

## ADR-021: Uploads are typed by content, not by claims

- **Status:** Accepted (Phase 5)
- **Decision:** Upload type comes from magic bytes (`%PDF-`, HTML markers);
  other binaries are rejected. Uploads are read with a hard byte cap, filenames
  are sanitized, and PDFs are limited by page count, with encrypted files
  refused. Uploaded sources always get `USER` authority.

## ADR-022: Embeddings in a separate table, one row per (chunk, model)

- **Status:** Accepted (Phase 6)
- **Decision:** `chunk_embeddings(chunk_id, model, embedding vector(1536))`
  with an HNSW index (`vector_cosine_ops`, m=16, ef_construction=64). The
  vector size is fixed by migration, and settings refuse to start with any
  other size.
- **Consequences:** Switching or A/B-testing an embedding model means adding
  rows, not rewriting chunks. Changing dimensions needs a migration.

## ADR-023: Database-backed job queue with SKIP LOCKED (before Kafka)

- **Status:** Accepted (Phase 6)
- **Decision:** `embedding_jobs` is created in the same transaction as the
  document. Workers claim jobs with `FOR UPDATE SKIP LOCKED`. Failures retry
  up to `EMBEDDING_JOB_MAX_ATTEMPTS`, after which the job is marked FAILED.
- **Consequences:**
  - Jobs are durable and exactly-once, with any number of parallel workers and
    no new infrastructure.
  - Kafka (Phase 21) will add push-based wake-ups and fan-out, but this table
    stays the source of truth for job state.

## ADR-024: Offline "hashing" embedder as the default provider

- **Status:** Accepted (Phase 6)
- **Decision:** `EMBEDDING_PROVIDER=hashing` by default, `openai` when a key is
  configured. The provider interface is the same either way.
- **Consequences:** Tests, CI and the demo never depend on an external API or
  cost money. Retrieval-quality numbers will be reported separately for each
  provider (Phase 27), and hashing results are never presented as semantic
  quality.

## ADR-025: Hybrid retrieval in PostgreSQL with Reciprocal Rank Fusion

- **Status:** Accepted (Phase 7)
- **Decision:** Keep search inside PostgreSQL. Full-text uses a generated
  `tsvector` column with a GIN index and `websearch_to_tsquery` (never raises on
  user input). Vector search uses pgvector cosine distance on the HNSW index with
  `hnsw.iterative_scan = strict_order`, so filtered queries still return enough
  rows. The two ranked lists are merged with RRF (k=60), which uses ranks only
  and needs no score calibration.
- **Access control:** Workspace membership is checked first (404 for
  non-members). Visibility filters (workspace-private sources, or shared sources
  of products in the workspace, latest document only) are SQL predicates applied
  before ranking, so other tenants' chunks can never be ranked or leaked.
- **Consequences:**
  - No extra search cluster (OpenSearch/Elasticsearch) to run or keep in sync.
  - If the embedding provider fails, hybrid falls back to full-text and reports
    `degraded: true`. Vector-only mode returns 503.
  - Needs pgvector 0.8 or later for iterative scans.

## ADR-026: Immutable requirement versions with optimistic locking

- **Status:** Accepted (Phase 8)
- **Decision:** Each workspace has one `purchase_requirements` row whose
  `current_version` counter is bumped on every save, and every save inserts an
  immutable `requirement_versions` row. Clients send `expected_version`; the
  server locks the parent row (`SELECT ... FOR UPDATE`) and returns 409 when it
  doesn't match. The first save's race is resolved by the unique `workspace_id`.
- **Consequences:**
  - Recommendations (Phase 15/16) can record exactly which version they used.
  - Collaborators never silently overwrite each other (lost-update protection).
  - Re-sending identical content is a no-op (200), so retries are safe.

## ADR-027: Requirement extraction = offline rules by default, LLM optional

- **Status:** Accepted (Phase 8)
- **Decision:** `REQUIREMENTS_EXTRACTOR=rules` uses a deterministic pattern
  extractor. `openai` uses chat completions with a strict JSON Schema; the output
  is validated by the same `RequirementSpec` model the API uses, and the brief
  is marked as untrusted data in the system prompt. If the LLM call fails, the
  service falls back to rules and reports `degraded: true`.
- **Consequences:** Tests and CI are deterministic and free. Invalid model output
  can never be stored. Users always review the spec (preview, then save) before
  it drives any comparison.

## ADR-028: Evidence packs as snapshots + deterministic citation validation

- **Status:** Accepted (Phase 9)
- **Decision:** Agents and Q&A (Phases 10-17) never cite live chunks. They cite an
  evidence pack: search results frozen into numbered items (E1..En) whose text and
  source metadata are copied. A deterministic validator rejects unknown markers,
  uncited substantive sentences, numbers not present in the cited items, and
  quoted phrases not found verbatim.
- **Consequences:** Citations stay verifiable after re-ingestion or deletion.
  Hallucinated figures behind real citations are caught without an LLM judge.
  The validator is lexical: paraphrased claims aren't semantically checked
  (Phase 27 evaluates that).

## ADR-029: Agents produce cited facts; verdicts are computed in code

- **Status:** Accepted (Phase 10)
- **Decision:** An agent gathers evidence (one product-filtered hybrid search
  per criterion), freezes it as an evidence pack and extracts values. Values
  come from the rule patterns, or from the LLM, where a value counts only if it
  cites an existing pack item. MET/UNMET against the requirement is always
  computed deterministically (`satisfies()`), never by the model. Every run is
  stored with its citation-validation report, engine, duration and token count.
- **Consequences:** Results are auditable and reproducible offline. A model
  can't flip a verdict or cite evidence that doesn't exist, and fabricated
  numbers in summaries show up as validation issues.

## ADR-030: Review analysis = per-opinion labels, aggregation in code

- **Status:** Accepted (Phase 11)
- **Decision:** The Review Intelligence Agent reads every visible REVIEW chunk of
  the product (not a top-k search, so counts aren't biased by ranking). It labels
  (aspect, polarity) per clause with a lexicon and negation window, or with the LLM,
  where a label only counts if its quote is verbatim in the cited item. Counts,
  sentiment (MIXED unless one side has a 3:1 majority), praises and complaints are
  computed in code. Summary sentences cite items and contain no numbers.
- **Consequences:** Results are reproducible and auditable, and LLM labels can't
  invent quotes. The rules engine misses sarcasm and implicit opinions (measured in
  `benchmarks/review_sentiment.py`). Analysis is capped at
  `AGENT_MAX_REVIEW_CHUNKS` chunks.

## ADR-031: Compatibility = code-derived capabilities + evidence checks

- **Status:** Accepted (Phase 12)
- **Decision:** Owned devices are stored in the versioned requirements
  (`owned_devices`) and mapped to a fixed capability vocabulary in code (no
  model decides what "Sony TV" needs). Each capability is checked against
  product-filtered evidence, with negation limited to the capability's own clause,
  falling back to catalog has_* specs. INCOMPATIBLE if any capability is NOT_SUPPORTED,
  COMPATIBLE only if all are SUPPORTED, otherwise UNCERTAIN.
- **Consequences:** Verdicts are explainable (device → capability → cited
  sentence). Devices outside the vocabulary are listed as `unmapped_devices`
  rather than guessed. The benchmark reports a held-out set because the rules were
  tuned on the main set.

## ADR-032: The Value Agent is deterministic (no LLM)

- **Status:** Accepted (Phase 13)
- **Decision:** Pricing and value math use exact Decimals in code. The price comes
  from cited evidence (or the catalog), budget fit never converts currencies, and
  requirement fit reuses the latest Product Research run for the same requirement
  version instead of re-deriving facts. MUST criteria weigh 5; SHOULD criteria use
  their 1-5 weight.
- **Consequences:** Value numbers are reproducible and auditable, with no LLM
  arithmetic. Accurate value scores need a research run first (Phase 15
  orchestration will sequence them). Prices are point-in-time evidence until
  Phase 18 adds price history.

## ADR-033: Risks combine evidence with other agents' stored verdicts

- **Status:** Accepted (Phase 14)
- **Decision:** The Risk Agent doesn't recompute other agents' work. It reads the
  latest successful research, compatibility and value runs for the same
  requirement version and turns their failures into risks. Evidence risks come
  from negation-aware sentence patterns. Severities are fixed in code (e.g. safety
  is HIGH; reliability is HIGH when two or more evidence items report failures).
  Full-text queries use `or` so any single term can match.
- **Consequences:** Risk output is only as fresh as the runs it reads. Phase 15
  orchestration runs the agents in dependency order: research, compatibility and
  value before risk.

## ADR-034: Deterministic synthesis + bounded, failure-isolated orchestration

- **Status:** Accepted (Phase 15)
- **Decision:** The final recommendation is computed in code from stored agent
  outputs. Hard constraints (a MUST criterion UNMET, INCOMPATIBLE) gate the verdict
  before any score is considered, and every reason names its source agent. The
  orchestrator runs agents sequentially in dependency order on one DB session. Each
  step runs in a savepoint, so one agent's failure becomes a FAILED run and doesn't
  abort the analysis. Budgets are enforced between steps (time → skip, tokens →
  offline engines, product cap) and never by cancelling an in-flight DB query.
- **Consequences:** Results are reproducible and explainable, and a broken agent
  degrades the analysis instead of failing it. Analysis runs inside the request
  for now; Phase 21 moves it to Kafka-driven workers using the same orchestrator.

## ADR-035: Transparent multi-criteria comparison with hard constraints

- **Status:** Accepted (Phase 16)
- **Decision:** Products are compared with a weighted sum of per-criterion
  utilities. Meeting a requirement is worth 0.7; how far a product sits in the
  min-max range of the compared products, in the criterion's direction, is
  worth 0.3. Unknown values score 0. Hard constraints filter rather than
  penalise: an ineligible product is shown with its score but ranked last. A
  product with an unverified MUST can't be declared the winner. Every cell
  reports its contribution in points, and a sensitivity check reports which
  weight changes would flip the winner.
- **Consequences:** Users can see why a product wins and how fragile the result
  is. Min-max normalisation is relative to the products being compared, so
  adding a product can shift scores; the score is for ranking within a
  comparison, not an absolute grade.

## ADR-036: Ask MirrorMarket = citation-enforced answers that may abstain

- **Status:** Accepted (Phase 17)
- **Decision:** Answers are built only from a frozen evidence pack. The offline
  engine returns verbatim evidence sentences, so they're supported by
  construction. Model answers are checked sentence by sentence with the citation
  validator, and any sentence with an unknown marker, an unsupported number or
  quote, or no citation is dropped and reported. When no supported sentence
  remains, or retrieval finds nothing relevant, the API abstains with a fixed
  message instead of guessing.
- **Consequences:** Hallucinated figures can't reach the user, and the
  abstention rate becomes a measurable quality signal (Phase 27). The extractive
  engine misses paraphrases ("fast charge" vs "10 minute charge"); the measured
  held-out accuracy reflects that.

## ADR-037: Price history as global, idempotent snapshots; aggregation in SQL

- **Status:** Accepted (Phase 18)
- **Decision:** Prices are public facts, so snapshots belong to the catalog
  product, not a workspace. Each observation is unique per (product, retailer,
  currency, observed_at), and inserts use ON CONFLICT DO NOTHING, so re-sending a
  batch is safe. Bucketing (date_trunc + min/max per retailer) runs in PostgreSQL;
  statistics are computed in Python with Decimals. Currencies are never converted.
- **Consequences:** The Value Agent can use dated, retailer-attributed prices.
  The benchmark showed the original slowness came from application code (an
  O(n²) loop), not the database, and that the composite index doesn't help yet
  at tens of thousands of rows. It's kept for larger histories.

## ADR-038: Redis as an optional accelerator that fails open

- **Status:** Accepted (Phase 19)
- **Decision:** Redis holds only derived or short-lived state: cache entries,
  rate-limit counters, idempotency records and one-time tokens. PostgreSQL stays
  the source of truth. Every Redis call is wrapped so an outage degrades to "no
  cache, no limit, no replay" instead of failing requests, and `/ready` reports
  Redis without gating on it. Rate limiting uses GCRA in a single Lua script with
  Redis's own clock (one key per identity, exact Retry-After, no replica clock
  skew). The cache invalidates by bumping a per-product version number instead of
  deleting keys, which avoids SCAN and the stale-write race. Idempotency is ASGI
  middleware keyed by caller + method + path + key, claimed with SET NX, so a
  retried POST never runs an agent twice.
- **Consequences:** Failing open means a Redis outage also disables brute-force
  protection; that trade-off favours availability and is logged as a warning.
  Stored idempotent responses are kept for 24 h. Ordinary tests run with Redis
  off, so rate-limit counters never leak between tests.

## ADR-039: Push-only WebSockets, Redis pub/sub fan-out, REST for all writes

- **Status:** Accepted (Phase 20)
- **Decision:** A workspace WebSocket only delivers events; comments, votes and
  agent runs are written through REST and then published. The token is sent in the
  first message (not the URL), the socket closes when the token expires, and no
  database connection is held while a socket is open. Events fan out across API
  replicas through Redis pub/sub and fall back to local delivery. Each socket has
  a bounded queue; slow clients are disconnected. Presence is a TTL'd Redis sorted
  set keyed by connection.
- **Consequences:** One code path for validation and RBAC, and sockets stay cheap.
  Delivery is at-most-once with no replay, so clients must refetch after
  reconnecting; durable event delivery is Kafka's job (Phase 21). Membership
  changes do not close existing sockets yet.

## ADR-040: Transactional outbox, at-least-once Kafka, idempotent consumers

- **Status:** Accepted (Phase 21)
- **Decision:** A change and its event are written in one PostgreSQL transaction
  (outbox table); a relay publishes to Kafka afterwards and marks rows published
  only after the broker acknowledged them. Delivery is therefore at-least-once,
  and every consumer deduplicates with an inbox row `(consumer, event_id)` that
  commits together with its side effects. Offsets are committed manually after
  that transaction. Failed messages are retried, then copied to a dead-letter
  topic. Relay and consumer code depend on two small broker protocols; Kafka is
  one adapter and tests use an in-memory one plus a real broker in CI.
- **Alternatives rejected:** publishing from the request handler (events lost or
  phantom on crash); Kafka transactions for exactly-once (does not cover the
  PostgreSQL write); change data capture with Debezium (more moving parts than
  this project needs).
- **Consequences:** No lost or phantom events, and duplicates are harmless.
  The feed is eventually consistent. Ordering is per key (workspace), not global.
  Redis pub/sub stays for transient WebSocket hints.

## ADR-041: Rotating refresh tokens, versioned access tokens, append-only audit log

- **Status:** Accepted (Phase 22)
- **Decision:** Access tokens stay short-lived and stateless. Sessions are held by
  opaque refresh tokens that are hashed at rest and rotated on every use; re-use of
  a rotated token revokes the session. Immediate revocation of access tokens uses a
  per-user `token_version` claim instead of a token denylist. Audit events are
  written in the same transaction as the action into a table that a trigger makes
  append-only and that has no foreign keys. Evidence is treated as untrusted input
  to the LLM: neutralised, scanned sentence by sentence, and backed by output
  validation. Authorization is regression-tested from the OpenAPI schema.
- **Alternatives rejected:** long-lived access tokens (no revocation); a Redis
  denylist of token ids (must never fail open, and Redis is optional here);
  relying on prompt wording or detection alone against injection.
- **Consequences:** One extra indexed lookup per request is avoided (the user row
  is already loaded). A stolen refresh token is usable at most once before the
  session dies. Detection heuristics miss paraphrased attacks (measured), which is
  why they are only one of four layers.

## ADR-042: API documentation is generated from code and tested for drift

- **Status:** Accepted (Phase 23)
- **Decision:** The OpenAPI document comes from the FastAPI routes plus one module
  that adds the API-wide conventions (error envelope, common errors, idempotency
  and request-id headers). The committed contract, the endpoint index and the
  Postman collection are generated from it by `tools/api_docs.py`. Tests fail when
  the committed files are stale, when an operation lacks a summary/tag/stable id,
  when an example body violates its schema, or when the collection's example
  journey stops working against the real API.
- **Alternatives rejected:** hand-written reference docs or a hand-maintained
  Postman collection (they drift); publishing only the live `/openapi.json` (no
  reviewable diff of API changes in pull requests).
- **Consequences:** Every API change shows up as a diff of `docs/api/openapi.json`
  and needs `make api-docs`. Operation ids are now part of the public contract.

## ADR-043: Next.js backend-for-frontend; tokens never reach browser JavaScript

- **Status:** Accepted (Phase 24)
- **Decision:** The browser talks only to the Next.js app. Route handlers keep the
  access and refresh tokens in HttpOnly, SameSite=Lax cookies and forward
  `/api/v1/*` to FastAPI with the bearer token added server-side; they refresh
  once and retry when the access token is rejected. Endpoints that return tokens
  are reachable only through `/api/session/*`, which strip them. State-changing
  requests must come from the same origin. The API client and its types are
  generated from the OpenAPI contract. Pages fetch data client-side through the
  forwarder (TanStack Query); `proxy.ts` only redirects signed-out visitors.
- **Alternatives rejected:** tokens in localStorage or JS-readable cookies (any XSS
  steals the session); calling FastAPI directly from the browser (needs CORS and
  exposes tokens to scripts); Server Components fetching with cookies (they cannot
  update cookies, so they cannot rotate refresh tokens).
- **Consequences:** No CORS configuration is needed for the web app. One extra hop
  per API call. Because several requests can try to refresh at once, the API now
  accepts a re-used refresh token for 10 seconds after its rotation
  (`REFRESH_REUSE_LEEWAY_SECONDS`); beyond that, re-use still revokes the session.

## ADR-044: Every write is committed by its service; tests end each request like production

- **Status:** Accepted (Phase 25)
- **Context:** Building the UI against a running API showed that saving requirements,
  freezing an evidence pack through the API and recording agent runs returned 201 but
  stored nothing. Those services flushed and never committed, and the per-request
  session rolls back whatever is uncommitted when it closes. The test suite missed it
  because all requests in a test share one session, so flushed rows stayed visible.
- **Decision:** The service that makes a change commits it. `RequirementService.save`,
  `EvidenceService.create` and `AgentService.record` / `record_failure` commit (a run,
  its evidence pack and its outbox event in one transaction). The orchestrator still
  isolates each agent step in a savepoint and commits after every step, so finished
  steps survive a later failure. In tests, the shared session is rolled back at the end
  of every request, exactly as closing a real session would, so any future write that
  is not committed is gone by the next request and the test fails.
- **Alternatives rejected:** committing in the request dependency (a handler that
  returns after a partial failure would persist half-finished work); a separate
  database per test with real sessions (slow, and tests would no longer be isolated by
  rollback).

## ADR-045: Workspace UI reads agent output defensively and shows why a product wins

- **Status:** Accepted (Phase 25)
- **Decision:** A comparison is an agent run whose `output` is free-form JSON in the
  API contract, so the UI validates it with a zod schema before rendering and treats
  anything else as "no comparison". The matrix puts criteria in rows and products in
  rank order, states each cell's status in words (not colour alone), shows the points
  each cell contributes, highlights values that carry citations, and reports whether
  the winner survives halving or doubling any one importance. Research (all agents)
  and rescoring (weights only) are separate actions. Edit controls follow the API's
  role rules, which remain the real enforcement.
- **Alternatives rejected:** trusting the output shape with a type cast (a contract
  change would crash the page); a single "compare" action that always re-runs every
  agent (slow, and costly with the OpenAI engine).

## ADR-046: Browsers open the WebSocket with a short-lived ticket, directly to the API

- **Status:** Accepted (Phase 26)
- **Context:** The WebSocket expects the access token in its first message. In the
  browser the token lives in an HttpOnly cookie that page scripts cannot read, and
  Next.js route handlers cannot proxy a WebSocket.
- **Decision:** `POST /workspaces/{id}/realtime-ticket` (authenticated like any other
  call, through the forwarder) returns a signed ticket that lasts 30 seconds, names one
  workspace and carries its own token type. The browser connects to the API's
  WebSocket address (served at runtime by `/api/config`) and sends the ticket as its
  first message. REST endpoints reject tickets, the socket rejects a ticket for another
  workspace, and "log out everywhere" invalidates tickets already issued. The socket
  stays push-only: an event only tells the page what to refetch through the API. If
  the socket cannot be opened the page says live updates are off and keeps working.
- **Alternatives rejected:** handing the access token to page scripts (undoes the
  cookie design); a cookie on the WebSocket handshake (the API is another origin, and
  cookie-authenticated sockets need their own cross-site protections); polling (no
  presence, more load); a custom Node server to proxy sockets (replaces the standard
  Next.js server for one feature).
- **Known limit:** a ticket can be replayed within its 30 seconds. That opens another
  read-only connection as the same member of the same workspace, which the
  per-user connection cap already bounds.

## ADR-047: One end-to-end journey in a real browser, run in CI against the Docker stack

- **Status:** Accepted (Phase 26)
- **Decision:** A Playwright test drives Chromium through the whole product (account,
  requirements, products with a source and a price, evidence search, comparison,
  grounded question and abstention, vote, a comment arriving live in a second window,
  log out) against the built web app and the real API from Docker Compose. Component
  behaviour stays in fast Vitest tests with the network mocked; the browser test
  exists to prove the pieces work together, which is exactly where the missing-commit
  bug of Phase 25 was hiding.
- **Alternatives rejected:** many small browser tests (slow and brittle for what unit
  tests already cover); mocking the API in the browser (would not have caught the
  bugs this layer exists for).

## ADR-048: Quality is measured end to end, on labelled data, with the baseline kept

- **Status:** Accepted (Phase 27)
- **Context:** Each component had its own benchmark and they looked good. Driving the
  whole product with questions a buyer would ask showed the offline answerer was right
  7 times in 24 and that a planted review could change which product was ruled out.
  None of the component benchmarks could have shown either.
- **Decision:** `backend/evaluation` drives the real application through its HTTP API
  against a synthetic, hand-labelled dataset and scores retrieval, extraction, facts,
  comparison decisions, answers, abstention and poisoned sources. Results are JSON
  files; `docs/EVALUATION.md` is generated from them and a test fails if the committed
  offline result is not what the code produces. The first run is kept as a baseline, and
  changes made after it are reported with before and after numbers. Because those
  changes were made with knowledge of the dataset, a held-out set was written
  afterwards and run once. Known remaining failures are listed, not hidden.
- **Alternatives rejected:** an LLM judging answers (a second unmeasured system grading
  the first, and not reproducible offline); reporting only the improved numbers
  (overstates quality, since the fixes were tuned on that data); a public benchmark
  (none covers comparison shopping with citations and abstention).

## ADR-049: Sources are trusted by kind, and offline engines never read planted instructions

- **Status:** Accepted (Phase 27)
- **Decision:** Facts and prices are read from official sources first, then by kind of
  document: specification sheets, manufacturer pages, manuals, warranties and return
  policies before reviews, reviews before notes (`app/domain/trust.py`). The offline
  answerer prefers them mildly. Sentences the injection detector flags are removed
  before the rule-based agents and the offline answerer read evidence, as they already
  were before a language model did. Questions that name a workspace product are
  answered from that product's sources only.
- **What this does not solve:** a false statement in a low-trust source is still used
  when no better source covers the same point, and can still be quoted in an offline
  answer; the detector misses paraphrases. Both are measured in `docs/EVALUATION.md`.
- **Alternatives rejected:** ignoring reviews for facts entirely (often the only source);
  majority voting between sources (an attacker adds two documents); trusting uploads
  marked "official" (uploads are never official by design).

