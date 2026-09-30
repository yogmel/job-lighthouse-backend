"""The run pipeline for v0.4: steps 1–5 and 7 of SYSTEM_DESIGN → Job runner.

Scoring (step 6, v0.5) and the digest (step 8, v0.6) are not here yet.
Each company is committed on its own, so one company's result survives a
later company's failure.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .company_run import Fetcher, fetch_openings, run_company
from .models import Company, Run


async def run_pipeline(
    session: AsyncSession, run: Run, fetch: Fetcher = fetch_openings
) -> int:
    """Process every active company of ``run.user_id``. Returns new jobs."""
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
        await session.commit()
        new_jobs += len(outcome.new_job_ids)
    return new_jobs
