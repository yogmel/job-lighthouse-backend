# Job Lighthouse — Tasks

Tickets for [VERSIONING.md](./VERSIONING.md), broken down small. Planning doc
only — not synced to GitHub Issues.

IDs: `BE-` backend repo, `FE-` frontend repo, `PROJ-` cross-cutting/repo-less.

---

## v0.1 — Foundations

### ~~BE-001 · Set up migration tooling~~

**Target:** backend **Version:** v0.1

Pick and wire a migration framework (e.g. Alembic) against the shared
Postgres instance, usable by both services.

**Acceptance criteria:**

- `migrate up`/`down` commands run against a local Postgres
- Empty baseline migration committed

### ~~BE-002 · Users + PasswordResetToken schema~~

**Target:** backend **Version:** v0.1

Migration for `Users` and `PasswordResetToken` tables per SYSTEM_DESIGN.md.

**Acceptance criteria:**

- Both tables created with documented columns/types
- FK `PasswordResetToken.user_id -> Users.id`

### ~~BE-003 · Config schema~~

**Target:** backend **Version:** v0.1

Migration for `Config` (`keywords_include`, `keywords_exclude`, `location`,
`cron`, `profile`, `profile_version`).

**Acceptance criteria:**

- Table created, `user_id` FK present
- `keywords_include`/`keywords_exclude` stored as arrays

### ~~BE-004 · Companies schema~~

**Target:** backend **Version:** v0.1

Migration for `Companies`, `source` as `jsonb`.

**Acceptance criteria:**

