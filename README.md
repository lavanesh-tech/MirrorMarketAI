# MirrorMarket AI

**Real-time collaborative purchasing intelligence.**

People create shared workspaces for purchases they are researching ("a laptop under
$1,500 for Java, Docker, university and occasional local AI"). MirrorMarket turns
the request into structured requirements, gathers evidence about candidate products,
runs specialised AI agents over that evidence, and returns comparisons in which
every factual claim cites its source. Collaborators see the research happen live.

> **Status: Phase 6 of 35 complete.** Done so far: the backend foundation, async
> PostgreSQL + pgvector, migrations, readiness checks, JWT auth, role-based
> comparison workspaces, a product catalog, and evidence sources: SSRF-safe URL
> ingestion and PDF/HTML/text uploads, turned into versioned snapshots and
> normalized documents, then chunked and embedded into pgvector by a background
> worker. There is no retrieval API, no agents and no UI yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Principles

- **PostgreSQL is the system of record**, and **pgvector** is the vector store.
- **Deterministic code, not the LLM, decides** auth, access control, prices,
  numbers, persistence, tenant isolation and citation validity.
- **Evidence-backed answers:** LLM claims must cite evidence the application can validate.
- **No invented data or metrics:** benchmarks record the commit, dataset, hardware and config behind every number.

## Tech stack (target)

| Area | Choice |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic v2, pydantic-settings |
| Data | PostgreSQL 17 + pgvector, SQLAlchemy 2.x (async), Alembic |
| Cache / coordination | Redis 7: GCRA rate limits, price cache, Idempotency-Key replay, one-time tokens (fail open) |
| Events | Kafka (KRaft): transactional outbox, idempotent consumers, dead-letter topic; see `docs/EVENTS.md` |
| AI | OpenAI chat + embeddings, LangChain where it helps |
| Realtime | WebSockets (push-only events, Redis pub/sub fan-out, presence); see `docs/REALTIME.md` |
| Frontend | Next.js, React, TypeScript |
| Quality | Ruff, mypy (strict), pytest, Playwright, k6 |
| Ops | Docker, GitHub Actions, Terraform, AWS (ECS Fargate, RDS, ElastiCache, S3) |

## Repository layout

```
MirrorMarketAI/
├── backend/                  FastAPI service (Python, managed with uv)
│   ├── app/
│   │   ├── main.py           application factory + lifespan
│   │   ├── core/             settings, logging, request context, middleware
│   │   ├── api/v1/           versioned HTTP routes (/api/v1/...)
│   │   ├── schemas/          Pydantic request/response models
│   │   └── ...               domain, models, repositories, services, ingestion,
│   │                         retrieval, agents, providers, events, workers,
│   │                         realtime, security, telemetry (filled in later phases)
│   ├── migrations/           Alembic environment + versioned migrations
│   ├── tests/                unit/, api/ and db/ (real PostgreSQL) tests
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── pyproject.toml        dependencies + ruff/mypy/pytest configuration
│   └── uv.lock               exact, reproducible dependency versions
├── infrastructure/docker/    local container init scripts (pgvector extension)
├── evaluation/               RAG/agent evaluation harness (Phase 27)
├── benchmarks/results/       recorded benchmark runs (Phase 29)
├── docs/                     architecture, decisions, roadmap
├── .github/workflows/ci.yml  CI pipeline
├── docker-compose.yml        postgres+pgvector, redis, kafka, api, workers
├── Makefile                  developer commands
└── .env.example              configuration template (copy to .env)
```

## Getting started (macOS, Apple Silicon)

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/),
[uv](https://docs.astral.sh/uv/) (`brew install uv`), `make` (ships with Xcode
Command Line Tools). uv installs Python 3.12 for you if it's missing.

```bash
make env        # create .env from .env.example
make install    # install locked backend dependencies into backend/.venv
make check      # ruff + mypy + pytest with coverage (DB tests need Docker running)
make up         # build image, start postgres/redis, run migrations, start api
make health     # liveness:  GET /api/v1/health
make ready      # readiness: GET /api/v1/ready (database + schema revision)
make verify-pgvector
```

Interactive API docs: <http://127.0.0.1:8000/api/v1/docs>

To run the API on your Mac with auto-reload instead of in a container:

```bash
make infra      # just postgres + redis
make migrate    # alembic upgrade head against 127.0.0.1:5433
make run        # uvicorn --reload on 127.0.0.1:8000
```

Local ports (all bound to `127.0.0.1` only; change them in `.env`):

| Service | Host port |
| --- | --- |
| API | 8000 |
| PostgreSQL | 5433 |
| Redis | 6380 |

Run `make help` for every command.

