# Job Lighthouse — Backend

Two Python services on Postgres that track job openings at chosen companies.
Users add companies, the runner fetches their openings on a schedule, an LLM
scores each new job against the user's profile, and a digest email goes out.

Frontend (Next.js on Vercel) lives in a separate repo. Tickets prefixed
`FE-` belong there.

## Docs — read before designing anything

- `docs/SYSTEM_DESIGN.md` — **source of truth** for schemas, API, pipeline,
  and the reasoning behind each decision. If code and this doc disagree, ask
  before changing either.
- `docs/VERSIONING.md` — build order (v0.1 → v1.0) and what each version
  excludes. Don't build features from a later version early.
- `docs/TASKS.md` — tickets (`BE-`, `FE-`, `PROJ-`) with acceptance criteria.
  Reference the ticket ID in branches/commits/PRs.

## Stack

- Python 3.13, managed with **uv** (`uv_build` backend)
- FastAPI for both services
- Postgres (self-hosted in Docker Compose), Alembic migrations shared by
  both services, SQLAlchemy async
- Playwright for `scraper` sources with `strategy: "dynamic"`
- OpenAI for match scoring and selector discovery
- Resend for transactional email
- Deploy: Docker Compose on one DigitalOcean droplet, Nginx in front,
  GitHub Actions builds and deploys on push to `main`

## Architecture

Two services behind Nginx. Everything else is a module, not a deploy.

| Service | Routes | Owns |
| --- | --- | --- |
| **Job Runner** | `/config`, `/jobs`, `/runs` | `Config`, `Jobs`, `Runs`, `RunCompanyResult`, the cron tick loop, the run pipeline |
| **Companies** | `/companies`, `/auth`, `/account` | `Companies`, `Users`, `PasswordResetToken`, auth, onboarding/detection |

- **Do not** split Auth or the scheduler into their own services.
- Both services validate JWTs **locally** with a shared secret. No calls to
  Auth per request. The token carries `user_id`. Each request also checks
  that the user row still exists (one DB lookup), so a deleted account's
  tokens stop working at once.

## Rules that are easy to break

**Tenancy**
- Every user-owned query filters by `user_id` from the JWT. Accessing another
  user's row returns **404**, not 403.
- No sharing or dedup across users. Two users tracking the same company get
  two rows and two scrapes.

**Source**
- `Companies.source` is one `jsonb` column holding a discriminated union on
  `kind`: `board` | `scraper` | `custom`. Validate it in the app layer.
- `Board` = `lever` | `greenhouse` | `ashby` | `smartrecruiters`.
- `careers_url` lives inside `Selectors`, not on the company.

**Run pipeline** (see SYSTEM_DESIGN.md → Job runner service)
- Take `pg_try_advisory_lock` first. If it fails, no-op. Release in `finally`.
  Never use a "running" boolean column.
- A run that throws must still close its `Runs` row as `failed`.
- Same URL = same job. No content diffing, no re-insert.
- **A failed fetch must never close jobs.** Close disappeared jobs only when:
  - `board`: any HTTP success, including empty `[]`
  - `scraper`: only a **non-empty** result
  - `custom`: only when the handler reports success
- Write exactly one `RunCompanyResult` per company per run, whatever the outcome.
- `Job.active` means "posting is still open". It is not a user dismiss flag.
  Pausing or resuming a company changes no job.
- Jobs are never deleted, except by account deletion or by deleting their
  Company (which takes the run lock and returns 409 during a run; ADR 0001).

**Scoring**
- Each scored job stores `profile_version`. Editing the profile bumps the
  version and does **not** rescore stored jobs.
- No history of past profile texts.

**Notifications**
- Digest = all jobs with `active = true AND notified_at IS NULL` whose company
  isn't paused, not "jobs from this run".
- Send nothing when that set is empty.
- Stamp `notified_at` only after the provider confirms success.
- Email is sent inline. No queue, worker, or outbox.

**Scheduling**
- Tick every minute, read `Config.cron`, compare against the last
  `Runs.started_at`. Don't register cron jobs at boot.
- Scheduled and manual runs share the same pipeline and the same lock.
- Single-company runs (`scope = "company"`) don't count for the due check:
  it looks only at `scope = "all"` runs.

**Onboarding (`POST /companies/detect`)**
- Deterministic ATS URL match first. Call the LLM only when no board matches.
- If the LLM can't find selectors either, mark the company as needing a
  `custom` handler (paused), don't error.

**Auth & security**
- Hash passwords. Store only hashes of reset tokens.
- Login and reset endpoints must not reveal whether an email exists.
- Never return `password_hash` in a response.
- Secrets come from env vars only. Never hardcode or commit them.
- Don't log or print user PII (emails, profile text) beyond what's needed.

## Out of scope — don't build unless asked

Caching / Redis, Firebase Auth, content diffing, rescore on profile edit,
shared company catalog, email-verification gate. See VERSIONING.md →
"Deferred indefinitely".

## Current state

Backend tickets for v0.1 – v0.11 are built; v1.0 (cutover) is next. Struck
tickets in `docs/TASKS.md` are done. Setup, commands, env vars, code layout,
CI and deploy are in `README.md` — read it rather than guessing a command.

## Working in this repo

- **Ticket workflow.** One branch and PR per ticket (or tightly linked
  pair), named after it. The required `pr-title` check fails unless the
  title starts with a ticket ID. Strike the ticket in `docs/TASKS.md` and
  update `docs/SYSTEM_DESIGN.md` in the same PR. Put `Closes #<issue>` in
  the body. `/ticket <ID>` or `/ticket #<issue>` runs this whole flow.
- **DB tests skip silently** without `DATABASE_URL`. A green `uv run pytest`
  with skips proves nothing: use `make test` / `make cov` (they load
  `.env`), or `set -a; . ./.env; set +a` first. In a new git worktree,
  `.env` isn't there; ask the user to copy it.
- **`.env*` files can't be edited** by Claude (denied in
  `.claude/settings.json`). Ask the user to change `.env.example`.
- **`tests/test_cors.py` reloads the service modules**, which replaces
  `main.app`. Set dependency overrides on `companies_client.app` /
  `runner_client.app`, not on an imported `app`.
- **Semgrep flags log calls** whose message mentions passwords or tokens,
  even with nothing secret in them. Reword the message.
- **Before pushing:** `make lint typecheck cov`. CI also runs Semgrep,
  gitleaks, pip-audit and a Trivy image scan (see `README.md` → CI & hooks).

## Agent skills

### Issue tracker

GitHub Issues on `yogmel/job-lighthouse-backend`, mirrored in `docs/TASKS.md`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.