- Table created with documented columns
- `source` column is `jsonb`, no app-layer validation yet (that's v0.3+)

### ~~BE-005 · Jobs schema~~

**Target:** backend **Version:** v0.1

Migration for `Jobs`.

**Acceptance criteria:**

- Table created with documented columns
- FK `Job.company_id -> Companies.id`

### ~~BE-006 · Runs + RunCompanyResult schema~~

**Target:** backend **Version:** v0.1

Migration for `Runs` and `RunCompanyResult`.

**Acceptance criteria:**

- Both tables created
- FKs `RunCompanyResult.run_id -> Runs.id`, `.company_id -> Companies.id`

### ~~BE-007 · Scaffold Job Runner Service~~

**Target:** backend **Version:** v0.1

FastAPI app skeleton: project layout, config loading (DB URL, secrets from
env), health-check endpoint.

**Acceptance criteria:**

- `GET /health` returns 200
- Connects to Postgres on startup, fails loudly if it can't

### ~~BE-008 · Scaffold Companies Service~~

**Target:** backend **Version:** v0.1

Same as BE-007, for the Companies Service.

**Acceptance criteria:**

- `GET /health` returns 200
- Connects to Postgres on startup, fails loudly if it can't

### ~~BE-009 · Docker Compose baseline~~

**Target:** backend **Version:** v0.1

Compose file: Postgres, Job Runner Service, Companies Service. Local `.env`
for secrets.

**Acceptance criteria:**

- `docker compose up` brings up all three, health checks pass
- Services read DB URL from compose-provided env vars

### ~~BE-010 · Nginx reverse proxy~~

**Target:** backend **Version:** v0.1

Nginx container in compose, routes by path prefix to each service.

**Acceptance criteria:**

- Requests to `/companies*`, `/auth*`, `/account*` route to Companies Service
- Requests to `/config*`, `/jobs*`, `/runs*` route to Job Runner Service
- Config is TLS-ready (cert paths wired, self-signed OK locally)

### ~~PROJ-001 · CI/CD: build + deploy to droplet~~

**Target:** project **Version:** v0.1

GitHub Actions workflow: build both service images on push to `main`,
deploy to the droplet via Docker Compose.

**Acceptance criteria:**

- Push to `main` produces a green deploy run
- Droplet is running the pushed commit's images after the workflow finishes

### ~~PROJ-002 · CI checks, branch rules, local hooks~~

**Target:** project **Version:** v0.1

GitHub Actions workflow on every PR and push to `main`, plus rules on
`main` so nothing merges red. Same checks run locally via pre-commit and
Claude Code hooks.

- **CI jobs:** Ruff lint + format check, `uv lock --check`, pytest against a
  Postgres service container, migration round-trip (`upgrade head` →
  `downgrade base` → `upgrade head`) with a single-head check, `docker build`
  (no push)
- **Branch rules on `main`:** PR required, CI required to pass, no force
  push, no direct push. Private repo: rulesets/branch protection need
  **GitHub Pro** (or Team) — upgrade, or record that they are convention only
- **pre-commit:** Ruff, `uv lock --check`, gitleaks (from PROJ-003); a
  `make hooks` target that installs them
- **Claude Code hooks** in committed `.claude/settings.json`: run Ruff on
  edited Python files, deny edits to `.env*`
- **PR hygiene:** PR template (`Closes #`, test checklist), check that the PR
  title starts with a ticket ID (`BE-`/`FE-`/`PROJ-`)

**Acceptance criteria:**

- A PR with a lint error, failing test, broken migration or two Alembic heads
  shows a red check and can't be merged
- `make hooks` installs pre-commit; a commit with a Ruff error is blocked
- CI runtime stays under ~5 minutes

### ~~PROJ-003 · Secret, dependency and image scanning~~

**Target:** project **Version:** v0.1

Security scans that work on a **private** repo without GitHub Advanced
Security (so no GitHub secret scanning / CodeQL).

- **Secrets:** gitleaks in CI (full history on first run) and in pre-commit
- **Dependencies:** Dependabot version updates (uv/pip, Docker, GitHub
  Actions) + Dependabot alerts; `pip-audit` in CI
- **Code:** Ruff `S` (Bandit) rules enabled, false positives ignored inline
  with a reason
- **Images:** Trivy scan of both service images in the PROJ-001 workflow,
  failing on HIGH/CRITICAL with a fix available

**Depends on:** PROJ-001 (Trivy step), PROJ-002 (CI workflow, pre-commit).

**Acceptance criteria:**

- A committed fake secret fails CI and is blocked by pre-commit
- Dependabot opens update PRs weekly, grouped to avoid noise
- A known-vulnerable dependency or image layer fails the check

---

## v0.2 — Auth & account basics

### ~~BE-011 · POST /auth/signup (email+password)~~

**Target:** backend **Version:** v0.2

Hash + store password, create `Users` row.

**Acceptance criteria:**

- Duplicate email returns a clear 409
- Password stored as a hash, never plaintext

### ~~BE-012 · POST /auth/login + JWT issuing~~

**Target:** backend **Version:** v0.2

Verify credentials, issue a signed JWT carrying `user_id`.

**Acceptance criteria:**

- Wrong password/unknown email returns 401, no user enumeration in the message
- Token verifiable with the shared signing secret

### ~~BE-013 · POST /auth/google~~

**Target:** backend **Version:** v0.2

Verify the Google ID token the frontend posts (from Google Identity
Services), find-or-create `Users` row by `google_id`, issue JWT.

**Acceptance criteria:**

- First-time Google login creates a `Users` row with `password_hash: null`
- Repeat login matches the existing row, doesn't duplicate

### ~~BE-014 · Shared JWT validation middleware~~

**Target:** backend **Version:** v0.2

Middleware/dependency, used by both services, validating the JWT locally
against the shared signing secret and injecting `user_id`.

**Acceptance criteria:**

- Missing/invalid/expired token returns 401 on any protected route
- Valid token makes `user_id` available to route handlers in both services

### ~~BE-015 · GET/PUT /account~~

**Target:** backend **Version:** v0.2

Fetch and update own credentials (email, password).

**Acceptance criteria:**

- `PUT` requires current password to change it
- Response never includes `password_hash`

### ~~FE-001 · Sign up page~~

**Target:** frontend **Version:** v0.2

Email/password form + "Sign up with Google" button.

**Acceptance criteria:**

- Successful signup redirects into the app, authenticated
- Server validation errors (e.g. duplicate email) surface inline

### ~~FE-002 · Log in page~~

**Target:** frontend **Version:** v0.2

Email/password form + Google login button.

**Acceptance criteria:**

- Successful login redirects into the app, authenticated
- Invalid credentials show an inline error

### ~~FE-003 · Session handling~~

**Target:** frontend **Version:** v0.2

Store the JWT, attach it to API calls to both services, protect
authenticated routes, redirect to login when missing/expired.

**Acceptance criteria:**

- Refreshing the page keeps the session until the token expires
- An expired/invalid token redirects to login instead of showing broken data

### ~~PROJ-004 · Type checking, SAST and quality dashboard~~

**Target:** project **Version:** v0.2

Deeper checks, timed for the auth work (first real logic and first
security-sensitive code).

- **Types:** mypy or pyright in "basic" mode in CI and pre-commit; tighten
  per module later
- **SAST:** Semgrep (Python + FastAPI rules) in CI — CodeQL needs GitHub
  Advanced Security on a private repo
- **Coverage:** pytest-cov in CI, reported on the PR; minimum threshold set
  once v0.2 lands
- **Quality dashboard:** pick one of Codacy / SonarQube Cloud / Codecov (or
  none) — check free-tier limits for private repos before choosing

**Depends on:** PROJ-002.

**Acceptance criteria:**

- Type and Semgrep checks are required on `main` and green on the current code
- Coverage percentage visible on every PR
- Dashboard decision recorded here (tool chosen or explicitly skipped)

**Decisions:**

- **Types:** mypy (pure Python, runs from the project venv in CI and
  pre-commit), default mode plus `check_untyped_defs`.
- **SAST:** Semgrep `p/python` + `p/fastapi`, run with pinned `uvx`.
- **Coverage:** pytest-cov with `fail_under = 90` (96% when set). Shown as a
  PR comment and in the `test` job summary.
- **Dashboard: skipped.** The PR coverage comment plus the CI checks cover
  what we need now, with no third-party service or extra secret. Revisit
  if we want coverage trends over time.

### v0.2 follow-ups

BE-011 – BE-015 are merged. Still to do:

- **Open questions:** see SYSTEM_DESIGN.md → Auth & accounts → Open
  questions. Update the code and that section once each is decided.
- ~~**`.env.example`:** add `JWT_SECRET` (required) and `GOOGLE_CLIENT_ID`
  (optional), matching the README env block.~~ Done.
- ~~**Running tests locally:** use `make test`. Plain `uv run pytest` doesn't
  load `.env`, so every DB test is skipped and the run still looks green.~~ Done: `make test` exists and README documents it.
- ~~**CORS (blocks the deployed frontend):** neither service adds CORS
  headers, and neither does Nginx. Browser calls from the Vercel origin fail
  their preflight check, including FE-001 and FE-002 signup and login. Add
  `CORSMiddleware` in the shared `create_app`. Read allowed origins from an
  env var (e.g. `CORS_ALLOWED_ORIGINS`), and allow the `Authorization` and
  `Content-Type` headers. Document the env var in `.env.example` and DEPLOY.md.~~ Done in #88 / #91.

---

## v0.3 — Manual company management

### ~~BE-016 · GET /companies~~

**Target:** backend **Version:** v0.3

List the authenticated user's companies.

**Acceptance criteria:**

- Only returns rows for the requesting `user_id`

### ~~BE-017 · POST /companies (manual)~~

**Target:** backend **Version:** v0.3

Create a company with an explicit `source` (`board` or `scraper` kind only),
no detection.

**Acceptance criteria:**

- Rejects a malformed `source` (wrong discriminant/missing fields) with 400
- `custom` kind rejected for now (v0.10)

### ~~BE-018 · PUT /companies/{id}~~

**Target:** backend **Version:** v0.3

Edit an existing company (name, tier, source, active).

**Acceptance criteria:**

- 404 if the company doesn't belong to the requesting user
- Partial updates supported (e.g. tier-only change)

### ~~FE-004 · Companies list screen~~

**Target:** frontend **Version:** v0.3

Table/list of tracked companies: name, tier, board/source kind, added date,
active state.

**Acceptance criteria:**

- Empty state shown when no companies exist yet

### ~~FE-005 · Manual add-company form~~

**Target:** frontend **Version:** v0.3

Form to add a company with an explicit source (board picker + board_id, or
scraper strategy + selectors).

**Acceptance criteria:**

- Submitting calls `POST /companies` and the new row appears in the list

### ~~FE-006 · Edit company dialog~~

**Target:** frontend **Version:** v0.3

Edit name/tier/source from the list.

**Acceptance criteria:**

- Changing tier shows the "re-groups jobs" note from the mockup

### ~~FE-007 · Pause/remove company actions~~

**Target:** frontend **Version:** v0.3

Row menu actions: pause (`active = false`) and remove.

**Acceptance criteria:**

- Pausing doesn't delete the row or its jobs
- Removing asks for confirmation before calling the API

---

## v0.4 — Runner pipeline (manual trigger)

### ~~BE-019 · Run lifecycle + advisory lock scaffolding~~

**Target:** backend **Version:** v0.4

Acquire `pg_try_advisory_lock`, open a `Runs` row (`status: running`), close
it `success`/`failed` when the pipeline finishes or throws.

**Acceptance criteria:**

- Lock held for the run's duration, released in a `finally`
- A run that throws mid-pipeline still closes the `Runs` row as `failed`

### ~~BE-020 · Fetch openings — board source~~

**Target:** backend **Version:** v0.4

Fetch current openings from a company's board API (Greenhouse/Lever/
Ashby/SmartRecruiters).

