"""One company's part of a run, always ending in one ``RunCompanyResult``.

Fetch → insert new jobs (BE-022) → close/reopen (BE-023) → result row.

- A ``custom`` source runs its handler (see ``handlers``). One with no
  handler shipped yet is ``skipped``.
- A failed fetch is ``failed`` and touches no jobs.
- A DB error while syncing rolls back that company's job changes (savepoint)
  and is ``failed``; the result row is still written.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.companies.sources import (
    BoardSource,
    ScraperSource,
    Source,
)

from .boards import fetch_board
from .handlers import NoHandlerError, fetch_custom
from .models import Company, RunCompanyResult
from .openings import FetchError, Opening
from .scraper import fetch_scraper
from .sync import insert_new_jobs, sync_active

logger = logging.getLogger(__name__)

ResultStatus = Literal["ok", "failed", "skipped"]

# Synchronous; run in a worker thread.
Fetcher = Callable[[Source], list[Opening]]

_source_adapter: TypeAdapter[Source] = TypeAdapter(Source)


def fetch_openings(source: Source) -> list[Opening]:
    """Dispatch to the fetcher for ``source.kind``."""
    if isinstance(source, BoardSource):
        return fetch_board(source)
    if isinstance(source, ScraperSource):
        return fetch_scraper(source)
    return fetch_custom(source)


@dataclass(frozen=True)
class CompanyOutcome:
    status: ResultStatus
    # Distinct openings the fetch returned (0 unless ``ok``).
    jobs_found: int = 0
    new_job_ids: tuple[uuid.UUID, ...] = ()
    error: str | None = None


async def run_company(
    session: AsyncSession,
    run_id: uuid.UUID,
    company: Company,
    fetch: Fetcher = fetch_openings,
) -> CompanyOutcome:
    """Process ``company`` for ``run_id`` and add its result row.

    Flushes but doesn't commit. Never raises for a fetch or sync failure:
    those become a ``failed`` row.
    """
    outcome = await _outcome(session, company, fetch)
    session.add(
        RunCompanyResult(
            run_id=run_id,
            company_id=company.id,
            status=outcome.status,
            jobs_found=outcome.jobs_found,
            error=outcome.error,
        )
    )
    await session.flush()
    return outcome


async def fetch_company(
    company: Company, fetch: Fetcher = fetch_openings
) -> list[Opening] | CompanyOutcome:
    """Fetch ``company``'s openings without touching the DB.

    Returns the openings, or the ``failed`` / ``skipped`` outcome when there
    are none to use. Never raises for a fetch failure.
    """
    try:
        source = _source_adapter.validate_python(company.source)
    except ValidationError:
        return CompanyOutcome("failed", error="stored source is invalid")

    try:
        return await asyncio.to_thread(fetch, source)
    except NoHandlerError as exc:
        return CompanyOutcome("skipped", error=str(exc))
    except FetchError as exc:
        return CompanyOutcome("failed", error=str(exc) or "fetch failed")
    except Exception as exc:
        # A bug in a fetcher must not take the other companies down.
        logger.exception("Unexpected fetch error for company %s", company.id)
        return CompanyOutcome("failed", error=f"unexpected error: {type(exc).__name__}")


async def _outcome(
    session: AsyncSession, company: Company, fetch: Fetcher
) -> CompanyOutcome:
    openings = await fetch_company(company, fetch)
    if isinstance(openings, CompanyOutcome):
        return openings

    try:
        async with session.begin_nested():
            new_ids = await insert_new_jobs(session, company, openings)
            await sync_active(session, company, openings)
    except SQLAlchemyError as exc:
        logger.exception("Saving jobs failed for company %s", company.id)
        return CompanyOutcome(
            "failed", error=f"saving jobs failed: {type(exc).__name__}"
        )
    return CompanyOutcome(
        "ok", jobs_found=len({o.url for o in openings}), new_job_ids=tuple(new_ids)
    )
