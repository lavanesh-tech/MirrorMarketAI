# MirrorMarket AI

**Real-time collaborative purchasing intelligence.**

People create shared workspaces for purchases they are researching ("a laptop under
$1,500 for Java, Docker, university and occasional local AI"). MirrorMarket turns
the request into structured requirements, gathers evidence about candidate products,
runs specialised AI agents over that evidence, and returns comparisons in which
every factual claim cites its source. Collaborators see the research happen live.

> **Status: Phase 1 of 35, repository foundation.** Only the backend skeleton,
> local infrastructure and quality tooling exist so far. There is no RAG, no agents,
> no auth and no UI yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

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
| Cache / coordination | Redis |
| Events | Kafka (from Phase 21) |
| AI | OpenAI chat + embeddings, LangChain where it helps |
| Realtime | WebSockets |
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
│   ├── tests/                unit/ and api/ tests
│   ├── Dockerfile
│   ├── pyproject.toml        dependencies + ruff/mypy/pytest configuration
│   └── uv.lock               exact, reproducible dependency versions
├── infrastructure/docker/    local container init scripts (pgvector extension)
├── evaluation/               RAG/agent evaluation harness (Phase 27)
├── benchmarks/results/       recorded benchmark runs (Phase 29)
├── docs/                     architecture, decisions, roadmap
├── .github/workflows/ci.yml  CI pipeline
├── docker-compose.yml        postgres+pgvector, redis, api
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
make check      # ruff + mypy + pytest with coverage
make up         # build the API image, start postgres/redis/api, wait until healthy
make health     # GET http://127.0.0.1:8000/api/v1/health
make verify-pgvector
```

Interactive API docs: <http://127.0.0.1:8000/api/v1/docs>

To run the API on your Mac with auto-reload instead of in a container:

```bash
make infra      # just postgres + redis
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
| GET | `/api/v1/openapi.json` | OpenAPI schema |
| GET | `/api/v1/docs` | Swagger UI |

Every response carries an `X-Request-ID` header. Send your own (8-128 characters
from `A-Z a-z 0-9 . _ -`) to correlate across services; otherwise one is generated.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Decisions (ADR log)](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
