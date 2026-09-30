"""POST /auth/google.

The frontend runs Google Sign-In and posts the resulting ID token here. We
verify it against ``GOOGLE_CLIENT_ID``, find or create the matching ``users``
row, and issue our own JWT.

Account linking (not specified by the design doc):

- A user already linked to this Google account (``google_id == sub``) just
  logs in.
- If an account with the same email exists but isn't linked yet, we link it
  only when Google reports the email as verified. Google has then proven the
  caller controls that address, which is as strong as a password reset by
  email. If Google doesn't vouch for the email, linking would let anyone
  holding an unverified Google account with that address take over the
  existing account, so we refuse with 409 and ask them to log in with their
  password instead. An email already linked to a *different* Google account
  is refused the same way.
- Otherwise a new Google-only account is created (``password_hash`` null).
"""

from dataclasses import dataclass
from typing import Annotated, Any

import google.auth.exceptions
import google.auth.transport.requests
from fastapi import APIRouter, Depends, HTTPException, Request, status
from google.oauth2 import id_token
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from job_lighthouse_backend.common.auth import issue_token
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.common.settings import Settings

from ..models import User
from ..users import get_user_by_email
from .schemas import TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

EMAIL_TAKEN_DETAIL = (
    "An account with this email already exists. Log in with your password."
)


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool


class GoogleLoginRequest(BaseModel):
    id_token: str


def _as_bool(value: Any) -> bool:
    # Google sends a JSON bool; some older tokens used the string "true".
    if isinstance(value, str):
        return value.lower() == "true"
    return value is True


def verify_google_id_token(token: str, client_id: str) -> GoogleIdentity:
    """Verify a Google ID token (signature, expiry, audience, issuer).

    Blocking: fetches Google's signing certs over HTTP. Raises ``ValueError``
    or ``google.auth.exceptions.GoogleAuthError`` if the token is invalid.
    """
    claims = id_token.verify_oauth2_token(
        token, google.auth.transport.requests.Request(), audience=client_id
    )
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("Wrong issuer.")
    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise ValueError("Token is missing sub or email.")
    return GoogleIdentity(
        sub=str(sub),
        email=str(email),
        email_verified=_as_bool(claims.get("email_verified")),
    )


async def _get_user_by_google_id(session: AsyncSession, sub: str) -> User | None:
    result = await session.execute(select(User).where(User.google_id == sub))
    return result.scalar_one_or_none()


def _email_taken() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_TAKEN_DETAIL)


async def _find_or_create_user(session: AsyncSession, identity: GoogleIdentity) -> User:
    user = await _get_user_by_google_id(session, identity.sub)
    if user is not None:
        return user

    existing = await get_user_by_email(session, identity.email)
    if existing is not None:
        if not identity.email_verified or existing.google_id is not None:
            raise _email_taken()
        existing.google_id = identity.sub
        existing.email_verified = True
        user = existing
    else:
        user = User(
            email=identity.email,
            google_id=identity.sub,
            password_hash=None,
            email_verified=identity.email_verified,
        )
        session.add(user)

    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request for the same Google account (or email) won.
        await session.rollback()
        user = await _get_user_by_google_id(session, identity.sub)
        if user is None:
            raise _email_taken() from None
    return user


@router.post("/google", response_model=TokenResponse)
async def google_login(
    body: GoogleLoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    if not settings.google_client_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )
    try:
        identity = await run_in_threadpool(
            verify_google_id_token, body.id_token, settings.google_client_id
        )
    except (ValueError, google.auth.exceptions.GoogleAuthError):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token"
        ) from None

    user = await _find_or_create_user(session, identity)
    return TokenResponse(access_token=issue_token(user.id, settings))