**Acceptance criteria:**

- Returns a normalized list (title, url, location) per board type
- Non-200/malformed response is treated as a fetch failure, not empty

### ~~BE-021 · Fetch openings — scraper source~~

**Target:** backend **Version:** v0.4

Fetch current openings via CSS-selector scraping (static + dynamic/
Playwright strategies).

**Acceptance criteria:**

- Selectors from `Source.selectors` are used, not hardcoded
- Selector/network failure is a fetch failure, not an empty result

### ~~BE-022 · Diff by URL + write new Jobs~~

**Target:** backend **Version:** v0.4

Compare fetched openings against stored `Jobs` by URL, insert new ones.

**Acceptance criteria:**

- Existing URLs are never re-inserted or duplicated
- New rows get `match_score`/`match_description` left unset (v0.5 fills them)

### ~~BE-023 · Close disappeared jobs~~

**Target:** backend **Version:** v0.4

Per company whose fetch succeeded, set `active = false` on stored jobs no
longer in current openings — success test differs by `source.kind` per the
table in SYSTEM_DESIGN.md.

**Acceptance criteria:**

- `board` kind: runs on any HTTP success, including empty `[]`
- `scraper` kind: only runs on a non-empty result
- A failed fetch never closes any jobs for that company

### ~~BE-024 · Write RunCompanyResult~~

