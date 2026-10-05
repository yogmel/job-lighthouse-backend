# Job Lighthouse

Tracks job openings at companies a user chooses, scores each new job against
the user's profile, and emails a digest of new open jobs.

## Language

### Companies

**Company**:
An employer one user tracks, with the source its openings are fetched from. Two users tracking the same employer have two separate Companies.
_Avoid_: Employer, tracked company

**Paused company**:
A Company the user has stopped tracking for now. It is skipped by runs, and its Jobs keep the state they had when it was paused.
_Avoid_: Deactivated company, inactive company

**Deleted company**:
A Company the user has removed for good, together with every Job found for it. Nothing of it remains.
_Avoid_: Removed company, archived company

### Jobs

**Job**:
One posting found for a Company, identified by its URL.
_Avoid_: Opening (except for the raw fetched result), posting, listing

**Open job**:
A Job whose posting was still listed the last time its Company was fetched successfully. A Job becomes closed only when a successful fetch no longer lists it.
_Avoid_: Active job (when meaning anything but "posting still open")

### Runs

**Run**:
One pass that fetches openings for a user's Companies, stores new Jobs, closes Jobs no longer listed, and sends the Digest. Only one Run per user at a time.
_Avoid_: Scrape, sync, job run

**Full run**:
A Run over all of the user's Companies that are not paused. Scheduled Runs are always Full runs.
_Avoid_: Normal run, complete run

**Single-company run**:
A manual Run over one Company the user picks. It does not count toward the schedule.
_Avoid_: Company run, partial run

**Digest**:
The email listing the user's Open jobs, from Companies that are not paused, that no earlier Digest has included.
_Avoid_: Notification, newsletter
