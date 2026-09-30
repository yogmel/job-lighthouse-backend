# Loads .env (if present) so DATABASE_URL and POSTGRES_* reach every command.
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE ?= docker-compose
ALEMBIC := uv run alembic

.PHONY: certs up down logs db-up db-down migrate-up migrate-down migrate-new migrate-current migrate-history run-job-runner run-companies test hooks lint

certs: ## Create a self-signed TLS cert for local Nginx (nginx/certs/, git-ignored)
	@mkdir -p nginx/certs
	openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
		-subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
		-keyout nginx/certs/privkey.pem -out nginx/certs/fullchain.pem

up: ## Build and start the full stack (Postgres, migrate, both services, Nginx) and wait until healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the full stack (data volume is kept)
	$(COMPOSE) down

logs: ## Follow logs of all containers
	$(COMPOSE) logs -f

db-up: ## Start local Postgres and wait until it is healthy
	$(COMPOSE) up -d --wait postgres

db-down: ## Stop local Postgres (data volume is kept)
	$(COMPOSE) down

migrate-up: ## Apply all pending migrations
	$(ALEMBIC) upgrade head

migrate-down: ## Roll back the most recent migration
	$(ALEMBIC) downgrade -1

migrate-new: ## Create a new migration: make migrate-new m="add users table"
	@test -n "$(m)" || (echo 'usage: make migrate-new m="message"' && exit 1)
	$(ALEMBIC) revision -m "$(m)"

migrate-current: ## Show the current revision
	$(ALEMBIC) current

migrate-history: ## List all revisions
	$(ALEMBIC) history --verbose

run-job-runner: ## Run the Job Runner Service locally on :8001 (reload on change)
	uv run uvicorn job_lighthouse_backend.job_runner.main:app --reload --port 8001

run-companies: ## Run the Companies Service locally on :8002 (reload on change)
	uv run uvicorn job_lighthouse_backend.companies.main:app --reload --port 8002

test: ## Run the test suite (DB tests need `make db-up`)
	uv run pytest

hooks: ## Install the pre-commit git hooks (Ruff, gitleaks, uv lock --check)
	uv run pre-commit install

lint: ## Run the CI lint checks locally
	uv lock --check
	uv run ruff check .
	uv run ruff format --check .
