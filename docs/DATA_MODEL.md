# Data Model

PostgreSQL is the system of record. This document lists every table that exists
**now**, and the conventions all tables follow. Domain tables are added phase by
phase (see [ROADMAP.md](ROADMAP.md)).

## Current schema (migration head: `0003`)

| Object | Migration | Purpose |
| --- | --- | --- |
| extension `vector` | `0001` | pgvector type and operators for embeddings (used from Phase 6) |
| `users` | `0002` | accounts: `email` (unique, CHECK lower-case), `password_hash` (Argon2id), `display_name`, `is_active` |
| `organizations` | `0002` | tenants. `is_personal` marks the org every user gets at registration; `created_by_id → users` |
| `organization_members` | `0002` | user ↔ org with `role` ∈ OWNER/ADMIN/MEMBER; unique (org, user) |
| `comparison_workspaces` | `0002` | purchase-research workspaces: `organization_id` (CASCADE), `created_by_id`, `name`, `description` |
| `workspace_members` | `0002` | user ↔ workspace with `role` ∈ OWNER/EDITOR/MEMBER/VIEWER; unique (workspace, user) |
| `products` | `0003` | global catalog: `brand`, `name`, `category` (CHECK list), `description`, unique `canonical_key`, `created_by_id` |
| `product_variants` | `0003` | configurations (e.g. "16 GB / 512 GB"), JSONB `attributes`; unique (product, name) |
| `product_identifiers` | `0003` | `scheme` ∈ GTIN/MPN/ASIN/SKU, normalized `value`; unique (scheme, value) catalog-wide; optional `variant_id` |
| `product_specifications` | `0003` | `key` (snake_case), `value_number` NUMERIC(18,6) XOR `value_text`, `unit`; unique (product, variant, key) NULLS NOT DISTINCT |
| `workspace_products` | `0003` | tenant link: workspace ↔ product (+ optional variant, notes, `added_by_id`); unique (workspace, product); product FK RESTRICT |
| `alembic_version` | Alembic | the migration revision currently applied |

Foreign keys are indexed. Deleting a workspace cascades to its members.
Deleting a user is RESTRICTed while they're recorded as the creator of an
organization or workspace.

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
