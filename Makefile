# MirrorMarket AI - developer commands.   Run `make help` to list targets.

SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND := backend
UV      := uv
COMPOSE := docker compose
API_URL ?= http://127.0.0.1:8000

# Pass the repo-root .env to `uv run` when it exists (host-side dev server).
ENV_FILE_FLAG := $(if $(wildcard .env),--env-file ../.env,)

.PHONY: help
help: ## List available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
.PHONY: env
env: ## Create .env from .env.example (does not overwrite an existing .env)
	@if [ -f .env ]; then echo ".env already exists - leaving it alone"; \
	else cp .env.example .env && echo "created .env from .env.example"; fi

.PHONY: install
install: ## Install backend dependencies (exactly as locked in uv.lock)
	cd $(BACKEND) && $(UV) sync --locked

# --------------------------------------------------------------------------- #
# Quality gates
# --------------------------------------------------------------------------- #
.PHONY: fmt
fmt: ## Auto-format and auto-fix lint issues
	cd $(BACKEND) && $(UV) run ruff format . && $(UV) run ruff check --fix .

.PHONY: lint
lint: ## Lint + formatting check (no changes)
	cd $(BACKEND) && $(UV) run ruff check . && $(UV) run ruff format --check .

.PHONY: typecheck
typecheck: ## Strict mypy
	cd $(BACKEND) && $(UV) run mypy

.PHONY: test
test: ## Run the full test suite (DB tests start a Postgres container; Docker must be running)
	cd $(BACKEND) && $(UV) run pytest

.PHONY: test-unit
test-unit: ## Run tests that need no database (fast, no Docker)
	cd $(BACKEND) && $(UV) run pytest -m "not db"

.PHONY: test-db
test-db: ## Run only database, migration and readiness tests
	cd $(BACKEND) && $(UV) run pytest -m db

.PHONY: cov
cov: ## Run tests with branch coverage report
	cd $(BACKEND) && $(UV) run pytest --cov --cov-report=term-missing

.PHONY: check
check: lint typecheck cov ## Everything CI runs for the backend

# --------------------------------------------------------------------------- #
# Database migrations (host -> Compose Postgres on 127.0.0.1:5433)
# --------------------------------------------------------------------------- #
.PHONY: migrate
migrate: ## Apply all migrations to the local database
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) alembic upgrade head

.PHONY: migration
migration: ## Create a migration from model changes: make migration m="add users table"
	@test -n "$(m)" || (echo 'usage: make migration m="describe the change"' && exit 1)
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) alembic revision --autogenerate -m "$(m)"

.PHONY: db-current
db-current: ## Show the migration revision the local database is at
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) alembic current

.PHONY: db-history
db-history: ## List all migrations
	cd $(BACKEND) && $(UV) run alembic history --verbose

.PHONY: db-downgrade
db-downgrade: ## Roll back the most recent migration on the local database
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) alembic downgrade -1

.PHONY: db-shell
db-shell: ## Open psql inside the Postgres container
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
.PHONY: run
run: ## Run the API on the host with auto-reload (http://127.0.0.1:8000)
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

# --------------------------------------------------------------------------- #
# Docker
# --------------------------------------------------------------------------- #
.PHONY: compose-config
compose-config: ## Validate docker-compose.yml
	$(COMPOSE) config --quiet && echo "docker-compose.yml is valid"

.PHONY: build
build: ## Build the API image
	$(COMPOSE) build api

.PHONY: up
up: ## Start postgres + redis, run migrations, start api; wait until healthy
	$(COMPOSE) up -d --build --wait --wait-timeout 300

.PHONY: infra
infra: ## Start only postgres + redis (then `make migrate` and `make run` on the host)
	$(COMPOSE) up -d --wait --wait-timeout 120 postgres redis

.PHONY: down
down: ## Stop containers (keeps database volume)
	$(COMPOSE) down

.PHONY: reset-db
reset-db: ## Stop containers AND delete local database volume
	$(COMPOSE) down -v

.PHONY: ps
ps: ## Show container status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Follow logs from all services
	$(COMPOSE) logs -f

.PHONY: health
health: ## Call the liveness endpoint
	@curl -fsS -i $(API_URL)/api/v1/health; echo

.PHONY: ready
ready: ## Call the readiness endpoint (checks database + migration state)
	@curl -sS -i $(API_URL)/api/v1/ready; echo

# --------------------------------------------------------------------------- #
# Frontend (Next.js in ./frontend; needs Node 22+)
# --------------------------------------------------------------------------- #
.PHONY: web-install
web-install: ## Install frontend dependencies exactly as locked
	cd frontend && npm ci

.PHONY: web-dev
web-dev: ## Run the web app on http://localhost:3000 (start the API first: make up)
	cd frontend && npm run dev

