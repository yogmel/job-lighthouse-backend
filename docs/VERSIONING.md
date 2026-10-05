# Job Lighthouse — Versioning

Slices [SYSTEM_DESIGN.md](./SYSTEM_DESIGN.md) into buildable increments. Each
version is a vertical slice (backend + matching frontend where relevant) so
the app stays runnable/testable at every step. Ordered by dependency, not
just feature priority.

`v0.x` = pre-launch build. `v1.0` = cutover, replaces the current script in
production.

---

## v0.1 — Foundations

Infra only, nothing user-facing.

- Postgres schema: `Users`, `PasswordResetToken`, `Config`, `Companies`,
  `Jobs`, `Runs`, `RunCompanyResult`
- Job Runner Service + Companies Service scaffolded (FastAPI), empty routes
- Docker Compose: both services + Postgres + Nginx reverse proxy
- GitHub Actions: build + deploy to the droplet on push to `main`

**Depends on:** nothing.

---

## v0.2 — Auth & account basics

- `POST /auth/signup` (email+password or Google), `POST /auth/login`,
  `POST /auth/google`
- JWT issuing + local per-service validation (shared signing secret)
- `GET/PUT /account` (change credentials)
- Frontend: sign up / log in pages

**Excludes:** password reset (v0.11), export/delete (v0.11).
**Depends on:** v0.1.

---

## v0.3 — Manual company management

- `GET /companies`, `POST /companies` (explicit `Source`, no detection),
  `PUT /companies/{id}`
- Frontend: Companies list, manual add-company form, edit dialog, pause/remove

**Excludes:** agent-assisted add (v0.8), `custom` source kind (v0.10).
**Depends on:** v0.2 (rows are `user_id`-scoped).

---

## v0.4 — Runner pipeline (manual trigger)

- `POST /runs` (manual only, no cron yet)
- Pipeline steps 0–5, 7: lock, fetch openings (`board`/`scraper` kinds only),
  diff by URL, close disappeared jobs, write `Jobs`, write `RunCompanyResult`
- `GET /jobs`
- Frontend: Jobs board (list, filter by active/company/tier)

**Excludes:** match scoring (step 6 — v0.5), notifications (step 8 — v0.6),
`custom` source kind (v0.10).
**Depends on:** v0.3.

---

## v0.5 — Match scoring

- `Config.profile` / `profile_version`, `GET/PUT /config`
- Step 6: LLM match-scoring agent, writes `Job.match_score` /
  `match_description` / `profile_version`
- Frontend: Settings → Profile tab

**Depends on:** v0.4.

---

## v0.6 — Notifications

- Transactional email API integration (Resend/Postmark/SES)
- Step 8: digest send (`active = true AND notified_at IS NULL`), stamp
  `notified_at` only on confirmed send
- Frontend: Settings → Notifications tab (no Notion toggle — retired)

**Depends on:** v0.5 (digest reads scored jobs).

---

## v0.7 — Scheduling

- Internal tick loop (every minute), `pg_try_advisory_lock`
- `Config.cron` drives due-check against `Runs.started_at`
- Frontend: Settings → Schedule tab

**Depends on:** v0.4 (automates the trigger v0.4 built manually).

---

## v0.8 — Agent-assisted onboarding

- `POST /companies/detect`: deterministic ATS signature match
  (Greenhouse/Lever/Ashby/SmartRecruiters) → LLM selector-discovery fallback
  → draft `Source` + scored sample
- `POST /companies/{id}/test`
- Frontend: Add-company flow (paste URL → agent resolving → confirm screen)

**Depends on:** v0.3 (companies), v0.5 (reuses the agent/profile scoring path
for the confirm-screen sample).

---

## v0.9 — Runs visibility

- `GET /runs`, `GET /runs/{id}/companies`
- Frontend: Runs screen (next run, totals, history, per-company breakdown)

**Depends on:** v0.7 (there's a schedule to show), v0.4 (`RunCompanyResult`
already populated).

---

## v0.10 — Custom handler escape hatch

- `Source.kind: "custom"`, dev-implemented per-company handlers
- Pipeline step 2/5 support for `custom` (handler self-reports
  success/failure)
- Frontend: "needs custom handling" paused state on a company row

**Depends on:** v0.4, v0.8 (detect flow's third fallback surfaces this state).

---

## v0.11 — Account completeness

- `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm`
- `GET /account/export`, `DELETE /account` (cascade)
- Frontend: forgot-password flow, Settings → Account tab (export/delete)

**Depends on:** v0.2.

---

## v1.0 — Cutover

Go-live. Replaces the current script in production.

- Import `config.yaml`'s ~40 companies into `Companies`/`Source`
- Retire `daily.yml`/`full-scan.yml` script-run workflows (already retargeted
  to build/deploy since v0.1)
- Decommission Gmail SMTP + Notion integration
- SQLite dedup history **not migrated** — one accepted "everything's new"
  digest post-cutover

**Depends on:** every version above.

Also in v1.0, user-facing fixes from spec #126:

- Delete a company (its jobs go too; run history stays)
- Pausing a company leaves its jobs alone; paused companies' jobs skip the digest
- Frontend job list pages past the first 50 via the cursor

---

## v1.1 — Company and run controls

- Total job count on the first page of `GET /jobs` (`X-Total-Count`)
- Single-company run (`POST /runs { company_id }`), not counted by the scheduler

**Depends on:** v1.0.

---

## Deferred indefinitely (not versioned)

Explicitly out of scope per SYSTEM_DESIGN.md — no version slot until a real
need forces the question:

- Firebase Auth swap (v1 auth is hand-rolled by design)
- Caching layer (`GET /jobs` response cache, scrape-result cache)
- Content diffing on unchanged-URL jobs
- Rescore-on-profile-edit
- Shared company catalog across users (multi-user dedup)
- Email-verification gate
