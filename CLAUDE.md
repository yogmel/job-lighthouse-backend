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
- Postgres (self-hosted in Docker Compose), migrations shared by both services
  (Alembic suggested, see BE-001)
- Playwright for `scraper` sources with `strategy: "dynamic"`
- Transactional email API (Resend / Postmark / SES — not yet chosen)
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
  Auth per request. The token carries `user_id`.

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
  Pausing a company sets its jobs inactive; resuming does **not** restore them.
- Jobs are never deleted (except by account deletion).

**Scoring**
- Each scored job stores `profile_version`. Editing the profile bumps the
  version and does **not** rescore stored jobs.
- No history of past profile texts.

**Notifications**
- Digest = all jobs with `active = true AND notified_at IS NULL`, not "jobs
  from this run".
- Send nothing when that set is empty.
- Stamp `notified_at` only after the provider confirms success.
- Email is sent inline. No queue, worker, or outbox.

**Scheduling**
- Tick every minute, read `Config.cron`, compare against the last
  `Runs.started_at`. Don't register cron jobs at boot.
- Scheduled and manual runs share the same pipeline and the same lock.

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

Only a uv skeleton exists (`pyproject.toml`, empty `src/__init__.py`). Nothing
from v0.1 is built yet. The `[project.scripts]` entry points to
`job_lighthouse_backend:main`, which doesn't exist — expect to replace it
when scaffolding the services (BE-007 / BE-008).
