# MirrorMarket AI: Project State

Read this file first when resuming the project in a new session.

## Status

- **Completed phases:** 1 (foundation), 2 (async PostgreSQL, Alembic, readiness), 3 (users,
  organizations, workspaces, memberships, JWT auth, RBAC), 4 (product catalog and workspace products),
  5 (sources, snapshots, documents, SSRF-safe URL ingestion, uploads)
- **Next phase:** 6. Chunking `source_documents` into `document_chunks`, OpenAI embeddings
  behind a provider interface (with a deterministic fake for tests and CI), a pgvector column
  plus HNSW index, and embedding jobs tracked in PostgreSQL.
- **Last updated:** 2026-09-30

## Working rules (from the owner)

- Build one phase at a time and stop after each one until the owner says "continue".
- **Response format:** after each phase, reply with ONLY the files to download (and where they go) plus the terminal commands to copy. Nothing else.
- The owner reviews, commits and pushes all work. Never change git identity and never add AI
  attribution (no Co-authored-by, no Generated-by).
- Deliver complete files plus copy-paste macOS commands. Keep explanations short and don't
  regenerate files that haven't changed.
- zsh: don't put inline `#` comments in pasted command blocks, and use `git --no-pager diff`.
- Remote tools cannot write `Makefile` or `.github/workflows/*` into the owner's folder.
  Deliver those as downloadable files and give the owner an `mv` command to place them.
- Never commit secrets. Only `.env.example` is tracked.
- Never invent metrics. Benchmarks must record date, commit SHA, dataset, hardware and config.
- A final teaching and interview-prep phase happens only after the whole project is done.

## Environment

- macOS on Apple Silicon, zsh, Docker Desktop.
- Python 3.12, managed by **uv**. `backend/uv.lock` is committed and every install uses
  `uv sync --locked`.
- Repo: `/Users/lavaneshthirukondamahendran/Desktop/MirrorMarketAI` →
  `github.com/lavanesh-tech/MirrorMarketAI`, branch `main`.

## Important files

| File | Purpose |
| --- | --- |
| `backend/app/main.py` | `create_app(settings)` factory and lifespan |
| `backend/app/core/config.py` | `Settings`: secrets are `SecretStr`; production safety validators |
| `backend/app/core/logging.py` | JSON and console formatters; one stdout handler for every logger |
| `backend/app/core/request_context.py` | request-ID contextvar and validation |
| `backend/app/core/middleware.py` | pure-ASGI request-ID, access-log and safe-500 middleware |
| `backend/app/api/deps.py` | `get_app_settings`, `get_database`, `get_db_session` (per-request session, never commits) |
| `backend/app/core/database.py` | `Database`: async engine and pool settings, session factory, `ping()`, `current_revision()` |
| `backend/app/core/migrations.py` | `alembic_config()`, `expected_head_revision()` |
| `backend/app/models/base.py` | `Base` (naming convention), `UUIDPrimaryKeyMixin`, `TimestampMixin` |
| `backend/app/repositories/base.py` | generic `Repository[Model]`, `PageRequest` (max 100), `Page` |
| `backend/migrations/` | Alembic async `env.py` and `versions/` |
| `backend/tests/db/conftest.py` | Testcontainers or `TEST_DATABASE_URL`; fresh and migrated DBs; rollback-per-test session |
| `backend/app/api/v1/router.py` | includes all v1 routers |
| `backend/tests/conftest.py` | `make_settings`, `app` and `client` fixtures (ignore `.env`) |
| `docker-compose.yml` | postgres (pgvector 0.8.6, PG17) :5433, redis 7.4 :6380, api :8000, all bound to 127.0.0.1 |
| `Makefile` | `make help`; `make check` runs everything CI runs for the backend |
| `.github/workflows/ci.yml` | backend job (ruff, mypy, pytest) and docker job (build, up, smoke test, pgvector check) |
| `backend/app/core/errors.py` | `AppError` hierarchy and handlers; envelope `{"error": {code, message, request_id}}` |
| `backend/app/security/passwords.py`, `tokens.py` | Argon2id hashing; HS256 JWT create/decode (algorithm pinned, required claims) |
| `backend/app/domain/roles.py` | `WorkspaceRole` (OWNER > EDITOR > MEMBER > VIEWER), `has_at_least()`, `OrganizationRole` |
| `backend/app/models/identity.py` | User, Organization, OrganizationMember, ComparisonWorkspace, WorkspaceMember |
| `backend/app/repositories/identity.py` | User/Org/Workspace repositories (workspace reads always join via membership) |
| `backend/app/services/auth.py`, `workspaces.py` | register/login; workspace CRUD and `authorize()` (404 for non-members, 403 for low role) |
| `backend/app/api/deps.py` | also `get_current_user`, plus the `CurrentUser`, `SessionDep` and `SettingsDep` aliases |
| `backend/tests/db/conftest.py` | also an `api` client fixture (requests share the rolled-back session) and `register_user()` |
| `backend/app/domain/products.py` | `IdentifierScheme`, GTIN check digit and GTIN-14 normalization, `canonical_product_key`, spec-key rule |
| `backend/app/models/catalog.py` | Product, ProductVariant, ProductIdentifier, ProductSpecification, WorkspaceProduct, `PRODUCT_CATEGORIES` |
| `backend/app/repositories/catalog.py`, `services/catalog.py` | catalog search and lookup; `CatalogService` (creator-only edits); `WorkspaceProductService` (EDITOR+ add/remove) |
| `backend/app/api/v1/endpoints/products.py`, `workspace_products.py` | catalog routes and `/workspaces/{id}/products` routes |
| `backend/app/ingestion/safe_fetch.py` | `SafeFetcher` (SSRF defense, IP pinning, redirect/size/time/type limits), `validate_url_syntax`, `is_public_ip` |
| `backend/app/ingestion/parsers.py` | HTML/PDF/text parsing, `normalize_text`, `sniff_content_type` |
| `backend/app/models/sources.py` | ProductSource, SourceSnapshot (sha256, last_seen_at), SourceDocument; SourceType, SourceAuthority, SourceStatus |
| `backend/app/services/sources.py` | create/list/get, `ingest_url`, `upload`, `_store` (dedupe → parse → document) |
| `backend/tests/support/fake_web.py`, `documents.py` | fake DNS + HTTP (`FakeWeb`) and PDF/HTML builders for tests |
| `docs/DECISIONS.md` | ADR-001 to ADR-021 |
| `docs/DATA_MODEL.md`, `docs/TESTING.md` | schema conventions and test strategy |

