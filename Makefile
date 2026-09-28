# Loads .env (if present) so DATABASE_URL and POSTGRES_* reach every command.
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE ?= docker-compose
ALEMBIC := uv run alembic

.PHONY: db-up db-down migrate-up migrate-down migrate-new migrate-current migrate-history

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
