---
name: ticket
description: Take a ticket ID (e.g. BE-045) from latest main to an open PR — branch, implement, check, push, open PR.
argument-hint: <TICKET-ID | #issue> [extra notes]
disable-model-invocation: true
---

Ticket: $ARGUMENTS

Do every step without stopping to ask, unless the ticket is unclear or
conflicts with `docs/SYSTEM_DESIGN.md` (CLAUDE.md says to ask then).

## 0. Resolve the ticket

The argument is either a ticket ID (`BE-045`) or a GitHub issue
(`#127` or `127`).

- **Issue number:** `gh issue view <N> --json number,title,body,state`.
  The ticket ID is the start of the title (`BE-058 · Single-company run`).
  Use the issue body as extra context. If the issue is closed, stop and say so.
- **Ticket ID:** find the issue with
  `gh issue list --state open --search "<ID> in:title" --json number,title`.
  If there is none, keep going and say so in the final reply.

You now have both the ticket ID and the issue number (if any).

## 1. Branch from latest main

- `git status` must be clean. If not, stop and tell the user.
- `git fetch origin`
- `git switch -c <type>/<ID>-<short-slug> origin/main`
  - `<type>`: `feat`, `fix`, `docs`, `chore` or `refactor`
  - Example: `feat/BE-044-account-delete`

## 2. Read the ticket

- Find the ticket in `docs/TASKS.md` and read its acceptance criteria.
- Read the parts of `docs/SYSTEM_DESIGN.md` and `docs/VERSIONING.md` it touches.

## 3. Implement

- Use `/tdd` where possible, at clear seams.
- Run `make typecheck` and single test files often while working.
- Run the full suite once at the end with `make cov`.
- Use `/code-review` to review the work, and fix what it finds.

## 4. Update docs

- Strike the ticket in `docs/TASKS.md`.
- Update `docs/SYSTEM_DESIGN.md` to match what was built.

## 5. Check

- `make lint typecheck cov`. Fix failures and re-run until green.
- If DB tests skipped, the run proves nothing. Make sure `.env` is loaded.

## 6. Commit, push, open PR

- Commit message starts with the ticket ID: `<ID>: <summary>`.
- `git push -u origin HEAD`
- `gh pr create --base main --title "<ID>: <summary>" --body ...`
  - Body: short summary, test notes, and `Closes #<issue>`.
  - End the body with the attribution line from the system reminder.

## 7. Report

Reply with the PR URL and one line per notable decision or open question.
