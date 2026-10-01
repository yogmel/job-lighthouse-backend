"""The run pipeline: steps 1–7 of SYSTEM_DESIGN → Job runner.

The digest (step 8, v0.6) is not here yet. Each company is committed on its
own, so one company's result survives a later company's failure. Its new
jobs are scored (step 6) before that commit.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .company_run import Fetcher, fetch_openings, run_company
from .models import Company, Run
from .scoring import Scorer, load_profile, score_jobs


async def run_pipeline(
    session: AsyncSession,
    run: Run,
    fetch: Fetcher = fetch_openings,
    scorer: Scorer | None = None,
) -> int:
    """Process every active company of ``run.user_id``. Returns new jobs.

    Without a ``scorer`` or a profile, new jobs are stored unscored.
    """
    # Read once: a profile edit mid-run applies to the next run, not this one.
    profile = await load_profile(session, run.user_id)
    companies = (
        await session.scalars(
            select(Company)
            .where(Company.user_id == run.user_id, Company.active.is_(True))
            .order_by(Company.added_at, Company.id)
        )
    ).all()
    new_jobs = 0
    for company in companies:
        outcome = await run_company(session, run.id, company, fetch)
        if scorer is not None and profile is not None:
            await score_jobs(session, outcome.new_job_ids, profile, scorer)
        await session.commit()
        new_jobs += len(outcome.new_job_ids)
    return new_jobs
