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