.PHONY: web-check
web-check: ## Frontend lint, types, format, API-type drift, tests and production build
	cd frontend && npm run check

.PHONY: web-e2e-install
web-e2e-install: ## One-time: download the browser the end-to-end tests drive
	cd frontend && npx playwright install chromium

.PHONY: web-e2e
web-e2e: ## End-to-end tests in a real browser (start the API first: make up)
	cd frontend && npm run build && npm run e2e

ENGINE   ?= rules
EMBEDDER ?= hashing

.PHONY: eval
eval: ## End-to-end quality evaluation (needs PostgreSQL: make up). ENGINE=rules|openai EMBEDDER=hashing|openai
	cd $(BACKEND) && $(UV) run $(ENV_FILE_FLAG) python -m evaluation --engine $(ENGINE) --embedder $(EMBEDDER)

.PHONY: eval-report
eval-report: ## Rebuild docs/EVALUATION.md from the committed result files
	cd $(BACKEND) && $(UV) run python -m evaluation --report-only

.PHONY: api-docs
api-docs: ## Regenerate docs/api (OpenAPI, endpoint index, Postman collection) from the code
	cd $(BACKEND) && $(UV) run python -m tools.api_docs
	@test -d frontend/node_modules && (cd frontend && npm run --silent api:types) || true

.PHONY: obs-up
obs-up: ## Start the stack with Prometheus, Grafana (http://127.0.0.1:3001) and Jaeger; tracing on
	OTEL_ENABLED=true $(COMPOSE) --profile observability up -d --build --wait --wait-timeout 300
	@echo "Grafana http://127.0.0.1:3001  Prometheus http://127.0.0.1:9090  Jaeger http://127.0.0.1:16686"

.PHONY: obs-down
obs-down: ## Stop the stack including the observability containers (keeps volumes)
	$(COMPOSE) --profile observability down

.PHONY: obs-check
obs-check: ## Validate the Prometheus config and alert rules with promtool (needs Docker)
	docker run --rm --entrypoint promtool \
		-v "$(CURDIR)/infrastructure/observability/prometheus:/etc/prometheus:ro" \
		prom/prometheus:v3.13.4 check config /etc/prometheus/prometheus.yml

.PHONY: smoke-observability
smoke-observability: ## Check metrics reach Prometheus, the dashboard is in Grafana and traces reach Jaeger
	@python3 infrastructure/scripts/smoke_observability.py $(API_URL)

# --------------------------------------------------------------------------- #
# Load testing (k6 in Docker; see docs/PERFORMANCE.md)
# --------------------------------------------------------------------------- #
LOAD_COMPOSE := $(COMPOSE) -f docker-compose.yml -f docker-compose.loadtest.yml
API_WORKERS ?= 1
RATE ?= 30
STEPS ?= 25,50,100,150,200,300

.PHONY: load-up
load-up: ## Start the stack for load testing (offline engines, raised rate limits). API_WORKERS=4 for 4 API processes
	API_WORKERS=$(API_WORKERS) $(LOAD_COMPOSE) up -d --build --wait --wait-timeout 300

.PHONY: load-smoke
load-smoke: ## 30-second check that every scripted request works (needs: make load-up)
	cd $(BACKEND) && $(UV) run python -m benchmarks.k6_run smoke

.PHONY: load-test
load-test: ## Steady load for 2 minutes, recorded in benchmarks/results. RATE=30 requests/s
	cd $(BACKEND) && RATE=$(RATE) $(UV) run python -m benchmarks.k6_run load

.PHONY: load-capacity
load-capacity: ## Stepped load to find the highest sustained rate. STEPS=25,50,100,150,200,300
	cd $(BACKEND) && STEPS=$(STEPS) $(UV) run python -m benchmarks.k6_run capacity

.PHONY: perf-report
perf-report: ## Rebuild docs/PERFORMANCE.md from the recorded results
	cd $(BACKEND) && $(UV) run python -m benchmarks.k6_report

.PHONY: smoke-events
smoke-events: ## End-to-end check: comment -> outbox -> Kafka -> consumer -> activity feed
	@python3 infrastructure/scripts/smoke_events.py $(API_URL)

.PHONY: verify-pgvector
verify-pgvector: ## Confirm the pgvector extension is installed in the local database
	$(COMPOSE) exec -T postgres sh -c "psql -U \"\$$POSTGRES_USER\" -d \"\$$POSTGRES_DB\" -tAc \"SELECT 'vector ' || extversion FROM pg_extension WHERE extname = 'vector'\""

.PHONY: clean
clean: ## Remove local caches (not containers or volumes)
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name htmlcov \) -prune -exec rm -rf {} +
	rm -f $(BACKEND)/.coverage $(BACKEND)/coverage.xml
