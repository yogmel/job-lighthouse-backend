"""The run pipeline: steps 1–8 of SYSTEM_DESIGN → Job runner.

Each company is committed on its own, so one company's result survives a
later company's failure. Its new jobs are scored (step 6) before that commit.
The digest (step 8) goes out last, once every company is committed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.email import Mailer

from .company_run import Fetcher, fetch_openings, run_company
from .digest import send_digest
from .models import Company, Run
from .scoring import Scorer, load_profile, score_jobs


async def run_pipeline(
    session: AsyncSession,
    run: Run,
    fetch: Fetcher = fetch_openings,
    scorer: Scorer | None = None,
    mailer: Mailer | None = None,
) -> int:
    """Process every active company of ``run.user_id``. Returns new jobs.

    Without a ``scorer`` or a profile, new jobs are stored unscored. Without
    a ``mailer``, no digest is sent and ``notified_at`` stays null.
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
    if mailer is not None:
        await send_digest(session, run.user_id, mailer)
        await session.commit()
    return new_jobs
