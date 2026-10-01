"""/config: the caller's settings and match profile.

One row per user, created with defaults on first access: signup doesn't
create it, and the frontend reads it before the user has saved anything.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.companies.sources import NonEmptyStr

from .models import Config

router = APIRouter(prefix="/config", tags=["config"])

Session = Annotated[AsyncSession, Depends(get_session)]

# Daily at 07:00 UTC. Used only when the row is first created.
DEFAULT_CRON = "0 7 * * *"


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    keywords_include: list[str]
    keywords_exclude: list[str]
    location: str
    cron: str
    profile: str
    profile_version: int


class ConfigIn(BaseModel):
    """Full replace of the editable fields.

    ``profile_version`` is server-owned; sending it has no effect.
    """

    keywords_include: list[NonEmptyStr]
    keywords_exclude: list[NonEmptyStr]
    location: str
    # Checked against the schedule by the tick loop (v0.7).
    cron: NonEmptyStr
    # Markdown. Empty means "don't score".
    profile: str


def _account_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")


async def _upsert(session: AsyncSession, stmt: Any) -> Config:
    try:
        config = await session.scalar(stmt.returning(Config))
        await session.commit()
    except IntegrityError:
        # Only the user_id FK can fail: the token's user was deleted.
        await session.rollback()
        raise _account_not_found() from None
    assert config is not None  # noqa: S101 -- an upsert always returns its row
    return config


@router.get(
    "",
    response_model=ConfigOut,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Account not found"}},
)
async def get_config(user_id: CurrentUserId, session: Session) -> ConfigOut:
    """The caller's config, created with defaults if it doesn't exist yet."""
    stmt = insert(Config).values(user_id=user_id, location="", cron=DEFAULT_CRON)
    # A no-op update so RETURNING yields the existing row too.
    stmt = stmt.on_conflict_do_update(
        index_elements=[Config.user_id], set_={"user_id": stmt.excluded.user_id}
    )
    return ConfigOut.model_validate(await _upsert(session, stmt))


@router.put(
    "",
    response_model=ConfigOut,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Account not found"}},
)
async def put_config(
    body: ConfigIn, user_id: CurrentUserId, session: Session
) -> ConfigOut:
    """Replace the editable fields.

    ``profile_version`` goes up by one only when ``profile`` actually changes.
    The old text is overwritten, not kept. Stored jobs are not rescored.
    """
    stmt = insert(Config).values(user_id=user_id, **body.model_dump())
    changed = Config.profile.is_distinct_from(stmt.excluded.profile)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Config.user_id],
        set_={
            **{field: stmt.excluded[field] for field in ConfigIn.model_fields},
            "profile_version": case(
                (changed, Config.profile_version + 1),
                else_=Config.profile_version,
            ),
        },
    )
    return ConfigOut.model_validate(await _upsert(session, stmt))
