# Roadmap

MirrorMarket AI is built one phase at a time. Each phase ends with lint, type
checking, tests, updated documentation and a reviewed commit before the next
one starts. Working architecture is preserved across phases rather than rebuilt.

Legend: ✅ done · 🔜 next · ⬜ planned

## Foundation

| # | Phase | Status |
| --- | --- | --- |
| 1 | Repository foundation: FastAPI, settings, logging, request IDs, health, Docker, Compose (Postgres+pgvector, Redis), Ruff, mypy, pytest, Makefile, CI skeleton | ✅ |
| 2 | Async PostgreSQL, SQLAlchemy 2.x, Alembic, readiness endpoint, repository base, database tests (Testcontainers) | ✅ |
| 3 | Users, organizations/workspaces, memberships, authentication foundation | ✅ |

## Product data and RAG

| # | Phase | Status |
| --- | --- | --- |
| 4 | Products, variants, identifiers, specifications | ✅ |
| 5 | Product sources, snapshots, document ingestion, uploads, safe URL ingestion | ✅ |
| 6 | Chunking, OpenAI embeddings, pgvector, embedding jobs | ✅ |
| 7 | Postgres full-text, vector search, hybrid retrieval, metadata filters, retrieval tests | ✅ |
| 8 | Purchase requirements, structured extraction, requirement versions | ✅ |
| 9 | Evidence packs, citations, citation validator | ✅ |

## Agents

| # | Phase | Status |
| --- | --- | --- |
| 10 | Product Research Agent | ✅ |
| 11 | Review Intelligence Agent | ✅ |
| 12 | Compatibility Agent | ✅ |
| 13 | Value Agent | 🔜 |
| 14 | Risk Agent | ⬜ |
| 15 | Synthesis Agent, orchestration, bounded execution | ⬜ |
| 16 | Comparison engine: criteria, weights, hard constraints | ⬜ |
| 17 | Ask MirrorMarket (RAG Q&A) | ⬜ |
| 18 | Price snapshots and price history | ⬜ |

## Platform

| # | Phase | Status |
| --- | --- | --- |
| 19 | Redis: caching, rate limiting, idempotency, OAuth state | ⬜ |
| 20 | WebSockets: presence, comments, votes, realtime updates | ⬜ |
| 21 | Kafka: outbox, workers, idempotent consumers, DLQ | ⬜ |
| 22 | Security hardening: JWT, refresh, RBAC, SSRF, prompt-injection defences, file security, audit logs | ⬜ |
| 23 | OpenAPI, Postman collection, API documentation | ⬜ |

## Frontend

| # | Phase | Status |
| --- | --- | --- |
| 24 | Next.js / React / TypeScript foundation | ⬜ |
| 25 | Workspace UI: requirements, products, comparison matrix | ⬜ |
| 26 | Evidence explorer, agent status, Ask MirrorMarket, price history, realtime collaboration UI | ⬜ |

## Quality, operations, delivery

| # | Phase | Status |
| --- | --- | --- |
| 27 | RAG, agent and citation evaluation | ⬜ |
| 28 | OpenTelemetry, Prometheus, Grafana | ⬜ |
| 29 | Load and performance benchmarks (k6) | ⬜ |
| 30 | Production Docker, CI/CD | ⬜ |
| 31 | Terraform + AWS core deployment | ⬜ |
| 32 | EKS (only if justified) | ⬜ |
| 33 | Recruiter demo lifecycle: deploy, seed, smoke test, demo, benchmark, destroy, recreate | ⬜ |
| 34 | README, diagrams, screenshots, demo, benchmark report, resume evidence | ⬜ |
| 35 | Full project walkthrough for interviews | ⬜ |

## Phase 1 exit criteria

