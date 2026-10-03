"""GET /account/export: every row the caller owns, as one JSON document.

- One key per table, each a list of its rows with every column, oldest first.
  ``config`` is one object (or null): a user has at most one.
- ``account`` is the same shape as ``GET /account``, so ``password_hash``
  never leaks. Password reset tokens are left out: only hashes, no use to
  the user.
- ``run_company_results`` has no ``user_id``; it's scoped through its run.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import Base, get_session
from job_lighthouse_backend.job_runner.models import Config, Job, Run, RunCompanyResult

from .account import AccountOut
from .models import Company
from .users import get_user_by_id

router = APIRouter(prefix="/account", tags=["account"])

Session = Annotated[AsyncSession, Depends(get_session)]


def _row(obj: Base) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


@router.get(
    "/export",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Account not found"}},
)
async def export_account(user_id: CurrentUserId, session: Session) -> dict[str, Any]:
    """The caller's account, config, companies, jobs, runs and run results."""
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")

    config = await session.scalar(select(Config).where(Config.user_id == user_id))
    companies = await session.scalars(
        select(Company)
        .where(Company.user_id == user_id)
        .order_by(Company.added_at, Company.id)
    )
    jobs = await session.scalars(
        select(Job).where(Job.user_id == user_id).order_by(Job.date, Job.id)
    )
    runs = await session.scalars(
        select(Run).where(Run.user_id == user_id).order_by(Run.started_at, Run.id)
    )
    results = await session.scalars(
        select(RunCompanyResult)
        .join(Run, Run.id == RunCompanyResult.run_id)
        .where(Run.user_id == user_id)
        .order_by(Run.started_at, Run.id, RunCompanyResult.id)
    )
    return jsonable_encoder(
        {
            "exported_at": datetime.now(UTC),
            "account": AccountOut.from_user(user),
            "config": _row(config) if config is not None else None,
            "companies": [_row(c) for c in companies],
            "jobs": [_row(j) for j in jobs],
            "runs": [_row(r) for r in runs],
            "run_company_results": [_row(r) for r in results],
        }
    )
