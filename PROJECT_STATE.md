# MirrorMarket AI: Project State

Read this file first when resuming the project in a new session.

## Status

- **Completed phase:** 1 (repository foundation)
- **Next phase:** 2. Async PostgreSQL (SQLAlchemy 2.x + asyncpg), Alembic (first migration runs
  `CREATE EXTENSION IF NOT EXISTS vector`), `GET /api/v1/ready` readiness endpoint that checks
  the DB, repository base class, and database tests using Testcontainers.
- **Last updated:** 2026-09-30

## Working rules (from the owner)

- Build one phase at a time and stop after each one until the owner says "continue".
- The owner reviews, commits and pushes all work. Never change git identity and never add AI
  attribution (no Co-authored-by, no Generated-by).
- Deliver complete files plus copy-paste macOS commands. Keep explanations short and don't
  regenerate files that haven't changed.
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
| `backend/app/api/deps.py` | `get_app_settings` dependency (reads `app.state.settings`) |
| `backend/app/api/v1/router.py` | includes all v1 routers |
| `backend/tests/conftest.py` | `make_settings`, `app` and `client` fixtures (ignore `.env`) |
| `docker-compose.yml` | postgres (pgvector 0.8.6, PG17) :5433, redis 7.4 :6380, api :8000, all bound to 127.0.0.1 |
| `Makefile` | `make help`; `make check` runs everything CI runs for the backend |
| `.github/workflows/ci.yml` | backend job (ruff, mypy, pytest) and docker job (build, up, smoke test, pgvector check) |
| `docs/DECISIONS.md` | ADR-001 to ADR-008 |

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

## Migrations

None yet (Alembic arrives in Phase 2). The local DB gets pgvector from
`infrastructure/docker/postgres/initdb/01-extensions.sql`.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/v1/health` | liveness: `{status, service, version, environment}` |
| GET | `/api/v1/openapi.json`, `/api/v1/docs` | OpenAPI and Swagger UI |

## Tests

- 48 tests (unit and in-process API), 97% line+branch coverage. Run with `make check`.
- Verified on the owner's Mac (Python 3.12.14): ruff, mypy and pytest all pass.
- Verified on the owner's Mac: `make up` brings the stack up healthy, `/api/v1/health` returns 200
  with `x-request-id`, and pgvector 0.8.6 is installed.
- GitHub Actions CI is green (run 36773136185, commit b579343): the backend job and the docker job both pass.

## Benchmark results

None yet.

## Known limitations / open items

- The API does not connect to Postgres or Redis yet (Phase 2 and Phase 19).
- No auth, rate limiting or security headers yet (Phase 3 and Phase 22). Swagger docs are
  publicly exposed.
- `gpt-4.1-mini` / `text-embedding-3-small` are placeholder defaults set in `.env`.
- Before Phase 31, check which Postgres/pgvector versions AWS RDS supports and whether to use
  ElastiCache Redis OSS or Valkey.
