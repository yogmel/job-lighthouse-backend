"""POST /auth/password-reset/request and /auth/password-reset/confirm.

Request (BE-041):

- Always the same ``202`` with no body, whether the email has an account or
  not, so it never reveals which emails are registered.
- The lookup, token and send run after the response (a background task), so
  response time doesn't reveal it either.
- Google-only accounts get a link too: it sets their first password.
- At most ``RESET_REQUESTS_PER_HOUR`` per email. Extra ones get the same
  ``202`` and send nothing, so the endpoint can't flood an inbox.
- Without ``RESEND_API_KEY`` or ``PASSWORD_RESET_URL`` nothing is sent.

Tokens: 32 random bytes, valid for ``RESET_TOKEN_TTL``, single use. Only
their SHA-256 is stored. The token is random, so a fast unsalted hash is
enough, and it lets confirm look the token up.

Confirm (BE-042):

- An unknown, expired or used token is a **400**, not 401: a 401 logs the
  frontend out.
- The same ``UPDATE`` checks the token and marks it used, so two confirms
  with one token can't both win.
- Sets the new password, marks the email verified (the link proved the user
  reads it) and voids the user's other unused tokens.
"""

import hashlib
import html
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import State

from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.common.email import Email, Mailer, resend_mailer
from job_lighthouse_backend.common.settings import Settings

from ..models import PasswordResetToken
from ..rate_limit import RateLimiter
from ..users import get_user_by_email, get_user_by_id
from .passwords import hash_password
from .schemas import NewPassword

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])

RESET_TOKEN_TTL = timedelta(hours=1)
RESET_REQUESTS_PER_HOUR = 5
# token_urlsafe(32) is 43 characters; the cap only bounds the input.
MAX_TOKEN_LENGTH = 128

INVALID_LINK = "This reset link is invalid or has expired"


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=MAX_TOKEN_LENGTH)]
    new_password: NewPassword


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_reset_mailer(request: Request) -> Mailer | None:
    """The reset mailer, or ``None`` when email isn't configured.

    A dependency so tests can swap the network out. Built once per app.
    """
    state = request.app.state
    settings: Settings = state.settings
    if not settings.resend_api_key or not settings.email_from:
        return None
    if getattr(state, "mailer", None) is None:
        state.mailer = resend_mailer(settings.resend_api_key, settings.email_from)
    return state.mailer


async def get_reset_limiter(request: Request) -> RateLimiter:
    """The per-email request limiter. Built once per app."""
    state: State = request.app.state
    if getattr(state, "reset_limiter", None) is None:
        state.reset_limiter = RateLimiter(RESET_REQUESTS_PER_HOUR, 60 * 60)
    return state.reset_limiter


def build_reset_email(to: str, link: str) -> Email:
    ttl_minutes = int(RESET_TOKEN_TTL.total_seconds() // 60)
    text = (
        "Someone asked to reset the password for your Job Lighthouse account.\n\n"
        f"Set a new password here (valid for {ttl_minutes} minutes, once):\n"
        f"{link}\n\n"
        "If it wasn't you, ignore this email. Your password stays the same.\n"
    )
    safe = html.escape(link, quote=True)
    body = (
        "<p>Someone asked to reset the password for your Job Lighthouse "
        "account.</p>"
        f'<p><a href="{safe}">Set a new password</a> '
        f"(valid for {ttl_minutes} minutes, once).</p>"
        "<p>If it wasn't you, ignore this email. Your password stays the "
        "same.</p>"
    )
    return Email(
        to=to, subject="Reset your Job Lighthouse password", text=text, html=body
    )


async def send_reset(
    sessionmaker: async_sessionmaker[AsyncSession],
    email: str,
    reset_url: str,
    mailer: Mailer,
) -> None:
    """Issue a token for ``email``'s account and email the link.

    Runs after the response. Does nothing for an unknown email. Never
    raises: there is no one left to tell.
    """
    try:
        async with sessionmaker() as session:
            user = await get_user_by_email(session, email)
            if user is None:
                return
            token = secrets.token_urlsafe(32)
            session.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=hash_token(token),
                    expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
                )
            )
            await session.commit()
            user_id, to = user.id, user.email
        link = f"{reset_url}?{urlencode({'token': token})}"
        await mailer(build_reset_email(to, link))
    except Exception as exc:
        # Type only: the message may echo the address.
        logger.warning("Password reset email failed: %s", type(exc).__name__)
        return
    logger.info("Password reset email sent for user %s", user_id)


@router.post("/request", status_code=status.HTTP_202_ACCEPTED)
async def request_reset(
    body: ResetRequest,
    request: Request,
    background: BackgroundTasks,
    mailer: Annotated[Mailer | None, Depends(get_reset_mailer)],
    limiter: Annotated[RateLimiter, Depends(get_reset_limiter)],
) -> None:
    """Email a reset link if the account exists. Same response either way."""
    settings: Settings = request.app.state.settings
    if mailer is None or not settings.password_reset_url:
        logger.warning("Password reset requested but email isn't configured")
        return
    # async route: runs on the event loop, where each hit is atomic.
    if limiter.hit(body.email.lower()) is not None:
        return
    background.add_task(
        send_reset,
        request.app.state.sessionmaker,
        body.email,
        settings.password_reset_url,
        mailer,
    )


@router.post(
    "/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_400_BAD_REQUEST: {"description": INVALID_LINK}},
)
async def confirm_reset(
    body: ResetConfirm,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Set a new password from an emailed token. The token is then used up."""
    user_id: uuid.UUID | None = await session.scalar(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hash_token(body.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(PasswordResetToken.user_id)
    )
    user = await get_user_by_id(session, user_id) if user_id is not None else None
    if user is None:
        await session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)

    user.password_hash = hash_password(body.new_password)
    user.email_verified = True
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=func.now())
    )
    await session.commit()
