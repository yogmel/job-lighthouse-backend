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

```sh
make run-job-runner   # needs DATABASE_URL (from .env) and a running Postgres
curl localhost:8001/health
make test
```

- Settings come from env vars only. Required: `DATABASE_URL`.
- On startup each service runs `SELECT 1`. If Postgres is unreachable it
  logs the error and exits non-zero.
