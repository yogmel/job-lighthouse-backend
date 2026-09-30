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

```sh
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

