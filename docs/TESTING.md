# Testing

## Layout

| Folder | Marker | Needs | What it covers |
| --- | --- | --- | --- |
| `backend/tests/unit/` | `unit` | nothing | settings, logging, request IDs, pagination |
| `backend/tests/api/` | `api` | nothing | HTTP behaviour in-process: health, request IDs, safe 500s, CORS, readiness failure modes |
| `backend/tests/db/` | `db` | PostgreSQL | repository, constraints, transactions, session dependency, readiness, migrations |

## Commands

```bash
make test-unit   # unit + api tests, no Docker needed
make test-db     # DB tests only (starts a pgvector container)
make test        # everything
make check       # ruff + mypy + all tests with coverage (same as CI)
```

## Where the test database comes from

- **Default:** Testcontainers starts `pgvector/pgvector:0.8.6-pg17-trixie` once
  per test session. Docker Desktop must be running.
- **Override:** set
  `TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host:port/postgres` to use
  an existing server. That user needs `CREATE DATABASE`, and pgvector must be
  installed on the server.

## Isolation strategy

- **Migration tests:** each test creates a brand-new database and drops it afterwards.
- **Other DB tests:** they share one database migrated to head. Each test runs
  inside an outer transaction that is rolled back. Sessions use
  `join_transaction_mode="create_savepoint"`, so code under test can call
  `commit()` safely. API tests roll the shared session back at the end of every
  request, as closing a real per-request session does, so a write that a service
  flushed but never committed is gone by the next request and the test fails.
- **Test-only tables:** the repository tests' `test_gadget` table lives on a
  separate `MetaData` and is created inside the rolled-back transaction, so it
  never touches the real schema.

## Migration safety checks (`tests/db/test_migrations.py`)

- The migration history has exactly one head.
- A fresh database upgrades to head, and pgvector is installed.
- A full downgrade to base, followed by re-upgrade, works.
- Running `upgrade head` twice is a no-op.
- **No drift:** Alembic autogenerate finds no difference between the models and
  the migrated schema.
- Offline SQL generation (`alembic upgrade head --sql`) works.

## Rules

- Pytest treats warnings as errors. Two narrowly scoped ignores cover known
  third-party Testcontainers noise.
- External services (OpenAI, etc.) will be mocked in ordinary CI. Live-provider
  evaluations run separately (Phase 27).

## End-to-end (Playwright)

`frontend/e2e/journey.spec.ts` drives Chromium through one complete journey against the
built web app and the real API. Run it with `make up`, then `make web-e2e` (once:
`make web-e2e-install`). CI runs it in the `e2e` job against the Docker Compose stack and
keeps traces and screenshots when it fails. The journey expects the offline rules engines
(the default); with `AGENT_ENGINE=openai` the wording of answers may differ.

## Evaluation

`make eval` runs the end-to-end evaluation in `backend/evaluation` against a throwaway
database and rewrites `backend/evaluation/results/rules-hashing.json` and
`docs/EVALUATION.md`. `tests/db/test_evaluation_run.py` repeats the offline run and fails
if the committed result differs, so the published numbers cannot go stale.
`tests/unit/test_evaluation.py` checks the scoring functions and that every gold label
refers to something in the dataset. OpenAI configurations
(`make eval ENGINE=openai EMBEDDER=openai`) cost money and vary between runs; they are not
part of the test suite.

## Observability checks (Phase 28)

- `tests/api/test_metrics.py`: `/metrics` format, route-template labels, one label for
  unknown paths, token protection, switch-off; every `mm_*` name in the dashboard and the
  alert rules must exist in the code; Prometheus targets must be Compose services.
- `tests/db/test_tracing.py`: spans are collected in memory from a real app and database:
  request span, SQL child spans, failing statement, `traceparent`, sampling, no
  credentials in spans, trace ids on log lines, clean shutdown.
- `make obs-check` (promtool) and `make smoke-observability` need Docker; CI runs both.

