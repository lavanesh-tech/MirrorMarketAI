# Data Model

PostgreSQL is the system of record. This document lists every table that exists
**now**, and the conventions all tables follow. Domain tables are added phase by
phase (see [ROADMAP.md](ROADMAP.md)).

## Current schema (migration head: `0010`)

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
| `product_sources` | `0004` | evidence source for a product: `source_type`, `authority` (OFFICIAL/THIRD_PARTY/USER), `title`, `url`, `status` (PENDING/INGESTED/FAILED), `last_error`; `workspace_id` NULL = shared |
| `source_snapshots` | `0004` | immutable bytes per fetch/upload: `sha256` (unique per source), `content_type`, `byte_size`, `raw_content` BYTEA, `final_url`, `http_status`, `fetched_at`, `last_seen_at` |
| `source_documents` | `0004` | normalized text per snapshot (1:1) with denormalized `source_id`, `product_id`, `workspace_id` for filtered retrieval |
| `document_chunks` | `0005` | chunk text + `char_start`/`char_end` into the document, `token_estimate`, `content_hash`, `search_vector` (generated `to_tsvector('english', text)`, GIN index, `0006`); denormalized `source_id`, `product_id`, `workspace_id`; unique (document, chunk_index) |
| `chunk_embeddings` | `0005` | `embedding vector(1536)` per (chunk, `model`) — unique; HNSW cosine index |
| `embedding_jobs` | `0005` | per-document job: `status` PENDING/RUNNING/SUCCEEDED/FAILED, `attempts`, counts, `tokens_used`, `last_error`; partial index on PENDING |
| `purchase_requirements` | `0007` | one per workspace (unique `workspace_id`, CASCADE); `current_version` is the optimistic-lock counter |
| `requirement_versions` | `0007` | immutable history: `version` (unique per requirement, ≥1), `raw_text`, JSONB `spec` (RequirementSpec), JSONB `unparsed`, `extractor`, `change_note`, `created_by_id` |
| `evidence_packs` | `0008` | frozen search result: `workspace_id` (CASCADE), `query`, `mode`, `embedding_model`, `requirement_version`, `degraded`, `created_by_id` |
| `evidence_items` | `0008` | citable snapshot E{position}: unique (pack, position); chunk text, offsets, `content_hash`, source title/url/authority/type, score; `chunk_id`/`source_id` SET NULL on delete |
| `agent_runs` | `0009` | one row per agent execution: `agent`, `product_id`, `evidence_pack_id` (SET NULL), `requirement_version`, `status`, `engine`, `degraded`, JSONB `output` and `validation`, `duration_ms`, `tokens_used` |
| `price_snapshots` | `0010` | global price observations: product (CASCADE), optional variant, `retailer`, `amount` NUMERIC(12,2) > 0, `currency` ^[A-Z]{3}$, `observed_at`, `in_stock`, `url`, `source` MANUAL/IMPORT/EVIDENCE; unique (product, retailer, currency, observed_at); index (product, currency, observed_at) |
| `workspace_comments` | `0011` | workspace (CASCADE), optional product (NULL = workspace-level), author (RESTRICT), `parent_id` self FK (replies one level deep, enforced in the service), `body` CHECK 1-4000 chars, `edited_at`, `deleted_at` (soft delete, body overwritten); index (workspace, product, created_at) |
| `product_votes` | `0011` | one row per (workspace, product, user) UNIQUE; `value` CHECK IN (-1, 1); upsert with ON CONFLICT DO UPDATE, value 0 deletes the row |
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
