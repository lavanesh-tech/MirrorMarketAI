# MirrorMarket AI: Project State

Read this file first when resuming the project in a new session.

## Status

- **Completed phases:** 1 (repository foundation), 2 (async PostgreSQL, Alembic, readiness,
  repository base, DB tests)
- **Next phase:** 3. Users, organizations/workspaces, memberships, and the authentication
  foundation. This adds the first domain tables and migrations, plus workspace-scoped
  repositories.
- **Last updated:** 2026-09-30

## Working rules (from the owner)

- Build one phase at a time and stop after each one until the owner says "continue".
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
| `docs/DECISIONS.md` | ADR-001 to ADR-012 |
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

## Migrations

| Revision | File | Change |
| --- | --- | --- |
| `0001` (head) | `20260930_1700_0001_enable_pgvector.py` | `CREATE EXTENSION IF NOT EXISTS vector` |

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/health` | liveness: `{status, service, version, environment}` |
| GET | `/api/v1/ready` | readiness: `{status: ready\|not_ready, checks: {database, migrations}}`; 503 when not ready |
| GET | `/api/v1/openapi.json`, `/api/v1/docs` | OpenAPI and Swagger UI |

## Tests

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

- Phase 2 still needs verifying on the Mac and in CI (Testcontainers path, Compose `migrate`
  service, `make ready`).
- Redis is not used by the API yet (Phase 19).
- No domain tables yet (Phase 3).
- No auth, rate limiting or security headers yet (Phase 3 and Phase 22). Swagger docs are
  publicly exposed.
- `gpt-4.1-mini` / `text-embedding-3-small` are placeholder defaults set in `.env`.
- Before Phase 31, check which Postgres/pgvector versions AWS RDS supports and whether to use
  ElastiCache Redis OSS or Valkey.