**Target:** backend **Version:** v0.4

One row per company per run recording `status`/`jobs_found`/`error`.

**Acceptance criteria:**

- Every company in the run gets exactly one row, regardless of outcome

### ~~BE-025 · POST /runs (manual trigger)~~

**Target:** backend **Version:** v0.4

Wires BE-019 through BE-024 into one endpoint.

**Acceptance criteria:**

- Calling it while a run is already in flight no-ops (lock contention)
- Returns the created `Runs` row (at least its id/status)

### ~~BE-026 · GET /jobs~~

**Target:** backend **Version:** v0.4

List jobs with filters: active, company, tier.

**Acceptance criteria:**

- Only returns rows for the requesting `user_id`
- Filters are combinable (e.g. active + tier)

### ~~FE-008 · Jobs board screen~~

**Target:** frontend **Version:** v0.4

List view with active/company/tier filters.

**Acceptance criteria:**

- Empty state when no jobs match the current filters
- Manual "run now" action calls `POST /runs`

### v0.4 follow-ups

BE-019 – BE-026 are merged. Found while building them:

### PROJ-009 · Chromium in the Docker image for dynamic scrapes

**Target:** project **Version:** v0.4

The image has the `playwright` package but no browser, so every `scraper`
source with `strategy: "dynamic"` fails in production. It fails safe: the
company's `RunCompanyResult` is `failed` and no jobs are closed. Install
Chromium (`playwright install --with-deps chromium`) where the non-root
`app` user can read it.

