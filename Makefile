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
test: ## Run the test suite
	cd $(BACKEND) && $(UV) run pytest

.PHONY: cov
cov: ## Run tests with branch coverage report
	cd $(BACKEND) && $(UV) run pytest --cov --cov-report=term-missing

.PHONY: check
check: lint typecheck cov ## Everything CI runs for the backend

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
up: ## Start postgres, redis and api; wait until all are healthy
	$(COMPOSE) up -d --build --wait --wait-timeout 180

.PHONY: infra
infra: ## Start only postgres + redis (for `make run` on the host)
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
health: ## Call the health endpoint
	@curl -fsS -i $(API_URL)/api/v1/health; echo

.PHONY: verify-pgvector
verify-pgvector: ## Confirm the pgvector extension is installed in the local database
	$(COMPOSE) exec -T postgres sh -c "psql -U \"\$$POSTGRES_USER\" -d \"\$$POSTGRES_DB\" -tAc \"SELECT 'vector ' || extversion FROM pg_extension WHERE extname = 'vector'\""

.PHONY: clean
clean: ## Remove local caches (not containers or volumes)
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name htmlcov \) -prune -exec rm -rf {} +
	rm -f $(BACKEND)/.coverage $(BACKEND)/coverage.xml