## API (so far)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness: the process is up. Does not check dependencies. |
| GET | `/api/v1/ready` | Readiness: 200 only if PostgreSQL is reachable and migrated to the revision this build expects; otherwise 503. |
| POST | `/api/v1/auth/register` | Create an account (plus a personal organization) |
| POST | `/api/v1/auth/login` | Email + password → Bearer access token (15 min) |
| GET | `/api/v1/auth/me` | Current user 🔒 |
| POST | `/api/v1/workspaces` | Create a comparison workspace; you become OWNER 🔒 |
| GET | `/api/v1/workspaces?limit=&offset=` | Workspaces you're a member of, paginated 🔒 |
| GET | `/api/v1/workspaces/{id}` | Workspace details (members only) 🔒 |
| PATCH | `/api/v1/workspaces/{id}` | Update name/description (OWNER or EDITOR) 🔒 |
| GET | `/api/v1/workspaces/{id}/members` | Members and roles (members only) 🔒 |
| POST | `/api/v1/products` | Create a catalog product (409 if brand + name already exists) 🔒 |
| GET | `/api/v1/products?q=&category=&limit=&offset=` | Search the catalog 🔒 |
| GET | `/api/v1/products/by-identifier?scheme=GTIN&value=` | Look up by GTIN/UPC/EAN, MPN, ASIN or SKU 🔒 |
| GET | `/api/v1/products/{id}` | Product with variants, identifiers, specifications 🔒 |
| POST | `/api/v1/products/{id}/variants` | Add a variant (creator only) 🔒 |
| POST | `/api/v1/products/{id}/identifiers` | Add a validated identifier (creator only) 🔒 |
| PUT | `/api/v1/products/{id}/specifications` | Upsert typed spec values (creator only) 🔒 |
| POST | `/api/v1/workspaces/{id}/products` | Add a product to a workspace (OWNER/EDITOR) 🔒 |
| GET | `/api/v1/workspaces/{id}/products` | Products in the workspace (members) 🔒 |
| DELETE | `/api/v1/workspaces/{id}/products/{product_id}` | Remove it from the workspace (OWNER/EDITOR) 🔒 |
| POST | `/api/v1/products/{id}/sources` | Register a URL source (shared, or private with `workspace_id`) 🔒 |
| GET | `/api/v1/products/{id}/sources` | Shared sources + those from your workspaces 🔒 |
| POST | `/api/v1/products/{id}/sources/upload` | Upload PDF/HTML/MD/TXT (multipart) → snapshot + document 🔒 |
| POST | `/api/v1/sources/{id}/ingest` | SSRF-safe fetch → snapshot → parsed document 🔒 |
| GET | `/api/v1/sources/{id}` | Source status + latest document metadata 🔒 |
| GET | `/api/v1/sources/{id}/document` | Latest normalized text (untrusted content) 🔒 |
| POST | `/api/v1/sources/{id}/embed` | Chunk + embed the latest document now (idempotent) 🔒 |
| GET | `/api/v1/sources/{id}/chunks` | Chunks with character offsets 🔒 |
| GET | `/api/v1/embedding-jobs/{id}` | Job status, attempts, counts, tokens 🔒 |
| POST | `/api/v1/workspaces/{id}/requirements/extract` | Turn a free-text brief into structured requirements (preview) 🔒 |
| PUT | `/api/v1/workspaces/{id}/requirements` | Save a new requirements version (optimistic locking) 🔒 |
| GET | `/api/v1/workspaces/{id}/requirements[/versions[/{n}]]`, `/diff?from=&to=` | Current requirements, history and diffs 🔒 |
| POST/GET | `/api/v1/workspaces/{id}/evidence-packs[/{pack_id}]` | Freeze search results as citable evidence (E1..En) / list / get 🔒 |
| POST | `/api/v1/workspaces/{id}/evidence-packs/{pack_id}/validate` | Check [E1]-style citations: markers, uncited claims, numbers, quotes 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/research` | Product Research Agent: cited facts vs. requirements (MET/UNMET/UNKNOWN) 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/reviews/analyze` | Review Intelligence Agent: aspect sentiment, praises, complaints (cited) 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/compatibility` | Compatibility Agent: owned devices → capabilities → cited verdict 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/value` | Value Agent: cited price, budget fit, requirement fit, value index 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/risk` | Risk Agent: warranty/returns/safety/reliability + flags from other agents 🔒 |
| POST | `/api/v1/workspaces/{id}/products/{product_id}/synthesize` | Synthesis Agent: one verdict from the latest runs 🔒 |
| POST | `/api/v1/workspaces/{id}/analyze` | Run every agent (bounded) for the workspace's products and rank them 🔒 |
| POST | `/api/v1/workspaces/{id}/compare` | Comparison matrix: weighted scores, hard constraints, sensitivity 🔒 |
| POST | `/api/v1/workspaces/{id}/ask` | Ask MirrorMarket: grounded answer with enforced [E#] citations, or abstain 🔒 |
| GET | `/api/v1/workspaces/{id}/agent-runs[/{run_id}]` | Agent run history with output and citation validation 🔒 |
| POST/GET | `/api/v1/products/{id}/prices` | Record price observations (idempotent) / bucketed history + stats 🔒 |
| POST | `/api/v1/workspaces/{id}/search` | Hybrid search (full-text + vector, RRF) over the workspace's evidence, with filters 🔒 |

🔒 = requires `Authorization: Bearer <token>`. Errors always have the shape
`{"error": {"code", "message", "request_id"}}`.
| GET | `/api/v1/openapi.json` | OpenAPI schema |
| GET | `/api/v1/docs` | Swagger UI |

Every response carries an `X-Request-ID` header. Send your own (8-128 characters
from `A-Z a-z 0-9 . _ -`) to correlate across services; otherwise one is generated.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Decisions (ADR log)](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
- [Data model](docs/DATA_MODEL.md)
- [Testing](docs/TESTING.md)
