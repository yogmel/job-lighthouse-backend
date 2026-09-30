"""Sync a company's fetched openings into ``Jobs``.

Same URL = same job, per user: the ``(user_id, url)`` unique constraint is the
diff. Nothing here commits; the pipeline owns the transaction.
"""

import uuid

from sqlalchemy import ColumnElement, update
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


def can_close(source_kind: str, openings: list[Opening]) -> bool:
    """Whether a *successful* fetch is proof enough to close missing jobs.

    - ``board``: yes, even when empty. A 200 with ``[]`` means zero roles.
    - ``scraper``: only when non-empty. Empty may just be broken selectors.
    - ``custom``: yes. The handler reports failure by raising.

    A failed fetch raises before this point, so it never closes anything.
    """
    if source_kind == "scraper":
        return bool(openings)
    return source_kind in ("board", "custom")


async def sync_active(
    session: AsyncSession, company: Company, openings: list[Opening]
) -> tuple[int, int]:
    """Match the company's stored jobs' ``active`` flag to a successful fetch.

    - Closes active jobs whose URL is gone, if ``can_close`` allows it.
    - Reopens inactive jobs whose URL is listed again (e.g. after the company
      was paused and resumed). ``notified_at`` is left as is.

    Only this company's jobs are touched. Returns ``(closed, reopened)``.
    """
    urls = {o.url for o in openings}
    mine = (Job.user_id == company.user_id, Job.company_id == company.id)
    closed = reopened = 0
    if can_close(company.source["kind"], openings):
        closed = await _set_active(
            session, False, *mine, Job.active.is_(True), Job.url.not_in(urls)
        )
    if urls:
        reopened = await _set_active(
            session, True, *mine, Job.active.is_(False), Job.url.in_(urls)
        )
    return closed, reopened


async def _set_active(
    session: AsyncSession, active: bool, *where: ColumnElement[bool]
) -> int:
    ids = await session.scalars(
        update(Job).where(*where).values(active=active).returning(Job.id)
    )
    return len(ids.all())
