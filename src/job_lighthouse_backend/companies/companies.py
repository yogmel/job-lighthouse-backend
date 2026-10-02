"""/companies: the caller's tracked companies.

Every query is scoped to the ``user_id`` in the JWT. A malformed ``source``
is a **400**; other body errors stay FastAPI's usual 422.
"""

import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, HttpUrl, model_validator
from sqlalchemy import column, select, table, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.job_runner.company_run import (
    CompanyOutcome,
    Fetcher,
    fetch_company,
    fetch_openings,
)

from .models import Company
from .sources import ManualSource, NonEmptyStr, Source


class _SourceErrorsAre400(APIRoute):
    """Turn body validation errors under ``source`` into a 400."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                if any(tuple(e["loc"][:2]) == ("body", "source") for e in errors):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, detail=jsonable_encoder(errors)
                    ) from None
                raise

        return route


router = APIRouter(
    prefix="/companies", tags=["companies"], route_class=_SourceErrorsAre400
)

Session = Annotated[AsyncSession, Depends(get_session)]

# Owned by the Job Runner; only pausing writes to it from here.
_jobs = table("jobs", column("user_id"), column("company_id"), column("active"))


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tier: int
    added_at: datetime
    website_url: str
    active: bool
    source: Source


class CompanyCreate(BaseModel):
    name: NonEmptyStr
    tier: int
    website_url: HttpUrl
    active: bool = True
    source: ManualSource


class CompanyUpdate(BaseModel):
    """Partial update: only the fields sent are changed."""

    name: NonEmptyStr | None = None
    tier: int | None = None
    website_url: HttpUrl | None = None
    active: bool | None = None
    # Replaces the whole source; no merging with the stored one.
    source: ManualSource | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> "CompanyUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"Cannot be null: {', '.join(nulls)}")
        return self


class SourceTestOut(BaseModel):
    """Result of fetching a company's source once.

    - ``ok``: reachable; ``jobs_found`` may be 0.
    - ``failed``: the fetch failed; ``error`` says why.
    - ``skipped``: the source can't be fetched yet (``custom``).
    """

    status: Literal["ok", "failed", "skipped"]
    jobs_found: int
    error: str | None


def get_fetcher() -> Fetcher:
    """Dependency so tests can swap the network out."""
    return fetch_openings


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Company not found")


@router.get("", response_model=list[CompanyOut])
async def list_companies(user_id: CurrentUserId, session: Session) -> list[CompanyOut]:
    """All of the caller's companies, oldest first, paused ones included."""
    companies = await session.scalars(
        select(Company)
        .where(Company.user_id == user_id)
        .order_by(Company.added_at, Company.id)
    )
    return [CompanyOut.model_validate(c) for c in companies]


@router.post(
    "",
    response_model=CompanyOut,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_400_BAD_REQUEST: {"description": "Malformed source"}},
)
async def create_company(
    body: CompanyCreate, user_id: CurrentUserId, session: Session
) -> CompanyOut:
    """Add a company with an explicit ``board`` or ``scraper`` source.

    Stored as given: no detection and no test fetch.
    """
    company = Company(
        user_id=user_id,
        name=body.name,
        tier=body.tier,
        website_url=str(body.website_url),
        active=body.active,
        source=body.source.model_dump(mode="json"),
    )
    session.add(company)
    try:
        await session.commit()
    except IntegrityError:
        # Only the user_id FK can fail: the token's user was deleted.
        await session.rollback()
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Account not found"
        ) from None
    # Load server defaults (id, added_at).
    await session.refresh(company)
    return CompanyOut.model_validate(company)


@router.put(
    "/{company_id}",
    response_model=CompanyOut,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Malformed source"},
        status.HTTP_404_NOT_FOUND: {"description": "Company not found"},
    },
)
async def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    user_id: CurrentUserId,
    session: Session,
) -> CompanyOut:
    """Change any of name, tier, website_url, active, source.

    - Another user's company is a 404, same as a missing one.
    - Pausing (``active: false``) also sets the company's jobs inactive.
      Resuming (``active: true``) does not restore them.
    """
    company = await session.scalar(
        select(Company).where(Company.id == company_id, Company.user_id == user_id)
    )
    if company is None:
        raise _not_found()

    changes = body.model_dump(mode="json", exclude_unset=True)
    for field, value in changes.items():
        setattr(company, field, value)

    if changes.get("active") is False:
        await session.execute(
            update(_jobs)
            .where(_jobs.c.company_id == company_id, _jobs.c.user_id == user_id)
            .values(active=False)
        )

    await session.commit()
    return CompanyOut.model_validate(company)


@router.post(
    "/{company_id}/test",
    response_model=SourceTestOut,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Company not found"}},
)
async def test_company(
    company_id: uuid.UUID,
    user_id: CurrentUserId,
    session: Session,
    fetch: Annotated[Fetcher, Depends(get_fetcher)],
) -> SourceTestOut:
    """Fetch the company's stored source once and report what came back.

    Read-only: no jobs and no ``RunCompanyResult`` are written. Paused
    companies can be tested too.
    """
    company = await session.scalar(
        select(Company).where(Company.id == company_id, Company.user_id == user_id)
    )
    if company is None:
        raise _not_found()
    # Don't hold a DB connection through a fetch that can take seconds.
    await session.close()

    openings = await fetch_company(company, fetch)
    if isinstance(openings, CompanyOutcome):
        return SourceTestOut(status=openings.status, jobs_found=0, error=openings.error)
    return SourceTestOut(
        status="ok", jobs_found=len({o.url for o in openings}), error=None
    )