- [x] `GET /api/v1/health` returns 200 with service, version, environment
- [x] `X-Request-ID` on every response; structured JSON logs carry it
- [x] Unhandled errors return a generic JSON 500 without internal details
- [x] Settings validated at startup; secrets masked
- [x] Ruff, strict mypy and pytest pass
- [x] Docker Compose config valid; Postgres (pgvector) and Redis have health checks
- [x] `make up` verified on the developer Mac (image build + healthy stack)
- [x] CI green on GitHub after first push

## Phase 2 exit criteria

- [x] Async SQLAlchemy engine/session lifecycle is owned by the app lifespan
- [x] Alembic (async env) with baseline migration `0001` enabling pgvector
- [x] Compose `migrate` one-shot runs before `api`
- [x] `GET /api/v1/ready` checks DB connectivity and schema revision (503 otherwise)
- [x] Generic repository + pagination; `Base` naming convention, UUID and timestamp mixins
- [x] DB tests: repository, constraints, transactions, readiness, session dependency
- [x] Migration tests: fresh upgrade, downgrade/re-upgrade round trip, no model drift, single head
- [x] Verified on the developer Mac (`make up`, `make ready`, migrations, pgvector)
- [x] CI green (run 36775232625, commit 2860cd4)

## Phase 3 exit criteria

- [x] Tables: users, organizations, organization_members, comparison_workspaces, workspace_members (migration `0002`)
- [x] Register (Argon2id, personal org), login (JWT access token), `/auth/me`
- [x] Workspaces: create, paginated list (member-only), get, PATCH (OWNER/EDITOR), members
- [x] 404 for non-members, 403 for insufficient role; standard error envelope
- [x] Tests: tokens (expiry, alg=none, wrong key/aud/iss), passwords, role matrix, auth API, workspace isolation/RBAC, constraints
- [x] Verified on the developer Mac and CI green (run 36777813805, commit 4de24b4)

## Phase 4 exit criteria

- [x] Tables: products, product_variants, product_identifiers, product_specifications, workspace_products (migration `0003`)
- [x] Catalog API: create, search (text/category, paginated), get, lookup by identifier, variants, identifiers, spec upsert
- [x] Deterministic identity: canonical key, GTIN check digit + GTIN-14 normalization, global identifier uniqueness
- [x] Workspace products: add/list/remove with role enforcement and tenant isolation
- [x] Tests: identifier/key unit tests, catalog API, workspace-product RBAC/isolation, constraint tests
- [x] Verified on the developer Mac and CI green (run 36779473241, commit 7be3f05)

## Phase 5 exit criteria

- [x] Tables: product_sources, source_snapshots, source_documents (migration `0004`)
- [x] SSRF-safe URL fetcher (scheme/port/host/IP rules, DNS pinning, redirect re-validation, size/time/type limits)
- [x] Uploads: PDF/HTML/Markdown/text with magic-byte sniffing and size caps
- [x] Parsing + normalization (scripts/styles removed, control and zero-width chars stripped)
- [x] Content-addressed snapshots with change detection
- [x] Shared vs workspace-private sources with access control
- [x] Tests: SSRF matrix, fake-DNS/HTTP fetcher tests, parsers, source API (ingest, reuse, uploads, isolation)
- [x] Verified on the developer Mac and CI green (run 36782106155, commit 88c25ef)

## Phase 6 exit criteria

- [x] Sentence-aware chunker with exact offsets and overlap (unit-tested)
- [x] Embedding providers: OpenAI (retries/timeouts/dimension checks, mocked in tests) and offline hashing
- [x] Tables: document_chunks, chunk_embeddings (vector(1536) + HNSW), embedding_jobs (migration `0005`)
- [x] Jobs enqueued atomically with documents; worker with SKIP LOCKED, retries, max attempts
- [x] API: embed now, list chunks, job status; Compose `embedding-worker` service
- [x] Tests: chunking, providers, embed API, idempotency, cosine nearest-neighbour, retry/fail, parallel workers
- [x] Worker heartbeat healthcheck (Compose `--wait` requires it)
- [x] Verified on the developer Mac and CI green (run 36785705853, commit bcab052)

## Phase 7 exit criteria

