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
| 13 | Value Agent | ✅ |
| 14 | Risk Agent | ✅ |
| 15 | Synthesis Agent, orchestration, bounded execution | ✅ |
| 16 | Comparison engine: criteria, weights, hard constraints | ✅ |
| 17 | Ask MirrorMarket (RAG Q&A) | ✅ |
| 18 | Price snapshots and price history | ✅ |

## Platform

| # | Phase | Status |
| --- | --- | --- |
| 19 | Redis: caching, rate limiting, idempotency, OAuth state | ✅ |
| 20 | WebSockets: presence, comments, votes, realtime updates | ✅ |
| 21 | Kafka: outbox, workers, idempotent consumers, DLQ | ✅ |
| 22 | Security hardening: JWT, refresh, RBAC, SSRF, prompt-injection defences, file security, audit logs | ✅ |
| 23 | OpenAPI, Postman collection, API documentation | ✅ |

## Frontend

| # | Phase | Status |
| --- | --- | --- |
| 24 | Next.js / React / TypeScript foundation | ✅ |
| 25 | Workspace UI: requirements, products, comparison matrix | ✅ |
| 26 | Evidence explorer, agent status, Ask MirrorMarket, price history, realtime collaboration UI | 🔜 |

## Quality, operations, delivery

| # | Phase | Status |
| --- | --- | --- |
| 27 | RAG, agent and citation evaluation | ⬜ |
| 28 | OpenTelemetry, Prometheus, Grafana | ⬜ |
| 29 | Load and performance benchmarks (k6) | ⬜ |
| 30 | Production Docker, CI/CD | ⬜ |
| 31 | Terraform for AWS (code + validate only; nothing is applied) | ⬜ |
| 32 | EKS: dropped (no AWS deployment) | ➖ |
| 33 | Recruiter demo lifecycle on local Docker Compose: start, seed, smoke test, demo, benchmark, destroy, recreate | ⬜ |
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

## Phase 13 exit criteria

- [x] Value Agent: cited evidence price (sale vs list, EU format) with catalog `price` fallback; cents-quantized Decimals
- [x] Budget fit including currency mismatch (no FX guessing); over-budget amount
- [x] Requirement fit from the latest Product Research run at the same requirement version; value index; price per unit
- [x] `POST /workspaces/{id}/products/{pid}/value`
- [x] Price extraction benchmark (`benchmarks/price_extraction.py`)
- [x] Verified on the developer Mac and CI green (run 36802148225, commit b916de0)

## Phase 14 exit criteria

- [x] Risk Agent: warranty length, returns, safety and recalls, reliability, repairability, software support (negation-aware)
- [x] Cross-agent risks from the latest research, compatibility and value runs at the same requirement version
- [x] Severity and level computed in code; cited summary of evidence risks
- [x] `POST /workspaces/{id}/products/{pid}/risk`; Value Agent price query made OR-style (was missing prices)
- [x] Risk detection benchmark (`benchmarks/risk_detection.py`)
- [x] Verified on the developer Mac and CI green (run 36803337625, commit 0702a34)

## Phase 15 exit criteria

- [x] Synthesis Agent: code-computed verdict (hard-constraint gates), score, blockers/concerns/strengths naming their source agent
- [x] Orchestrator with dependency order; compatibility skipped without owned devices
- [x] Bounded execution: time budget (skip, never cut mid-query), token budget (fall back to rules), product cap
- [x] Failure isolation per step (savepoint + FAILED run with exception class only)
- [x] `POST /workspaces/{id}/analyze`, `POST /workspaces/{id}/products/{pid}/synthesize`; ranking test on a synthetic 3-product workspace
- [x] Verified on the developer Mac and CI green (run 36804675797, commit c880290)

## Phase 16 exit criteria

