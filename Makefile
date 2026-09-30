# Loads .env (if present) so DATABASE_URL and POSTGRES_* reach every command.
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE ?= docker-compose
ALEMBIC := uv run alembic
# Keep in step with SEMGREP_VERSION in .github/workflows/ci.yml.
SEMGREP_VERSION := 1.178.0

.PHONY: certs up down logs db-up db-down migrate-up migrate-down migrate-new migrate-current migrate-history run-job-runner run-companies test cov hooks lint typecheck sast audit secrets scan-image

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

cov: ## Run the test suite with coverage, same as CI (fails below fail_under in pyproject.toml)
	uv run pytest --cov --cov-report=term

hooks: ## Install the pre-commit git hooks (Ruff, mypy, gitleaks, uv lock --check)
	uv run pre-commit install

lint: ## Run the CI lint checks locally
	uv lock --check
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Run mypy, same as CI's typecheck job
	uv run mypy

sast: ## Run Semgrep, same as CI's semgrep job
	uvx semgrep@$(SEMGREP_VERSION) scan --metrics=off --error --config p/python --config p/fastapi src migrations

audit: ## Check locked dependencies for known CVEs (same as CI's pip-audit job)
	uv export --frozen --no-emit-project --format requirements-txt -o .audit-requirements.txt
	uvx pip-audit@2.10.1 --strict --require-hashes --disable-pip -r .audit-requirements.txt; \
		status=$$?; rm -f .audit-requirements.txt; exit $$status

secrets: ## Scan the full git history for secrets (needs gitleaks installed)
	gitleaks git --redact --verbose --exit-code 1 .

scan-image: ## Build the image and scan it with Trivy (needs trivy installed)
	docker build -t job-lighthouse-backend:scan .
	trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --ignorefile .trivyignore job-lighthouse-backend:scan