**Acceptance criteria:**

- A `dynamic` scraper source fetches successfully in the deployed container
- The image still passes the Trivy HIGH/CRITICAL gate

### ~~BE-045 · POST /runs can outlive the Nginx proxy timeout~~

**Target:** backend **Version:** v0.4

`POST /runs` runs the pipeline inside the request, and Nginx's default
`proxy_read_timeout` (60s) applies to `/runs`. Many companies or slow
`dynamic` scrapes (up to ~40s each) can exceed it: the client gets a 504
while the run continues. Preferred fix: return 202 with the `running` row
and run the pipeline in a background task (BE-019's lifecycle already
allows it).

**Acceptance criteria:**

- A run longer than the proxy timeout still gives the client a usable response
- Lock contention still no-ops (409); a failing run still closes as `failed`

### ~~BE-046 · Paginate GET /jobs~~

**Target:** backend **Version:** v0.4

Jobs are never deleted, so `GET /jobs` grows without bound.

**Acceptance criteria:**

- Bounded `limit` plus `offset` or a `(date, id)` keyset cursor
- Works with the existing `active` / `company_id` / `tier` filters
- Response tells the client whether more rows exist (coordinate with FE-008)

---

## v0.5 — Match scoring

### ~~BE-027 · Config.profile + GET/PUT /config~~

**Target:** backend **Version:** v0.5

Add `profile`/`profile_version` to `Config`, expose read/write.

**Acceptance criteria:**

- `profile_version` increments on every `profile` change
- No history of past profile text is kept, per design

### ~~BE-028 · Match-scoring agent~~

**Target:** backend **Version:** v0.5

Pipeline step 6: score each new job against `Config.profile`, write
`match_score`/`match_description`/`profile_version`.

**Acceptance criteria:**

- Every job written by BE-022 in this run gets a score before step 7 commits
- `Job.profile_version` matches the `Config.profile_version` used to score it

### ~~FE-009 · Settings → Profile tab~~

**Target:** frontend **Version:** v0.5

Edit the markdown profile text.

**Acceptance criteria:**

- Saving shows the "applies to next run, already-scored jobs keep their
  score" note from the mockup

---

## v0.6 — Notifications

### ~~BE-029 · Transactional email client~~

**Target:** backend **Version:** v0.6

Thin wrapper around the chosen provider (Resend/Postmark/SES).

**Acceptance criteria:**

- Send succeeds/fails distinguishably (caller can tell if it must not stamp
  `notified_at`)

### ~~BE-030 · Digest send + notified_at stamping~~

**Target:** backend **Version:** v0.6

Pipeline step 8: send every job where `active = true AND notified_at IS
NULL`; stamp `notified_at` only after provider confirms success.

**Acceptance criteria:**

- Empty digest set sends no email
- A failed send leaves `notified_at` null so those jobs reappear next digest

### FE-010 · Settings → Notifications tab

**Target:** frontend **Version:** v0.6

Notification preferences screen — no Notion-mirror toggle (retired).

**Acceptance criteria:**

- Matches the mockup minus the stale Notion control

---

## v0.7 — Scheduling