- [x] Comparison matrix from the latest research and value runs (same requirement version), with citations per cell
- [x] Weighted utility scoring (MET + directional min-max), weight overrides, hard constraints (MUST, optional hard budget)
- [x] Winner only among eligible products with verified hard constraints; margin; per-weight sensitivity (×0.5/×2)
- [x] `POST /workspaces/{id}/compare`, stored as a `comparison` run
- [x] Latency benchmark (`benchmarks/comparison_scale.py`); sensitivity optimised with a cached utility matrix
- [x] Verified on the developer Mac and CI green (run 36805594258, commit 343970a)

## Phase 17 exit criteria

- [x] `POST /workspaces/{id}/ask`: retrieval (optional product scope) → evidence pack → grounded answer → stored `ask` run
- [x] Offline extractive engine (IDF term coverage, verbatim cited sentences, abstention threshold)
- [x] LLM engine with `answerable` flag; every sentence citation-validated, unsupported ones dropped; abstain if none remain
- [x] QA benchmark with tuning and held-out sets (`benchmarks/qa_extractive.py`)
- [x] Verified on the developer Mac and CI green (run 36806831760, commit d66ecc2)

## Phase 18 exit criteria

- [x] `price_snapshots` (migration `0010`): NUMERIC amounts, ISO currency, constraints, idempotent unique key
- [x] Batch recording (ON CONFLICT DO NOTHING) and bucketed history (date_trunc in SQL) with statistics
- [x] Value Agent prefers fresh in-stock snapshots in the budget currency (`PRICE_MAX_AGE_DAYS`)
- [x] DB benchmark with/without the history index (`benchmarks/price_history_db.py`); O(n²) stats bug found and fixed
- [x] Verified on the developer Mac and CI green (run 36808476931, commit 0069f7a)

## Phase 19 exit criteria

- [x] Redis client in the lifespan (`REDIS_URL`, empty = disabled), short timeouts, every feature fails open
- [x] GCRA rate limiting in one Lua script (Redis clock): auth per IP and per email, agent runs per user, 429 + Retry-After
- [x] Price-history read-through cache with version-key invalidation on new snapshots (`X-Cache: HIT|MISS`)
- [x] `Idempotency-Key` middleware (POST/PATCH): per-caller scope, in-progress 409, body-mismatch 422, 5xx not stored
- [x] Single-use tokens (GETDEL) + PKCE helper for OAuth state; `/ready` reports Redis as non-required
- [x] Benchmark of cache hit vs Postgres and rate-limit decision latency (`benchmarks/redis_paths.py`)
- [x] Verified on the developer Mac and CI green (run 36811623348, commit 7247b7f)

## Phase 20 exit criteria

- [x] `workspace_comments` and `product_votes` (migration `0011`); comments with one-level replies, edit, soft delete; vote upsert and tallies
- [x] WebSocket `/ws/workspaces/{id}`: first-message JWT auth, membership check, origin allow-list, closes on token expiry
- [x] Limits: connections per user, message size and rate, idle timeout, bounded send queue (slow consumer closed with 1013)
- [x] Cross-replica fan-out through Redis pub/sub with local fallback; presence in a Redis sorted set with TTL
- [x] Events for comments, votes, agent runs and presence; tenant isolation tested; two-replica test
- [x] Fan-out benchmark (`benchmarks/realtime_fanout.py`); protocol documented in `docs/REALTIME.md`
- [x] Verified on the developer Mac and CI green (run 36934271121, commit 02710f1)

## Phase 21 exit criteria

- [x] Transactional outbox (migration `0012`): events written in the same transaction as comments, votes, agent runs and prices
- [x] Relay: FOR UPDATE SKIP LOCKED, publish in order, backoff, give up after max attempts, retention purge
- [x] Kafka adapters (aiokafka): idempotent producer with acks=all, manual offset commits, explicit topic creation
- [x] Idempotent consumer (`processed_events` inbox), retries, dead-letter topic with error headers, rewind on failure
- [x] Activity feed read model and `GET /workspaces/{id}/activity`; Kafka + event-worker in Compose
- [x] Real-broker tests (Testcontainers Kafka), CI smoke test through Kafka, throughput benchmark (`benchmarks/event_pipeline.py`)
- [x] Verified on the developer Mac and CI green (run 36938010067, commit 88e3893)

