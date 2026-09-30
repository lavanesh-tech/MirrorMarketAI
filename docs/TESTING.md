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
  `commit()` safely.
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