### ~~BE-031 · Internal tick loop~~

**Target:** backend **Version:** v0.7

Background loop, wakes every minute, reads `Config.cron`, checks it against
the last `Runs.started_at` to decide if a run is due.

**Acceptance criteria:**

- Changing `Config.cron` changes behavior on the next tick, no restart needed

### ~~BE-032 · Wire advisory lock into scheduled runs~~

**Target:** backend **Version:** v0.7

Due ticks call the same run pipeline as manual `POST /runs`, sharing the
BE-019 lock so they can't overlap.

**Acceptance criteria:**

- A due tick during an in-flight manual run no-ops instead of double-running

### FE-011 · Settings → Schedule tab

**Target:** frontend **Version:** v0.7

Cron editor (including raw cron override per the mockup).

**Acceptance criteria:**

- Saving calls `PUT /config`, next-run estimate updates accordingly

---

## v0.8 — Agent-assisted onboarding

### ~~BE-033 · Deterministic ATS signature matcher~~

**Target:** backend **Version:** v0.8

Match a pasted careers URL/host against known signatures for Greenhouse,
Lever, Ashby, SmartRecruiters.

**Acceptance criteria:**

- A known-board URL resolves to `{kind: "board", board, board_id}` with no
  LLM call
- Unrecognized URL returns no match, doesn't throw

### ~~BE-034 · LLM selector-discovery fallback~~

**Target:** backend **Version:** v0.8

When BE-033 finds no match, an agent reads the page and proposes
`Selectors`.

**Acceptance criteria:**

- Returns a draft `Selectors` object when the agent finds a plausible list
- Returns "needs custom handling" when it can't (feeds v0.10's paused state)

### BE-035 · POST /companies/detect

**Target:** backend **Version:** v0.8

Wires BE-033 → BE-034, verifies with a live fetch, returns a draft `Source`
plus a scored sample of matched openings.

**Acceptance criteria:**

- Response sample jobs are scored using the existing v0.5 scoring path
- Confirming persists exactly the returned `Source`, no re-detection

### BE-036 · POST /companies/{id}/test

**Target:** backend **Version:** v0.8

Re-run a company's existing source, report reachability/job count.

**Acceptance criteria:**

- Reports failure distinctly from "reachable, zero jobs"

### FE-012 · Add-company: paste URL + resolving state

**Target:** frontend **Version:** v0.8

Paste-a-URL input, "agent resolving: Detected Greenhouse..." loading state.

**Acceptance criteria:**

- Matches the mockup's resolving copy/behavior

### FE-013 · Add-company: confirm + added banner

**Target:** frontend **Version:** v0.8

Confirm screen showing the scored sample; on confirm, success banner.

**Acceptance criteria:**

- Confirm calls the persist step and the company appears in FE-004's list

---

## v0.9 — Runs visibility

### BE-037 · GET /runs

**Target:** backend **Version:** v0.9

Run history for the dashboard.

**Acceptance criteria:**

- Ordered most-recent first
- Only returns the requesting user's runs

### BE-038 · GET /runs/{id}/companies

**Target:** backend **Version:** v0.9

Per-company breakdown for one run, from `RunCompanyResult`.

**Acceptance criteria:**

- 404 if the run doesn't belong to the requesting user

### FE-014 · Runs screen

**Target:** frontend **Version:** v0.9

Next run estimate, today's totals, run history, per-company breakdown.

**Acceptance criteria:**

- Per-company rows show ok/failed/skipped and job counts per the mockup

---

## v0.10 — Custom handler escape hatch

### BE-039 · Source.kind: "custom" support

**Target:** backend **Version:** v0.10

Extend the `Source` union/validation to accept `{kind: "custom", handler}`.

**Acceptance criteria:**

- `POST`/`PUT /companies` accept a `custom` source (previously rejected in
  BE-017)

### BE-040 · Pipeline support for custom handlers

**Target:** backend **Version:** v0.10

