# Deleting a company deletes its jobs but keeps its run history

Jobs used to be deleted only by account deletion. Deleting a Company now
hard-deletes all of its Jobs too, while its `Runs` and `RunCompanyResult` rows
stay, with `company_id` set to null and the company name as it was at run time.
Users expect a removed company's jobs to vanish, but run history is the audit
trail of what the runner did, so it outlives the company.

## Considered Options

- **Keep the jobs with a null `company_id`.** Rejected: jobs are unique per
  `(user, url)`, so re-adding the same company would find its postings blocked
  by the orphan rows. It would need an "adopt orphans" rule, and the orphans
  would stay open forever because nothing fetches them again.
- **Soft delete (`deleted_at`).** Rejected: every company query would need the
  filter, for a restore feature nobody asked for.
- **Delete run history with the company.** Rejected: the user wants past runs
  to stay readable.

## Consequences

- `Runs.company_id` (single-company runs) can be null for two reasons, so the
  scheduler tells full runs apart by `Runs.scope`, not by a null `company_id`.
- A delete takes the per-user run lock and returns 409 while a run is in
  progress, so a run never inserts jobs for a company that is gone.
