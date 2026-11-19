# MirrorMarket AI

**Real-time collaborative purchasing intelligence.**

People create shared workspaces for purchases they are researching ("a laptop under
$1,500 for Java, Docker, university and occasional local AI"). MirrorMarket turns
the request into structured requirements, gathers evidence about candidate products,
runs specialised AI agents over that evidence, and returns comparisons in which
every factual claim cites its source. Collaborators see the research happen live.

> **Status: phases 1-30 of 35 complete** (verified on the developer Mac and in CI).
> The backend, the AI agents, the web app, realtime collaboration, events, security,
> observability, load testing and production images are done. Still to come:
> Terraform for AWS (code and `terraform validate` only, nothing is deployed), a
> scripted local demo, and the final report. See [docs/ROADMAP.md](docs/ROADMAP.md).

## What it does

1. **Requirements:** a free-text brief becomes versioned, structured requirements
   (must-haves, nice-to-haves, budget).
2. **Evidence:** spec sheets, reviews and manuals are ingested (URL or upload),
   chunked, embedded into pgvector and searched with hybrid full-text + vector search.
3. **Agents:** research, reviews, compatibility, value, risk and synthesis agents work
   only from frozen evidence packs; every claim must cite an item the code can check.
4. **Compare and ask:** a weighted comparison matrix with hard constraints and
   sensitivity analysis, and grounded answers that cite their sources or abstain.
5. **Together, live:** comments, votes, presence and agent results arrive over
   WebSockets for everyone in the workspace.

## Measured results

All numbers are recorded with the commit, data and hardware behind them; the
datasets are synthetic and labelled by the author, which the linked reports say.

- **Tests:** 760 backend tests (pytest, real PostgreSQL), 96 frontend tests (Vitest),
  and a Playwright journey from sign-up to a cited answer against the production
  containers; CI runs all of them, scans both images and publishes them to GHCR.
- **Load** ([docs/PERFORMANCE.md](docs/PERFORMANCE.md)): one API process sustained
  100 requests/s; three API containers behind nginx sustained every step tried up
  to 300 requests/s with no failed requests (developer Mac, offline engines).
- **Quality** ([docs/EVALUATION.md](docs/EVALUATION.md)): end-to-end evaluation of
  retrieval, extraction, decisions, grounded answers and poisoned sources, with a
  held-out set and the remaining failures listed.

## Principles

- **PostgreSQL is the system of record**, and **pgvector** is the vector store.
- **Deterministic code, not the LLM, decides** auth, access control, prices,
  numbers, persistence, tenant isolation and citation validity.
- **Evidence-backed answers:** LLM claims must cite evidence the application can validate.
- **No invented data or metrics:** benchmarks record the commit, dataset, hardware and config behind every number.

## Tech stack

| Area | Choice |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic v2, pydantic-settings |
| Data | PostgreSQL 17 + pgvector, SQLAlchemy 2.x (async), Alembic |
| Cache / coordination | Redis 7: GCRA rate limits, price cache, Idempotency-Key replay, one-time tokens (fail open) |
| Events | Kafka (KRaft): transactional outbox, idempotent consumers, dead-letter topic; see `docs/EVENTS.md` |
| AI | OpenAI chat + embeddings, LangChain where it helps |
| API docs | OpenAPI 3.1, endpoint index and Postman collection generated from code and drift-tested; see `docs/API.md` |
| Security | Rotating refresh tokens, append-only audit log, prompt-injection and upload defences, authorization matrix test; see `docs/SECURITY.md` |
| Observability | Prometheus metrics, Grafana dashboard, alert rules, OpenTelemetry traces in Jaeger; see `docs/OBSERVABILITY.md` |
| Realtime | WebSockets (push-only events, Redis pub/sub fan-out, presence); see `docs/REALTIME.md` |
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind; backend-for-frontend with HttpOnly cookie sessions; see `frontend/README.md` |
| Quality | Ruff, mypy (strict), pytest, Playwright, k6 |
| Ops | Docker (non-root, read-only images), GitHub Actions, GHCR, Grype image scan; Terraform for AWS planned (Phase 31, code only) |

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
│   │                         realtime, security, telemetry
│   ├── migrations/           Alembic environment + versioned migrations
│   ├── tests/                unit/, api/ and db/ (real PostgreSQL) tests
│   ├── evaluation/           end-to-end quality evaluation (`make eval`)
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── pyproject.toml        dependencies + ruff/mypy/pytest configuration
│   └── uv.lock               exact, reproducible dependency versions
├── frontend/                 Next.js web app (own Dockerfile, Vitest + Playwright tests)
├── infrastructure/docker/    local container init scripts (pgvector extension)
├── infrastructure/nginx/     load balancer for the scaled load test (Phase 30)
├── infrastructure/observability/  Prometheus config and alerts, Grafana dashboard (Phase 28)
├── benchmarks/k6/            k6 load-test scripts (Phase 29)
├── benchmarks/results/       recorded load-test runs
├── docs/                     architecture, decisions, roadmap
├── .github/workflows/ci.yml  CI pipeline
├── docker-compose.yml        postgres+pgvector, redis, kafka, api, workers, web (profile)
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

### Try the app locally

The whole product (web app, API, workers, PostgreSQL, Redis, Kafka) runs on your
machine with Docker Compose. No API keys are needed: offline rule-based engines and a
local embedder are the defaults.

```bash
make env
make web-up     # everything above plus the web app on http://localhost:3000
```

Open <http://localhost:3000>, create an account and a workspace, write a brief under
Requirements, add two products with a source each under Products, then press
"Research and compare" under Compare and ask a question under Ask. Open the
workspace in a second window to see comments and votes arrive live.

```bash
make web-e2e    # the same journey, automated in a real browser
make down       # stop everything (keeps the database volume)
```

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
| Web app (`make web-up`) | 3000 |
| PostgreSQL | 5433 |
| Redis | 6380 |

Run `make help` for every command.

## API

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

| GET | `/api/v1/openapi.json` | OpenAPI schema |
| GET | `/api/v1/docs` | Swagger UI |

🔒 = requires `Authorization: Bearer <token>`. Errors always have the shape
`{"error": {"code", "message", "request_id"}}`. The full list of endpoints, generated
from the code, is in [docs/api/ENDPOINTS.md](docs/api/ENDPOINTS.md).

Every response carries an `X-Request-ID` header. Send your own (8-128 characters
from `A-Z a-z 0-9 . _ -`) to correlate across services; otherwise one is generated.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Decisions (ADR log)](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
- [Data model](docs/DATA_MODEL.md)
- [Testing](docs/TESTING.md)
- [Observability](docs/OBSERVABILITY.md): metrics, dashboard, alerts and traces (`make obs-up`)
- [Performance](docs/PERFORMANCE.md): load-test results and what they found (`make load-up`, `make load-capacity`)
- [Evaluation](docs/EVALUATION.md): measured quality, what was fixed, and what still fails
- [Security](docs/SECURITY.md) · [Events](docs/EVENTS.md) · [Realtime](docs/REALTIME.md) · [API](docs/API.md)
