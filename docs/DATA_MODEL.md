# Data Model

PostgreSQL is the system of record. This document lists every table that exists
**now**, and the conventions all tables follow. Domain tables are added phase by
phase (see [ROADMAP.md](ROADMAP.md)).

## Current schema (migration head: `0001`)

| Object | Created by | Purpose |
| --- | --- | --- |
| extension `vector` | `0001_enable_pgvector` | pgvector type and operators for embeddings (used from Phase 6) |
| table `alembic_version` | Alembic | the migration revision currently applied |

No domain tables yet. The first ones (users, organizations/workspaces,
memberships) arrive in Phase 3.

## Conventions

- **Base class:** every model inherits `app.models.base.Base`, so there is one
  `MetaData` for Alembic autogenerate and the drift test.
- **Primary keys:** `UUIDPrimaryKeyMixin`. The application generates a UUIDv4
  before INSERT. IDs are known before commit (outbox, idempotency) and can't be guessed.
- **Timestamps:** `TimestampMixin`: `created_at` and `updated_at` are `timestamptz`,
  default `now()` on the server, and `updated_at` is refreshed on update.
- **Constraint names** are deterministic:

  | Kind | Pattern |
  | --- | --- |
  | primary key | `pk_<table>` |
  | foreign key | `fk_<table>_<column>_<referred_table>` |
  | unique | `uq_<table>_<columns>` |
  | check | `ck_<table>_<name>` |
  | index | `ix_<column_label>` |

- **Tenant isolation:** every workspace-owned table will carry a non-null
  `workspace_id` foreign key. Repository methods for those tables will require a
  workspace scope, and route handlers never build queries directly (from Phase 3).
- **Money and numbers:** prices will use `NUMERIC` with an explicit currency
  column, never floats (Phase 18).

## Migration workflow

```bash
make migration m="add users table"   # autogenerate from model changes
# review the generated file in backend/migrations/versions/
make migrate                         # apply to the local database
make test-db                         # includes the drift and round-trip tests
```

Every migration must include a working `downgrade()`. The round-trip test runs
downgrade to base and upgrade to head on a fresh database.
