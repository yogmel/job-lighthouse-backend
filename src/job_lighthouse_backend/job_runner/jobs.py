"""/jobs: the caller's stored job postings.

Every query is scoped to the ``user_id`` in the JWT.

Sorted by the company's tier ascending, then ``date`` newest first, then ``id``
descending. Paged with a keyset cursor on ``(tier, date, id)``, that same
order, so a page can cross a tier boundary. Runs add jobs to the list, so an
offset would skip or repeat rows between pages; a cursor doesn't. The body
stays a plain list: the next page's cursor comes in the ``X-Next-Cursor``
header, absent on the last page. The first page (no cursor) also carries
``X-Total-Count``, the number of jobs matching the filters, and
``X-Tier-Counts``, the same count per tier as a JSON object keyed by tier;
later pages skip both.
"""

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session

from .models import Company, Job

router = APIRouter(prefix="/jobs", tags=["jobs"])

Session = Annotated[AsyncSession, Depends(get_session)]

NEXT_CURSOR_HEADER = "X-Next-Cursor"
TOTAL_COUNT_HEADER = "X-Total-Count"
TIER_COUNTS_HEADER = "X-Tier-Counts"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(tier: int, date: datetime, job_id: uuid.UUID) -> str:
    """Opaque cursor for the page after the job ``(tier, date, job_id)``."""
    raw = f"{tier}|{date.isoformat()}|{job_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[int, datetime, uuid.UUID]:
    """Inverse of ``encode_cursor``. Raises ``ValueError`` on a bad cursor."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeError) as exc:
        raise ValueError("not base64") from exc
    tier_text, _, rest = raw.partition("|")
    date_text, _, id_text = rest.partition("|")
    date = datetime.fromisoformat(date_text)
    if date.tzinfo is None:
        raise ValueError("date has no timezone")
    return int(tier_text), date, uuid.UUID(id_text)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    url: str
    location: str
    description: str
    company_id: uuid.UUID
    # Display name at scrape time; may drift after a rename.
    company: str
    # Null until scored (v0.5).
    match_score: float | None
    match_description: str | None
    profile_version: int | None
    date: datetime
    notified_at: datetime | None
    active: bool
    # The company's current state, not stored on the job.
    company_active: bool


@router.get(
    "",
    response_model=list[JobOut],
    responses={
        status.HTTP_200_OK: {
            "headers": {
                NEXT_CURSOR_HEADER: {
                    "description": "Pass as `cursor` for the next page."
                    " Absent on the last page.",
                    "schema": {"type": "string"},
                },
                TOTAL_COUNT_HEADER: {
                    "description": "Jobs matching the filters. Only when no"
                    " `cursor` is given.",
                    "schema": {"type": "integer"},
                },
                TIER_COUNTS_HEADER: {
                    "description": "Jobs matching the filters per tier, as a"
                    ' JSON object keyed by tier, e.g. `{"1": 12, "2": 5}`.'
                    " Tiers with no match are omitted. Only when no `cursor`"
                    " is given.",
                    "schema": {"type": "string"},
                },
            }
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Bad cursor"},
    },
)
async def list_jobs(
    user_id: CurrentUserId,
    session: Session,
    response: Response,
    active: bool | None = None,
    company_id: uuid.UUID | None = None,
    tier: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> list[JobOut]:
    """The caller's jobs, by company tier ascending then newest ``date`` first,
    at most ``limit`` per page.

    Filters are optional and combine with AND. With no ``active`` filter,
    closed postings are included. Another user's ``company_id`` matches
    nothing, so it returns ``[]``.

    Keep the same filters when following ``X-Next-Cursor``. Without a
    ``cursor``, ``X-Total-Count`` is the number of jobs matching the filters and
    ``X-Tier-Counts`` splits that number by tier.
    """
    query = (
        select(Job, Company.active.label("company_active"), Company.tier.label("tier"))
        .join(Company, Job.company_id == Company.id)
        .where(Job.user_id == user_id, Company.user_id == user_id)
    )
    if active is not None:
        query = query.where(Job.active == active)
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    if tier is not None:
        # Tier lives on the company, so filter through it (current tier,
        # not the tier at scrape time).
        query = query.where(Company.tier == tier)
    if cursor is None:
        per_tier = (
            await session.execute(
                query.with_only_columns(Company.tier, func.count())
                .group_by(Company.tier)
                .order_by(Company.tier)
            )
        ).all()
        response.headers[TOTAL_COUNT_HEADER] = str(sum(n for _, n in per_tier))
        response.headers[TIER_COUNTS_HEADER] = json.dumps(
            {str(t): n for t, n in per_tier}, separators=(",", ":")
        )
    else:
        try:
            after = decode_cursor(cursor)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid cursor"
            ) from None
        after_tier, after_date, after_id = after
        query = query.where(
            or_(
                Company.tier > after_tier,
                and_(
                    Company.tier == after_tier,
                    tuple_(Job.date, Job.id) < (after_date, after_id),
                ),
            )
        )
    # One extra row tells whether another page exists, without a count.
    rows = (
        await session.execute(
            query.order_by(Company.tier, Job.date.desc(), Job.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    page = rows[:limit]
    if len(rows) > limit:
        last = page[-1].Job
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(
            page[-1].tier, last.date, last.id
        )
    return [
        JobOut(
            **{
                name: getattr(job, name)
                for name in JobOut.model_fields
                if name != "company_active"
            },
            company_active=company_active,
        )
        for job, company_active, _ in page
    ]
