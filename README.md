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
```

- `DATABASE_URL` is required. Alembic reads it only from the environment.
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
| `test` | single Alembic head, `upgrade head` → `downgrade base` → `upgrade head`, `pytest` against a Postgres 17 service |
| `docker-build` | `docker build` (no push) |
| `pr-title` | PR title starts with `BE-`/`FE-`/`PROJ-` + number (`pr-title.yml`) |

Run the same checks locally: `make lint`, `make test`.

**Local hooks**

- `make hooks` installs pre-commit: Ruff (lint + format), gitleaks,
  `uv lock --check`. A commit with a Ruff error is blocked.
- Keep the Ruff `rev` in `.pre-commit-config.yaml` in step with the Ruff
  version in `uv.lock`.
- `.claude/settings.json` (committed) runs Ruff on each Python file Claude
  Code edits and denies edits to `.env*` (this also covers `.env.example`;
  edit it by hand). Personal overrides go in `.claude/settings.local.json`
  (git-ignored).

**Branch rules on `main`**

- PR required, no direct push, no force push, no deletion.
- Required checks: `lint`, `test`, `docker-build`, `pr-title`.
- On a private repo, rulesets and branch protection need **GitHub Pro**
  (or Team). Without that, these rules are convention only.

## Deploy

Push to `main` builds the image, pushes it to GHCR (`sha-<commit>` tag) and
deploys it to the droplet with Docker Compose
(`.github/workflows/deploy.yml` → `deploy/deploy.sh`). One-time droplet setup,
secrets, operations and rollback: [`docs/DEPLOY.md`](docs/DEPLOY.md).