## Architecture decisions (details in docs/DECISIONS.md)

1. PostgreSQL + pgvector is the only primary and vector store.
2. The app is built by a factory with an explicit lifespan; nothing connects at import time.
3. uv with a committed lockfile.
4. Standard-library logging with a custom JSON formatter.
5. Pure-ASGI middleware for request IDs and safe 500s. Error body:
   `{"error": {"code", "message", "request_id"}}`.
6. `/health` is liveness only. Readiness (DB check) comes in Phase 2.
7. Pinned infrastructure images; host ports 5433/6380 bound to 127.0.0.1.
8. Strict mypy on app and tests; Ruff with bandit and bugbear rules; pytest treats warnings as errors.
9. Session per request. Repositories never commit; the service layer owns transactions.
10. Migrations ship in the image and run as the Compose `migrate` one-shot before `api` starts
    (the folder is named `migrations/`, not `alembic/`).
11. `/ready` checks DB connectivity and that `alembic_version` equals the code's head revision.
12. DB tests use real PostgreSQL through Testcontainers, never SQLite.
13. 15-minute HS256 JWT access tokens; refresh tokens and revocation come in Phase 22.
14. Non-members get 404 and members with too low a role get 403.
15. One error envelope for every error.
16. Roles are stored as VARCHAR with a CHECK constraint, not native enums.
17. The product catalog is global; workspace data lives on `workspace_products`.
18. Specs are typed: NUMERIC xor text, plus a unit, never floats.
19. A hand-written SSRF-safe fetcher pins the validated IP (Host header and SNI are kept).
20. Snapshots are immutable and content-addressed; the latest one is picked by `last_seen_at`.
21. Upload type comes from magic bytes; uploads always get USER authority.

## Migrations

