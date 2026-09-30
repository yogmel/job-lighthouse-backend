"""POST /auth/login."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import issue_token
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.common.settings import Settings

from ..users import get_user_by_email
from .passwords import verify_password
from .schemas import MAX_PASSWORD_LENGTH, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# One response for unknown email, wrong password and Google-only accounts,
# so the endpoint never reveals whether an email is registered.
INVALID_CREDENTIALS = "Invalid email or password"


class LoginRequest(BaseModel):
    email: EmailStr
    # No strength policy on login; the cap only bounds hashing cost.
    password: Annotated[str, Field(max_length=MAX_PASSWORD_LENGTH)]


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    user = await get_user_by_email(session, body.email)
    # Always verify, even with no user, so timing doesn't leak existence.
    ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings: Settings = request.app.state.settings
    return TokenResponse(access_token=issue_token(user.id, settings))
