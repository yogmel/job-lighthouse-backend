"""/jobs: the caller's stored job postings.

Every query is scoped to the ``user_id`` in the JWT.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session

from .models import Company, Job

router = APIRouter(prefix="/jobs", tags=["jobs"])

Session = Annotated[AsyncSession, Depends(get_session)]


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


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user_id: CurrentUserId,
    session: Session,
    active: bool | None = None,
    company_id: uuid.UUID | None = None,
    tier: int | None = None,
) -> list[JobOut]:
    """The caller's jobs, newest ``date`` first.

    Filters are optional and combine with AND. With no ``active`` filter,
    closed postings are included. Another user's ``company_id`` matches
    nothing, so it returns ``[]``.
    """
    query = select(Job).where(Job.user_id == user_id)
    if active is not None:
        query = query.where(Job.active == active)
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    if tier is not None:
        # Tier lives on the company, so filter through it (current tier,
        # not the tier at scrape time).
        query = query.join(Company, Job.company_id == Company.id).where(
            Company.user_id == user_id, Company.tier == tier
        )
    jobs = await session.scalars(query.order_by(Job.date.desc(), Job.id.desc()))
    return [JobOut.model_validate(j) for j in jobs]