| Revision | File | Change |
| --- | --- | --- |
| `0001` | `20260930_1700_0001_enable_pgvector.py` | `CREATE EXTENSION IF NOT EXISTS vector` |
| `0002` | `20260930_2105_0002_identity_and_workspaces.py` | users, organizations, organization_members, comparison_workspaces, workspace_members |
| `0003` | `20260930_2120_0003_product_catalog.py` | products, product_variants, product_identifiers, product_specifications, workspace_products |
| `0004` (head) | `20260930_2137_0004_sources_snapshots_documents.py` | product_sources, source_snapshots, source_documents |

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/health` | liveness: `{status, service, version, environment}` |
| POST | `/api/v1/auth/register`, `/api/v1/auth/login` | 201 user / 200 `{access_token, token_type, expires_at}` |
| GET | `/api/v1/auth/me` | current user (Bearer) |
| POST/GET | `/api/v1/workspaces` | create (caller becomes OWNER) / paginated list of your workspaces |
| GET/PATCH | `/api/v1/workspaces/{id}` | members only; PATCH needs OWNER or EDITOR |
| GET | `/api/v1/workspaces/{id}/members` | members only |
| POST/GET | `/api/v1/products` | create / search (`q`, `category`, pagination) |
| GET | `/api/v1/products/by-identifier`, `/api/v1/products/{id}` | lookup / detail |
| POST/PUT | `/api/v1/products/{id}/variants`, `/identifiers`, `/specifications` | creator-only edits |
| POST/GET/DELETE | `/api/v1/workspaces/{id}/products[/{product_id}]` | EDITOR+ modifies, members read |
| POST/GET | `/api/v1/products/{id}/sources` | register (shared or `workspace_id`) / list visible |
| POST | `/api/v1/products/{id}/sources/upload` | multipart upload → snapshot + document |
| POST | `/api/v1/sources/{id}/ingest` | SSRF-safe fetch → snapshot → document (422 unsafe_url, 502 fetch failed) |
| GET | `/api/v1/sources/{id}`, `/api/v1/sources/{id}/document` | status + latest doc meta / latest text |
| GET | `/api/v1/ready` | readiness: `{status: ready\|not_ready, checks: {database, migrations}}`; 503 when not ready |
| GET | `/api/v1/openapi.json`, `/api/v1/docs` | OpenAPI and Swagger UI |

## Tests

- Phase 5: 299 tests, 97% coverage in the cloud workspace. `reportlab` is a dev-only
  dependency used to build test PDFs.
- Phase 4: 218 tests, 97% coverage in the cloud workspace.
- Phase 3: 166 tests, 98% line+branch coverage in the cloud workspace (PostgreSQL 16 through
  `TEST_DATABASE_URL`). Coverage runs with `concurrency = ["greenlet", "thread"]`.
- Phase 2: 88 tests, 97% line+branch coverage in the cloud workspace, which used PostgreSQL 16
  and pgvector 0.6 through `TEST_DATABASE_URL`. They include 27 DB tests (repository, migrations,
  readiness and session tests). On the Mac and in CI they run through Testcontainers.
- Run with `make check`, or `make test-unit` if Docker isn't running.
- Phase 1 (historical): 48 tests.
- Verified on the owner's Mac (Python 3.12.14): ruff, mypy and pytest all pass.
- Verified on the owner's Mac: `make up` brings the stack up healthy, `/api/v1/health` returns 200
  with `x-request-id`, and pgvector 0.8.6 is installed.
- GitHub Actions CI is green (run 36773136185, commit b579343): the backend job and the docker job both pass.

## Benchmark results

None yet.

## Known limitations / open items

- Phase 2 verified on the Mac (Compose `migrate` then `api`, `/ready` returns 200, `alembic current` is
  0001, pgvector 0.8.6) and in CI (run 36775232625, commit 2860cd4, Testcontainers DB tests included).
- Redis is not used by the API yet (Phase 19).
- Phase 3 verified on the Mac (166 tests, 98% coverage; live register, login, me and workspace calls) and in CI (run 36777813805, commit 4de24b4).
- Phase 4 verified on the Mac (218 tests, 97% coverage; live product, spec and workspace-product calls) and in CI (run 36779473241, commit 7be3f05).
- Phase 5 still needs verifying on the Mac and in CI.
- Ingestion runs inside the request (no workers until Phase 21); raw bytes are stored in
  PostgreSQL (S3 comes in Phase 31); robots.txt isn't consulted yet (only user-supplied URLs
  are fetched, never crawled).
- Catalog has no moderation yet (only the creator can edit) and specs have no source links
  (Phase 5 adds sources and evidence).
- No invitations endpoint yet: members are added only through `WorkspaceService.add_member`
  (used by tests). Invitations come with collaboration (Phase 20).
- No refresh tokens, logout, rate limiting or account lockout yet (Phases 19 and 22).
- No auth, rate limiting or security headers yet (Phase 3 and Phase 22). Swagger docs are
  publicly exposed.
- `gpt-4.1-mini` / `text-embedding-3-small` are placeholder defaults set in `.env`.
- Before Phase 31, check which Postgres/pgvector versions AWS RDS supports and whether to use
  ElastiCache Redis OSS or Valkey.
