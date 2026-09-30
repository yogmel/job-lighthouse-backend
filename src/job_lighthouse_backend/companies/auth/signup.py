"""POST /auth/signup."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import issue_token
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.common.settings import Settings

from ..models import User
from ..users import get_user_by_email
from .passwords import hash_password
from .schemas import NewPassword, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_TAKEN = "An account with this email already exists."


class SignupRequest(BaseModel):
    email: EmailStr
    password: NewPassword


def _email_taken() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN)


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenResponse,
    responses={409: {"description": EMAIL_TAKEN}},
)
async def signup(
    body: SignupRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Create an email+password account and return a token for it."""
    # Also catches Google-only accounts: one row per email, case-insensitive.
    if await get_user_by_email(session, body.email) is not None:
        raise _email_taken()

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        google_id=None,
        email_verified=False,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent signup won the race on the lower(email) unique index.
        await session.rollback()
        raise _email_taken() from None

    settings: Settings = request.app.state.settings
    return TokenResponse(access_token=issue_token(user.id, settings))
