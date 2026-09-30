"""/companies: the caller's tracked companies.

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

from .models import Company
from .sources import Source

router = APIRouter(prefix="/companies", tags=["companies"])

Session = Annotated[AsyncSession, Depends(get_session)]


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tier: int
    added_at: datetime
    website_url: str
    active: bool
    source: Source


@router.get("", response_model=list[CompanyOut])
async def list_companies(user_id: CurrentUserId, session: Session) -> list[CompanyOut]:
    """All of the caller's companies, oldest first, paused ones included."""
    companies = await session.scalars(
        select(Company)
        .where(Company.user_id == user_id)
        .order_by(Company.added_at, Company.id)
    )
    return [CompanyOut.model_validate(c) for c in companies]
