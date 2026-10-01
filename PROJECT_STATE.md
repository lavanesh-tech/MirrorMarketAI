# MirrorMarket AI: Project State

Source of truth for progress. Paste this into a new conversation to resume. Details live in
`docs/` (DECISIONS, DATA_MODEL, ROADMAP, TESTING).

## Current phase

- Completed: 1-10.
- Next: **11, the Review Intelligence Agent.**
- Last verified: Phase 10, CI run 36798687173, commit 7c7545c (2026-10-01).

## Working rules

- Do one phase at a time, then stop until the owner says "next".
- Replies are compact: files, verification, metrics, run commands, commit message, next phase.
- Deliver work as a downloadable `phaseN.tgz` plus one copy-paste zsh block (no inline `#`, use `git --no-pager`).
- The owner commits and pushes. Never change git identity or add AI attribution.
- Never commit secrets; only `.env.example` is tracked. Never invent metrics.
- The full teaching and interview walkthrough happens only after Phase 35.

## Environment

- macOS on Apple Silicon, zsh, Docker Desktop. Python 3.12 with uv (`uv.lock` committed).
- Repo: `~/Desktop/MirrorMarketAI` → `github.com/lavanesh-tech/MirrorMarketAI` (main).
- Compose: postgres (PG17 + pgvector 0.8.6) :5433, redis :6380, migrate, api :8000, embedding-worker.

## Architecture decisions (ADR-001 to ADR-027)

- FastAPI app factory with lifespan; async SQLAlchemy 2 + asyncpg; Alembic in `backend/migrations`.
- JSON logs, request IDs, error envelope `{"error":{code,message,request_id}}`.
- Argon2id + HS256 JWT. RBAC OWNER>EDITOR>MEMBER>VIEWER; non-member 404, low role 403.
- Catalog with canonical keys, GTIN-14, typed specs (snake_case keys with units).
- SSRF-safe fetcher; content-addressed snapshots; workspace-private or shared sources.
- Chunking with exact offsets; embeddings are vector(1536) with an HNSW cosine index, one row per (chunk, model).
- DB job queue (SKIP LOCKED) and an embedding worker with a heartbeat healthcheck.
- Hybrid search: tsvector+GIN and pgvector, RRF k=60, access filters in SQL, latest document only, degrades to lexical.
- Requirements: immutable versions, optimistic locking (`expected_version`, 409), idempotent re-save.
- Extractors: offline rules by default; OpenAI strict JSON schema when configured; falls back to rules (`degraded`).
- Evidence packs freeze search hits as snapshot items E1..En. A deterministic validator checks markers, uncited sentences, numbers and quotes.
- Agents: workspace product + current requirement version → one hybrid search per criterion (product-filtered) → evidence pack → values (rules extractor, or LLM with validated citations) → MET/UNMET/UNKNOWN computed in code → `agent_runs` row. Shared `OpenAIChatClient` (strict JSON schema, retries); LLM failure → rules + `degraded`.
- Offline by default: `EMBEDDING_PROVIDER=hashing`, `REQUIREMENTS_EXTRACTOR=rules`.

## Database migrations (head 0009)

0001 pgvector · 0002 users/orgs/workspaces/members · 0003 catalog + workspace_products ·
0004 sources/snapshots/documents · 0005 chunks/embeddings/jobs · 0006 chunk tsvector + GIN ·
0007 purchase_requirements/requirement_versions · 0008 evidence_packs/evidence_items · 0009 agent_runs

## Major endpoints (/api/v1)

- health, ready; auth register/login/me
- workspaces CRUD + members; workspaces/{id}/products
- products (search, by-identifier, variants, identifiers, specifications)
- products/{id}/sources (+upload); sources/{id} ingest/document/embed/chunks; embedding-jobs/{id}
- POST workspaces/{id}/search (hybrid|lexical|vector + filters)
- workspaces/{id}/requirements: POST extract, PUT save, GET current, versions[/{n}], diff?from=&to=
- workspaces/{id}/evidence-packs: POST create (MEMBER+), GET list/get, POST {pack}/validate
- POST workspaces/{id}/products/{pid}/research (MEMBER+); GET workspaces/{id}/agent-runs[/{run}] (?agent=&product_id=)

## Tests

- 430 tests, 97% coverage (Phase 10; Mac + CI). `make check` runs everything CI runs.
- DB tests use Testcontainers on the Mac and in CI, or `TEST_DATABASE_URL` in the cloud workspace.

## Current measured metrics

- Retrieval smoke benchmark (synthetic: 8 docs, 8 labelled queries, hashing embedder), Recall@3 / MRR:
  lexical 0.375 / 0.375, vector 0.875 / 0.823, hybrid 0.875 / 0.823. Source: `tests/db/test_search.py`.
- Citation validator (synthetic, author-labelled, 24 answers): case accuracy 1.0, issue precision 1.0,
  recall 1.0 (16/16). Evidence: `backend/benchmarks/results/citations.json`. This shows the rules work, not real-world accuracy.
- Fact extraction, rules engine (synthetic, 24 labelled spec sentences): accuracy 0.7917 → 0.875 after allowing
  hyphenated units ("14.2-inch"). The 3 misses are spelled-out numbers. Evidence: `backend/benchmarks/results/fact_extraction.json`.

## Known issues / limits

- The hashing embedder is lexical, not semantic (OpenAI needs a key). Chunk size is measured in characters.
- The rule extractor is English-only pattern matching; what it can't use is returned as `unparsed`.
- Ingestion runs in the request; raw bytes are stored in Postgres (S3 comes in Phase 31).
- Not yet: refresh tokens, rate limiting, invitations, security headers (Phases 19, 20, 22).
- Before Phase 30: pin the CI runner (ubuntu-latest moves to 26 on 2026-10-19).
- New vector migrations need `from pgvector.sqlalchemy import Vector` added by hand.

## Important commands

```bash
cd backend && uv run python -m benchmarks.citations && uv run python -m benchmarks.fact_extraction && cd ..
make check
make up
make ps
make down
make migration m="msg"
make migrate
```
