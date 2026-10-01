# MirrorMarket AI: Project State

Source of truth for progress. Paste this into a new conversation to resume. Details live in
`docs/` (DECISIONS, DATA_MODEL, ROADMAP, TESTING).

## Current phase

- Completed: 1-20. Phase 21 (Kafka) is built; waiting for Mac + CI.
- Next: **22, Security hardening (refresh tokens, audit logs, prompt-injection and file defences).**
- Last verified: Phase 20, CI run 36934271121, commit 02710f1 (2026-10-01).
- Scope (owner decision 2026-10-01): no AWS deployment. Phase 31 is Terraform code + validate only, Phase 32 (EKS) is dropped, Phase 33 runs on local Docker Compose.

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
- Compose: postgres (PG17 + pgvector 0.8.6) :5433, redis :6380, kafka (apache/kafka 4.0.0, KRaft) :9094, migrate, api :8000, embedding-worker, event-worker.

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
- Review agent: all visible REVIEW chunks of the product (`scoped_chunks`) → evidence pack (mode `scope`) → aspect/polarity per clause (lexicon + negation, or LLM labels that must quote their item verbatim) → counts, MIXED/POSITIVE/NEGATIVE, praises, complaints (≥2 negative mentions) computed in code; the summary has no digits, so the citation check passes.
- Compatibility agent: owned devices (requirements `owned_devices`, or the request body) → capabilities mapped in code (USB-C, HDMI, iOS, ...; headphones also need Bluetooth/AAC) → product-filtered search per capability → SUPPORTED/NOT_SUPPORTED (negation scoped to the clause) or the catalog has_* fallback → verdict COMPATIBLE/INCOMPATIBLE/UNCERTAIN computed in code.
- Value agent (rules only by design, no LLM): price from product-filtered evidence (sale over "was" price, EU thousands format; cents-quantized Decimal) or catalog `price` spec → budget fit (WITHIN/OVER/UNDER_MIN/NO_BUDGET/UNKNOWN_PRICE/CURRENCY_MISMATCH, no FX) → requirement fit from the latest research run at the same requirement version (MUST weight 5) → value index = fit / (price / budget max), plus price per unit.
- Risk agent (rules only): an OR-style product search per category (warranty, returns, safety, reliability, repairability, support) → sentence patterns with negation → risks merged per (category, title); reliability is HIGH when 2+ items report it. It also adds risks from the latest research (MUST UNMET → HIGH, unverified → MEDIUM), compatibility (NOT_SUPPORTED → HIGH) and value (OVER → MEDIUM) runs at the same requirement version. Level = max severity; only cited evidence risks go into the summary.
- Synthesis (rules): the latest runs at the same requirement version produce a verdict. NOT_RECOMMENDED if a MUST is UNMET or the product is INCOMPATIBLE; CONSIDER if a MUST is unverified, it is OVER budget, or there is a HIGH evidence risk; RECOMMENDED otherwise; INSUFFICIENT_DATA if there is no research. Score = 60·fit + 20·reviews + 20·budget − 3·risk score (max 30). Ranking is by verdict, then score.
- Orchestrator (`POST /workspaces/{id}/analyze`): research → reviews → compatibility (only with owned devices) → value → risk → synthesis per product, run sequentially. Each step runs in its own savepoint; a failure is recorded as a FAILED run (error = exception class) and the pipeline carries on. Budgets: time (remaining steps SKIPPED, never cut mid-query), tokens (switches to rules), max products. The result is stored as an `orchestration` agent run.
- Comparison engine (`POST /workspaces/{id}/compare`): a matrix built from the latest research and value runs. Utility = 0.7·MET + 0.3·min-max position in the criterion's direction (unknown → 0). Score = 100·Σw·u/Σw (MUST weight 5, price 3, weights can be overridden 0-10). A MUST that is UNMET (or over budget when the budget is hard) makes a product ineligible. The winner must be eligible with all hard constraints verified. Sensitivity re-ranks with each weight ×0.5/×2, reusing the cached utility matrix. Stored as a `comparison` run with its source run ids.
- Ask (`POST /workspaces/{id}/ask`): hybrid search (optional product filter) → evidence pack → answer. The offline engine is extractive (IDF-weighted term coverage ≥ 0.5, up to 3 verbatim sentences with [E#]); the LLM engine uses a JSON schema {answerable, answer}. Every sentence must pass the citation validator or is dropped (`dropped_sentences`). If nothing is left, it abstains. LLM failure falls back to extractive with `degraded`. Stored as an `ask` run.
- Prices: global `price_snapshots` (product, retailer, NUMERIC amount, ISO currency, observed_at, in_stock, source). Batch insert uses ON CONFLICT DO NOTHING (idempotent). History is bucketed in SQL (date_trunc day/week/month, min/max per retailer) plus stats (latest per retailer, lowest current in stock, all-time low/high, 30-day average/low, change %, volatility, lowest-in-window). The Value Agent prefers the cheapest fresh (≤ PRICE_MAX_AGE_DAYS) in-stock snapshot in the budget currency, then evidence, then catalog.
- Redis (`app/coordination`): one pool per process; empty `REDIS_URL` disables everything and all features fail open on Redis errors. GCRA rate limits in one Lua script using Redis TIME (auth per IP + per email, agent POSTs per user; 429 + Retry-After). Price-history read-through cache with version-key invalidation (`X-Cache`). `Idempotency-Key` middleware for POST/PATCH (scoped per caller+path, SET NX claim, replay < 500, 409 in progress, 422 body mismatch). One-time tokens via GETDEL (OAuth state + PKCE). `/ready` reports Redis with `required=false`.
- Realtime (`app/realtime`, `docs/REALTIME.md`): push-only WebSocket per workspace, JWT in the first message, membership check with a short DB session, closes on token expiry. Hub with bounded per-socket queues (slow consumer → 1013), limits on connections/size/rate/idle, origin allow-list. EventBus → Redis pub/sub `mm:rt:<ws>` with local fallback; presence in a TTL'd Redis sorted set. Comments (one-level replies, soft delete) and votes (upsert) are REST writes that publish events; agent runs publish `agent_run.completed`.
- Events (`app/events`, `docs/EVENTS.md`): transactional outbox (`new_event()` added in the same transaction as comments, votes, agent runs, prices) → relay (SKIP LOCKED, in order, backoff, give-up, purge) → Kafka topic `mirrormarket.events.v1` keyed by workspace/product (aiokafka, acks=all, idempotent producer) → idempotent consumer (inbox `processed_events` in the handler's transaction, manual offset commit, retries, `.dlq` topic, rewind on failure) → `workspace_activity` read model. Broker ports with an in-memory implementation for tests. Worker: `python -m app.workers.event_worker`.
- Offline by default: `EMBEDDING_PROVIDER=hashing`, `REQUIREMENTS_EXTRACTOR=rules`.

## Database migrations (head 0012)

0001 pgvector · 0002 users/orgs/workspaces/members · 0003 catalog + workspace_products ·
0004 sources/snapshots/documents · 0005 chunks/embeddings/jobs · 0006 chunk tsvector + GIN ·
0007 purchase_requirements/requirement_versions · 0008 evidence_packs/evidence_items · 0009 agent_runs · 0010 price_snapshots · 0011 workspace_comments/product_votes · 0012 outbox_events/processed_events/workspace_activity

## Major endpoints (/api/v1)

- health, ready (database, migrations, redis); auth register/login/me (rate limited)
- Headers: `Idempotency-Key` on any POST/PATCH; responses may carry `X-Cache`, `X-RateLimit-*`, `Retry-After`, `Idempotent-Replayed`
- workspaces CRUD + members; workspaces/{id}/products
- products (search, by-identifier, variants, identifiers, specifications)
- POST/GET products/{id}/prices (batch record; history ?currency=&bucket=&since=&until=)
- products/{id}/sources (+upload); sources/{id} ingest/document/embed/chunks; embedding-jobs/{id}
- POST workspaces/{id}/search (hybrid|lexical|vector + filters)
- workspaces/{id}/requirements: POST extract, PUT save, GET current, versions[/{n}], diff?from=&to=
- workspaces/{id}/evidence-packs: POST create (MEMBER+), GET list/get, POST {pack}/validate
- POST workspaces/{id}/products/{pid}/reviews/analyze (MEMBER+); POST .../compatibility (optional body {owned_devices}); POST .../value; POST .../risk; POST .../synthesize
- POST workspaces/{id}/analyze (MEMBER+, optional body {product_ids}); POST workspaces/{id}/compare (body {product_ids, weights, budget_is_hard}); POST workspaces/{id}/ask (body {question, product_ids, limit})
- workspaces/{id}/comments: POST (MEMBER+), GET (?product_id=&workspace_level_only=), PATCH/DELETE {comment}; PUT workspaces/{id}/products/{pid}/vote {value: 1|-1|0}; GET workspaces/{id}/votes; GET workspaces/{id}/presence; GET workspaces/{id}/activity (eventually consistent feed)
- WebSocket `/api/v1/ws/workspaces/{id}` (events: presence.*, comment.*, vote.changed, agent_run.completed)
- POST workspaces/{id}/products/{pid}/research (MEMBER+); GET workspaces/{id}/agent-runs[/{run}] (?agent=&product_id=)

## Tests

- 584 tests (Phase 21): 582 pass in the cloud workspace at 97% coverage; the 2 real-Kafka tests (`-m kafka`) need Docker and run only on the Mac and in CI. Phase 20: 558 (Mac + CI).
- WebSocket tests use an in-loop ASGI client (`tests/support/ws.py`), so they share the rolled-back DB session.
- DB/Redis tests use Testcontainers on the Mac and in CI, or `TEST_DATABASE_URL` / `TEST_REDIS_URL` in the cloud workspace. Redis is off in ordinary tests.

## Current measured metrics

- Retrieval smoke benchmark (synthetic: 8 docs, 8 labelled queries, hashing embedder), Recall@3 / MRR:
  lexical 0.375 / 0.375, vector 0.875 / 0.823, hybrid 0.875 / 0.823. Source: `tests/db/test_search.py`.
- Citation validator (synthetic, author-labelled, 24 answers): case accuracy 1.0, issue precision 1.0,
  recall 1.0 (16/16). Evidence: `backend/benchmarks/results/citations.json`. This shows the rules work, not real-world accuracy.
- Fact extraction, rules engine (synthetic, 24 labelled spec sentences): accuracy 0.7917 → 0.875 after allowing
  hyphenated units ("14.2-inch"). The 3 misses are spelled-out numbers. Evidence: `backend/benchmarks/results/fact_extraction.json`.
- Review aspect sentiment, rules engine (synthetic, 24 labelled sentences): pair F1 0.8889 → 0.9333 (precision 1.0,
  recall 0.875) and exact match 0.8333 → 0.875 after adding "well" and "dies" to the lexicon. Misses: sarcasm and implicit opinions. Evidence: `backend/benchmarks/results/review_sentiment.json`.
- Compatibility capability detection, rules (synthetic): 0.8182 → 1.0 on 22 tuning cases after scoping negation to the clause;
  held-out set written after tuning: 1.0 (8/8). Evidence: `backend/benchmarks/results/compatibility.json`.
- Price extraction (synthetic, 14 labelled sentences): 0.8571 → 0.9286 after supporting EU thousands separators ("€1.299").
  Remaining miss: two products priced in one sentence. Evidence: `backend/benchmarks/results/price_extraction.json`.
- Risk detection (synthetic, 22 labelled sentences incl. negated and benign ones, not tuned on): precision 1.0, recall 1.0.
  The patterns and cases have the same author, so this isn't an independent evaluation. Evidence: `backend/benchmarks/results/risk_detection.json`.
- Comparison engine latency (synthetic seeded 50 products × 20 criteria, incl. sensitivity, cloud workspace): median 288.98 ms
  → 24.69 ms (11.7×) after caching the weight-independent utility matrix. Evidence: `backend/benchmarks/results/comparison_scale.json` (Mac M-series arm64: 12.47 ms median, 20.34 ms p95).

- Extractive QA (synthetic): 18 tuning questions (13 answerable + 5 unanswerable) 0.7222 → 1.0 after stopword/suffix changes;
  held-out set written afterwards (11 questions, other product): 0.8182; misses: "Is LDAC available?", "How fast does it charge?".
  Evidence: `backend/benchmarks/results/qa_extractive.json`.

- Price history query (real PostgreSQL 16, synthetic 50k snapshots / 21 products, ~2.4k for the target, cloud workspace):
  median 179.69 ms → 15.14 ms after removing an O(n²) baseline lookup in the stats and loading columns instead of ORM
  entities. The composite (product, currency, observed_at) index gave no measurable gain at this size (14.12 ms without).
  Evidence: `backend/benchmarks/results/price_history_db.json` (re-run on the Mac against Compose Postgres).

- Redis paths (cloud workspace, PostgreSQL 16 + Redis 7.0, same 50k synthetic snapshots): price history from Postgres
  median 14.07 ms vs from the Redis cache 0.79 ms (23 KB payload); one GCRA rate-limit decision 0.11 ms median.
  Mac (arm64, Compose PG17 + Redis 7.4): Postgres 7.69 ms vs cache 1.00 ms median; rate-limit decision 0.36 ms. Evidence: `backend/benchmarks/results/redis_paths.json`.

- Realtime fan-out (cloud workspace, Redis 7.0, in-memory queues, no socket I/O): publish → queued for all connections,
  median 0.19 / 0.24 / 0.65 ms for rooms of 10 / 100 / 1000 through Redis pub/sub; 0.02 / 0.04 / 0.28 ms single-process.
  Mac (arm64, Redis 7.4): 0.42 / 0.46 / 0.64 ms through Redis; 0.006 / 0.025 / 0.22 ms single-process. Evidence: `backend/benchmarks/results/realtime_fanout.json`.

- Event pipeline (cloud workspace, PostgreSQL 16, IN-MEMORY broker, so PostgreSQL side only; 2000 synthetic events):
  relay 9,869 events/s; consumer 410 events/s (one transaction per event); 2000/2000 redelivered duplicates rejected.
  Evidence: `backend/benchmarks/results/event_pipeline.json` (re-run on the Mac against real Kafka).

## Known issues / limits

- The hashing embedder is lexical, not semantic (OpenAI needs a key). Chunk size is measured in characters.
- The rule extractor is English-only pattern matching; what it can't use is returned as `unparsed`.
- Ingestion runs in the request; raw bytes are stored in Postgres (S3 comes in Phase 31).
- Not yet: refresh tokens, invitations, security headers, OAuth login flow (Phase 22 uses the one-time token store).
- Events: no dead-letter replay tool, no schema registry; Kafka data is not persisted across `make down` (the outbox is the source).
- Realtime: at-most-once, no replay (clients refetch on reconnect); membership is checked only at connect.
- Rate limits key on the socket peer IP; behind a proxy, uvicorn `--forwarded-allow-ips` is needed (Phase 29).
- Before Phase 30: pin the CI runner (ubuntu-latest moves to 26 on 2026-10-19).
- New vector migrations need `from pgvector.sqlalchemy import Vector` added by hand.

## Important commands

```bash
cd backend && uv run python -m benchmarks.citations && uv run python -m benchmarks.fact_extraction && uv run python -m benchmarks.review_sentiment && uv run python -m benchmarks.compatibility && uv run python -m benchmarks.price_extraction && uv run python -m benchmarks.risk_detection && uv run python -m benchmarks.comparison_scale && uv run python -m benchmarks.qa_extractive && cd ..
make up
cd backend && DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket uv run python -m benchmarks.price_history_db && cd ..
cd backend && DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket REDIS_URL=redis://localhost:6380/0 uv run python -m benchmarks.redis_paths && cd ..
cd backend && REDIS_URL=redis://localhost:6380/0 uv run python -m benchmarks.realtime_fanout && cd ..
make smoke-events
docker compose stop event-worker && cd backend && DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket KAFKA_BOOTSTRAP_SERVERS=localhost:9094 uv run python -m benchmarks.event_pipeline && cd ..
make check
make up
make ps
make down
make migration m="msg"
make migrate
```