- [x] `document_chunks.search_vector`: generated tsvector + GIN index (migration `0006`)
- [x] Full-text ranker (`websearch_to_tsquery`, `ts_rank_cd`) and pgvector cosine ranker with HNSW iterative scans
- [x] Reciprocal Rank Fusion (k=60); hybrid degrades to full-text when embedding fails
- [x] `POST /api/v1/workspaces/{id}/search`: modes, product/source/authority/type filters, members only
- [x] Only each source's latest document is searchable
- [x] Tests: RRF and metrics units, modes, filters, cross-workspace leakage, re-ingest, outage, Recall@3/MRR
- [x] Verified on the developer Mac and CI green (run 36787055813, commit 05b6dae)

## Phase 8 exit criteria

- [x] Tables: purchase_requirements, requirement_versions (migration `0007`)
- [x] `RequirementSpec`: category, budget (Decimal), MUST/SHOULD criteria with operators and weights, excluded brands, use cases
- [x] Offline rule extractor (units, conversions, ranges, negation, priorities) and OpenAI strict-schema extractor with retries
- [x] Preview, versioned save with optimistic locking, idempotent re-save, history, diff
- [x] LLM failure degrades to rules (`degraded: true`)
- [x] Tests: domain validation, extractor patterns, mocked OpenAI, API versioning/locking/RBAC, real concurrent saves
- [x] Verified on the developer Mac and CI green (run 36788330694, commit 9ac5155)

## Phase 9 exit criteria

- [x] Tables: evidence_packs, evidence_items (migration `0008`); items are text snapshots (FKs SET NULL)
- [x] Create pack from hybrid search (pins current requirement version); get, list; MEMBER+ creates
- [x] Citation validator: unknown markers, uncited sentences, unsupported numbers and quotes
- [x] Labelled synthetic benchmark (`benchmarks/citations.py`) with committed result JSON
- [x] Verified on the developer Mac and CI green (run 36789472754, commit 93964b3)

## Phase 10 exit criteria

- [x] Shared `OpenAIChatClient` (strict JSON schema, retries); the requirement extractor refactored onto it
- [x] Product Research Agent: per-criterion product-filtered retrieval, evidence pack, value extraction, catalog fallback
- [x] MET/UNMET/UNKNOWN/NOT_COMPARABLE computed in code; LLM values need valid citations; summary citation-validated
- [x] `agent_runs` table (migration `0009`); run, list, get endpoints; LLM failure degrades to rules
- [x] Fact-extraction benchmark (`benchmarks/fact_extraction.py`) with committed results
- [x] Verified on the developer Mac and CI green (run 36798687173, commit 7c7545c)

## Phase 11 exit criteria

- [x] Review Intelligence Agent over all visible REVIEW chunks of a workspace product (`scoped_chunks`)
- [x] Clause-level aspect sentiment with negation (rules) or LLM labels that must quote their item verbatim
- [x] Aggregates in code: per-aspect counts, POSITIVE/NEGATIVE/MIXED, praises, complaints, overall score; cited summary
- [x] `POST /workspaces/{id}/products/{pid}/reviews/analyze`; LLM failure degrades to rules
- [x] Review sentiment benchmark (`benchmarks/review_sentiment.py`) with committed results
- [x] Verified on the developer Mac and CI green (run 36799656902, commit cb3a9a9)

## Phase 12 exit criteria

- [x] `RequirementSpec.owned_devices` (rules + LLM extraction: "I have…", "works with my…")
- [x] Compatibility Agent: devices → capabilities in code; per-capability product search; clause-scoped negation; catalog fallback
- [x] Verdict computed in code; LLM judgements need verbatim quotes; LLM failure degrades to rules
- [x] `POST /workspaces/{id}/products/{pid}/compatibility` (body overrides requirements; 422 when nothing to check)
- [x] Benchmark with tuning and held-out sets (`benchmarks/compatibility.py`)
- [x] Verified on the developer Mac and CI green (run 36801095777, commit 2c7e123)
