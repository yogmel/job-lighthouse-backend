"""Sync a company's fetched openings into ``Jobs``.

Same URL = same job, per user: the ``(user_id, url)`` unique constraint is the
diff. Nothing here commits; the pipeline owns the transaction.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Company, Job
from .openings import Opening


async def insert_new_jobs(
    session: AsyncSession, company: Company, openings: list[Opening]
) -> list[uuid.UUID]:
    """Insert openings whose URL the user has no job for yet.

    Returns the new jobs' ids. An existing URL is left untouched, even if it
    belongs to another of the user's companies or its title changed.
    Scoring fields stay null (v0.5 fills them).
    """
    # Same URL twice in one fetch is one job; keep the first.
    unique: dict[str, Opening] = {}
    for opening in openings:
        unique.setdefault(opening.url, opening)
    if not unique:
        return []
    rows = [
        {
            "user_id": company.user_id,
            "company_id": company.id,
            "company": company.name,
            "title": o.title,
            "url": o.url,
            "location": o.location,
            "description": o.description,
        }
        for o in unique.values()
    ]
    stmt = (
        insert(Job)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_jobs_user_id_url")
        .returning(Job.id)
    )
    return list(await session.scalars(stmt))