## Phase 22 exit criteria

- [x] Refresh tokens (migration `0013`): hashed at rest, rotated on every use, re-use revokes the session, absolute session cap
- [x] Logout, logout-all and change-password; `token_version` invalidates access tokens at once; JWT key rotation with a previous key
- [x] Append-only audit trail (database trigger) for auth, workspace, moderation and upload events; owner-only workspace view
- [x] Prompt-injection layers: neutralised evidence, sentence-level withholding, benchmark with a held-out set
- [x] Upload hardening (executables, archives, active PDFs, filename sanitising, text cap); security headers; request body limit
- [x] Authorization matrix test generated from the OpenAPI schema (auth required everywhere, no workspace leaks)
- [x] Verified on the developer Mac and CI green (run 36942149562, commit fe33016)

## Phase 23 exit criteria

- [x] OpenAPI 3.1 document finished in code: stable operation ids, one error schema everywhere, common errors, `Idempotency-Key` and `X-Request-ID`, tag descriptions; validated by a spec validator
- [x] Generated and committed: `docs/api/openapi.json`, `ENDPOINTS.md`, Postman collection and environment (`make api-docs`)
- [x] Drift test: committed artefacts must equal what the code generates
- [x] Postman example bodies validate against the request schemas, and the collection's journey runs against the real API
- [x] API guide (`docs/API.md`): quickstart, conventions, error table, versioning policy; `DOCS_ENABLED` switch
- [x] Verified on the developer Mac and CI green (run 36943664765, commit b26c9df)

## Phase 24 exit criteria

- [x] Next.js (App Router) + React + TypeScript strict in `frontend/`, Tailwind, ESLint, Prettier, locked dependencies
- [x] Backend-for-frontend: tokens only in HttpOnly cookies, `/api/v1` forwarder with one silent refresh and retry, same-origin check, token endpoints unreachable from the browser
- [x] Typed API client generated from `docs/api/openapi.json`, with a drift check
- [x] Pages: log in, create account, workspaces (list + create with idempotency key), workspace overview; route protection in `proxy.ts`
- [x] Vitest + Testing Library tests; real-browser journey run against the live API
- [x] CI job for the frontend; backend refresh leeway so parallel refreshes do not end a session
- [x] Verified on the developer Mac and CI green (run 36958852957, commit 4857d96)

## Phase 25 exit criteria

- [x] Workspace sections with shared navigation: Overview, Requirements, Products, Compare
- [x] Requirements: free-text brief, extraction preview with "not understood" clauses, editable draft (must-have / nice-to-have, importance, remove), versioned save with conflict handling, version history
- [x] Products: catalog search, add and remove, create a catalog product, specification editor for the product's creator
- [x] Compare: research + compare, rescore with changed importance and budget rule, matrix with scores, ruled-out reasons, cited values highlighted, sensitivity note, stale-requirements notice
- [x] Role rules in the UI match the API (OWNER/EDITOR edit, MEMBER runs, VIEWER reads)
- [x] Fix: requirements, evidence packs and agent runs are now committed; test requests discard uncommitted work so a missing commit fails the suite
- [x] Vitest tests for the new screens; real-browser journey run against the live API
- [x] Verified on the developer Mac and CI green (run 37024991448, commit a0b4178)

## Phase 26 exit criteria

- [x] Evidence: add sources per product (pasted text, file upload, web address), ingested and embedded in one action; search the workspace's passages
- [x] Agent status: every step of the last research run per product, with engine, duration, skip or failure reason and the verdict
- [x] Ask: grounded answer with citation markers linked to the passages cited; clear abstention when the sources do not answer
- [x] Price history: statistics, chart and a form to record a price
- [x] Realtime: live presence, comments, votes and comparison refresh over the WebSocket; activity feed; works without the socket
- [x] Browser WebSocket authentication with a 30-second, workspace-bound ticket (tokens stay in HttpOnly cookies)
- [x] Playwright end-to-end journey in the repo and as a CI job against the Docker stack
- [ ] Verified on the developer Mac and CI green
