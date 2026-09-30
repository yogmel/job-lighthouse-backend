# Job Lighthouse — Backend

See `CLAUDE.md` and `docs/` for architecture and tickets.

## Local database & migrations

Migrations use **Alembic** and live in `migrations/` at the repo root. Both
services share them.

### 1. Env vars

Create a `.env` in the repo root (git-ignored). The Makefile loads it
automatically.

```sh
POSTGRES_USER=lighthouse
POSTGRES_PASSWORD=change-me
POSTGRES_DB=lighthouse
POSTGRES_PORT=5432

DATABASE_URL=postgresql+psycopg://lighthouse:change-me@localhost:5432/lighthouse

# Auth. Generate the secret with: openssl rand -hex 32
JWT_SECRET=change-me
# JWT_TTL_SECONDS=604800        # optional, default 7 days
# GOOGLE_CLIENT_ID=...          # needed for POST /auth/google
```

- `DATABASE_URL` is required. Alembic reads it only from the environment.
- `JWT_SECRET` is required by both services at startup. Use the same value
  for both. Tests set their own.
- URL-encode special characters in the password (e.g. `%` → `%25`).
- Avoid `$` and `#` in `.env` values: Make parses the file and would mangle them.
- A plain `postgresql://` URL also works. It is switched to the psycopg (v3)
  driver automatically.

### 2. Commands

```sh
uv sync                        # install deps
make db-up                     # start Postgres (docker-compose) and wait until healthy
make migrate-up                # alembic upgrade head
make migrate-down              # alembic downgrade -1
make migrate-new m="add users" # new empty migration in migrations/versions/
make migrate-current           # show current revision
make db-down                   # stop Postgres (data is kept)
```

If you use the Docker Compose plugin instead of the standalone binary, run
with `COMPOSE="docker compose"`.

### Writing migrations

- Migrations are written by hand (`op.create_table(...)`). No autogenerate
  yet.
- Every migration needs a working `downgrade()`.

## Services

Both services are FastAPI apps in `src/job_lighthouse_backend/`, sharing
`common/` (settings, DB engine, app factory).

| Service | Module | Local port |
| --- | --- | --- |
| Job Runner | `job_runner.main:app` | 8001 |
| Companies | `companies.main:app` | 8002 |

```sh
make run-job-runner   # needs DATABASE_URL (from .env) and a running Postgres
make run-companies
curl localhost:8001/health localhost:8002/health
make test
```

- Settings come from env vars only. Required: `DATABASE_URL`.
- On startup each service runs `SELECT 1`. If Postgres is unreachable it
  logs the error and exits non-zero.

## Docker Compose

`docker-compose.yml` runs the whole backend from one image (`Dockerfile`):

| Container | What it does | Host port |
| --- | --- | --- |
| `postgres` | Postgres 17, data in the `pgdata` volume | `127.0.0.1:5432` |
| `migrate` | one-shot `alembic upgrade head`, then exits | — |
| `job-runner` | Job Runner Service | `127.0.0.1:8001` |
| `companies` | Companies Service | `127.0.0.1:8002` |
| `nginx` | reverse proxy + TLS, routes by path | `80`, `443` |

```sh
make certs  # once: self-signed cert for Nginx
make up     # docker compose up -d --build --wait
make logs
make down
```

- Uses the same `.env` as above (`POSTGRES_*`). Optional: `JOB_RUNNER_PORT`,
  `COMPANIES_PORT` to change the host ports.
- Services get `DATABASE_URL` from compose, built from `POSTGRES_*` with host
  `postgres`. The `DATABASE_URL` in `.env` (pointing at `localhost`) is only
  for running Alembic / the services on your machine.
- Both services wait for Postgres to be healthy and for `migrate` to finish.
- `.env` is excluded from the image by `.dockerignore`.

## Nginx & TLS

`nginx/default.conf` routes by path prefix:

| Path | Service |
| --- | --- |
| `/companies*`, `/auth*`, `/account*` | Companies |
| `/config*`, `/jobs*`, `/runs*` | Job Runner |
| anything else | 404 from Nginx |

- Port 80 redirects to HTTPS on 443.
- Nginx reads `fullchain.pem` and `privkey.pem` from `NGINX_CERTS_DIR`
  (default `./nginx/certs`, git-ignored). Nginx won't start without them.
- Locally, `make certs` creates a self-signed cert for `localhost`:
  `curl --cacert nginx/certs/fullchain.pem https://localhost/jobs`
  (or `curl -k`).
- On the droplet, the host's Nginx holds the Let's Encrypt cert on 80/443
  and proxies to this Nginx on `127.0.0.1:8443`, which uses a self-signed
  cert. See [`docs/DEPLOY.md`](docs/DEPLOY.md) → Layout on the droplet.
- Optional: `NGINX_HTTP_PORT`, `NGINX_HTTPS_PORT` to change host ports
  (a value like `127.0.0.1:8443` also sets the bind address).

## CI & hooks

`.github/workflows/ci.yml` runs on every PR and every push to `main`:

| Job | Checks |
| --- | --- |
| `lint` | `uv lock --check`, `ruff check`, `ruff format --check` |
| `typecheck` | `mypy` (settings and files in `pyproject.toml` → `[tool.mypy]`) |
| `semgrep` | Semgrep SAST, `p/python` + `p/fastapi` rules, on `src/` and `migrations/` |
| `test` | single Alembic head, `upgrade head` → `downgrade base` → `upgrade head`, `pytest --cov` against a Postgres 17 service; fails below `fail_under` |
| `coverage-comment` | posts the coverage table on the PR (one comment, updated each push); skipped for Dependabot |
| `docker-build` | `docker build` (no push) + Trivy image scan |
| `gitleaks` | secrets in the branch's full git history |
| `pip-audit` | known CVEs in `uv.lock` (all groups) |
| `pr-title` | PR title starts with `BE-`/`FE-`/`PROJ-` + number (`pr-title.yml`); skipped for Dependabot |

Run the same checks locally: `make lint`, `make typecheck`, `make sast`,
`make cov`, `make audit`, `make secrets`, `make scan-image` (the last two
need `gitleaks` / `trivy`, e.g. `brew install gitleaks trivy`).

**Types, SAST, coverage (PROJ-004)**

- **mypy** runs in default mode plus `check_untyped_defs`. Tighten one
  module at a time with `[[tool.mypy.overrides]]` (e.g. `strict = true`).
- **Semgrep** uses registry rulesets, which change over time: a new rule can
  turn a PR red with no code change. Silence a false positive with
  `# nosemgrep: <rule-id>` on the line, with the reason in a comment
  above it.
- **Coverage** settings live in `pyproject.toml` → `[tool.coverage.*]`.
  `concurrency` includes `greenlet`, or SQLAlchemy async code counts as
  missed. `fail_under` is 90 (coverage was 96% when set). Raise it as
  tests grow. `make test` doesn't measure coverage; `make cov` does.
- The table shows up in the PR comment and in the `test` job summary
  (Dependabot PRs get the summary only: their token can't comment).
- **Dashboard:** none for now (Codacy / SonarQube Cloud / Codecov
  skipped, see TASKS.md → PROJ-004).

**Local hooks**

- `make hooks` installs pre-commit: Ruff (lint + format), mypy, gitleaks,
  `uv lock --check`. A commit with a Ruff or mypy error is blocked.
- Keep the Ruff `rev` in `.pre-commit-config.yaml` in step with the Ruff
  version in `uv.lock`.
- `.claude/settings.json` (committed) runs Ruff on each Python file Claude
  Code edits and denies edits to `.env*` (this also covers `.env.example`;
  edit it by hand). Personal overrides go in `.claude/settings.local.json`
  (git-ignored).

**Branch rules on `main`**

- PR required, no direct push, no force push, no deletion.
- Required checks: `lint`, `typecheck`, `semgrep`, `test`, `docker-build`,
  `gitleaks`, `pip-audit`, `pr-title`. (`coverage-comment` is not required:
  it only reports.)
- On a private repo, rulesets and branch protection need **GitHub Pro**
  (or Team). Without that, these rules are convention only.

## Security scanning

Works on a private repo without GitHub Advanced Security (PROJ-003).

| What | Where | Fails on |
| --- | --- | --- |
| Secrets | gitleaks in CI (full history) and pre-commit | any finding |
| Dependencies | `pip-audit` in CI; Dependabot updates + alerts | any known CVE |
| Code | Ruff `S` (Bandit) rules, part of `ruff check`; Semgrep in CI (`semgrep`) | any finding |
| Image | Trivy in CI (`docker-build`) and `deploy.yml` before push | HIGH/CRITICAL with a fix available |

- **False positives**
  - Ruff: `# noqa: S608 -- <reason>` on the line. Tests ignore `S101`
    (asserts) in `pyproject.toml`.
  - gitleaks: add the finding's fingerprint to `.gitleaksignore` with a
    comment. A real leaked secret must be **rotated**, not ignored.
  - Trivy: add the ID to `.trivyignore` with a reason and a revisit date.
    Prefer a fix first.
- **Image hardening**: the `Dockerfile` runs `apt-get upgrade` and removes
  the system `pip`, which cleared the fixable HIGHs in `python:3.13-slim`.
  The upgrade layer is cached until the base image digest changes, so if
  Trivy goes red on a Debian fix that a rebuild doesn't pick up: wait for
  the upstream base image rebuild, or add a `.trivyignore` entry with a
  revisit date.
- **Dependabot skips** Postgres majors (need a data migration) and Python
  minor/major bumps (also touch `.python-version` / `requires-python`).
- **Dependabot** (`.github/dependabot.yml`): weekly on Monday for `uv`,
  Dockerfile, `docker-compose.yml` and GitHub Actions. Grouped: one
  minor/patch PR and one major PR per ecosystem at most.
- **Dependabot alerts** are a repo setting, not a file. Turn on once:
  Settings → Code security → *Dependabot alerts* and *Dependabot security
  updates*.
- **Version pins** to bump together:
  - gitleaks: `GITLEAKS_VERSION` in `ci.yml` and `rev` in
    `.pre-commit-config.yaml`
  - Trivy: `trivy-action` commit SHA and `version` in `ci.yml` and
    `deploy.yml` (pinned by SHA: its tags were hijacked once)
  - pip-audit: version in `ci.yml` and the `Makefile`
  - Semgrep: `SEMGREP_VERSION` in `ci.yml` and the `Makefile`

## Deploy

Push to `main` builds the image, pushes it to GHCR (`sha-<commit>` tag) and
deploys it to the droplet with Docker Compose
(`.github/workflows/deploy.yml` → `deploy/deploy.sh`). One-time droplet setup,
secrets, operations and rollback: [`docs/DEPLOY.md`](docs/DEPLOY.md).
