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
