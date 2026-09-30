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
| 3 | Users, organizations/workspaces, memberships, authentication foundation | 🔜 |

## Product data and RAG

| # | Phase | Status |
| --- | --- | --- |
| 4 | Products, variants, identifiers, specifications | ⬜ |
| 5 | Product sources, snapshots, document ingestion, uploads, safe URL ingestion | ⬜ |
| 6 | Chunking, OpenAI embeddings, pgvector, embedding jobs | ⬜ |
| 7 | Postgres full-text, vector search, hybrid retrieval, metadata filters, retrieval tests | ⬜ |
| 8 | Purchase requirements, structured extraction, requirement versions | ⬜ |
| 9 | Evidence packs, citations, citation validator | ⬜ |

## Agents

| # | Phase | Status |
| --- | --- | --- |
| 10 | Product Research Agent | ⬜ |
| 11 | Review Intelligence Agent | ⬜ |
| 12 | Compatibility Agent | ⬜ |
| 13 | Value Agent | ⬜ |
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
- [ ] Verified on the developer Mac (`make check`, `make up`, `make ready`)
- [ ] CI green