Steps 2/5 dispatch to a per-company handler function keyed by
`source.handler`; handler self-reports success/failure.

**Acceptance criteria:**

- A company with no implemented handler is treated as failed/skipped, not
  a crash
- Handler's own success/failure feeds `RunCompanyResult` and the step-5
  close-disappeared-jobs decision

### FE-015 · "Needs custom handling" paused state

**Target:** frontend **Version:** v0.10

Visual state on a company row when detection fell through to `custom` with
no handler yet.

**Acceptance criteria:**

- Row shows a distinct "needs custom handling" badge, stays paused until a
  developer ships the handler

---

## v0.11 — Account completeness

### BE-041 · POST /auth/password-reset/request

**Target:** backend **Version:** v0.11

Issue a `PasswordResetToken`, email the reset link.

**Acceptance criteria:**

- Unknown email still returns a generic success response (no enumeration)
- Token stored hashed, never the raw value

### BE-042 · POST /auth/password-reset/confirm

**Target:** backend **Version:** v0.11

Verify token, set new password, mark token used.

**Acceptance criteria:**

- Expired or already-used token is rejected
- Token can't be reused after a successful confirm

### BE-043 · GET /account/export

**Target:** backend **Version:** v0.11

JSON export of all rows owned by the requesting user across every table.

**Acceptance criteria:**

- Export includes Config, Companies, Jobs, Runs, RunCompanyResults for that
  user only

### BE-044 · DELETE /account

**Target:** backend **Version:** v0.11

Delete the account, cascading to all owned rows.

**Acceptance criteria:**

- Cascade removes Config, Companies, Jobs, Runs, RunCompanyResults,
  PasswordResetTokens for that user
- JWTs for the deleted user are rejected immediately after (or on next
  validation) even if not yet expired

### FE-016 · Forgot-password flow

**Target:** frontend **Version:** v0.11

Request-reset form + set-new-password form (from emailed link).

**Acceptance criteria:**

- Both request and confirm show a generic success message even on invalid
  input, matching BE-041's non-enumeration behavior

### FE-017 · Settings → Account tab (export/delete)

**Target:** frontend **Version:** v0.11

Change credentials (already v0.2), plus export and delete-account actions.

**Acceptance criteria:**

- Delete requires an explicit confirmation step before calling the API

---

## v1.0 — Cutover

### PROJ-005 · One-time company import script

**Target:** project **Version:** v1.0

Script importing `config.yaml`'s ~40 companies into `Companies`/`Source`.

**Acceptance criteria:**

- Every company in `config.yaml` gets a row with a correctly-typed `source`
- `type: smartrecruiters` and the ATS `type`s map to `board`; the rest
  (`google`, `deel`, `ebay`, `kleinanzeigen`, `aiven`, `bolt`, `betterstack`,
  and the `dynamic` entries) map to `custom` or `scraper` per their current
  `main.py`/`ats_api.py` handling

### PROJ-006 · Retire script-run CI workflows

**Target:** project **Version:** v1.0

Confirm `daily.yml`/`full-scan.yml` are fully retargeted to build/deploy
(from PROJ-001) and no longer invoke `main.py` directly.

**Acceptance criteria:**

- No workflow step runs the old script

### PROJ-007 · Decommission Gmail SMTP + Notion

**Target:** project **Version:** v1.0

Remove `notify/notion.py` and SMTP-based email code/config/secrets from the
old script's path.

**Acceptance criteria:**

- No remaining code path sends via Gmail SMTP or writes to Notion
- Old secrets (Gmail app password, Notion token) revoked/removed from repo
  and droplet

### PROJ-008 · Go-live checklist

**Target:** project **Version:** v1.0

Final cutover: confirm all v0.x tickets closed, run PROJ-005, disable the
old script's trigger, monitor first live scheduled run end-to-end.

**Acceptance criteria:**

- First post-cutover digest arrives from the new system, not the old script
- Old script's GitHub Actions trigger is disabled (not just unused)
