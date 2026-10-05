"""GET/PUT/DELETE /account: the caller's own credentials and account.

Every query is scoped to the ``user_id`` in the JWT. Responses always go
through ``AccountOut``, so ``password_hash`` can never leak.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session

from .auth.passwords import hash_password, verify_password
from .auth.schemas import MAX_PASSWORD_LENGTH, NewPassword
from .models import User
from .users import get_user_by_email, get_user_by_id

router = APIRouter(prefix="/account", tags=["account"])

Session = Annotated[AsyncSession, Depends(get_session)]


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    has_password: bool
    google_linked: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "AccountOut":
        return cls(
            id=user.id,
            email=user.email,
            email_verified=user.email_verified,
            has_password=user.password_hash is not None,
            google_linked=user.google_id is not None,
            created_at=user.created_at,
        )


class AccountUpdate(BaseModel):
    # Bounded so a huge input can't make verification costly.
    current_password: Annotated[str, Field(max_length=MAX_PASSWORD_LENGTH)] | None = (
        None
    )
    email: EmailStr | None = None
    new_password: NewPassword | None = None

    @model_validator(mode="after")
    def _requires_a_change(self) -> "AccountUpdate":
        if self.email is None and self.new_password is None:
            raise ValueError("Provide email and/or new_password")
        return self


class AccountDelete(BaseModel):
    current_password: Annotated[str, Field(max_length=MAX_PASSWORD_LENGTH)] | None = (
        None
    )


def _wrong_password() -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN, detail="Current password is incorrect"
    )


def _email_taken() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail="Email already in use")


async def _load_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    return user


@router.get("", response_model=AccountOut)
async def get_account(user_id: CurrentUserId, session: Session) -> AccountOut:
    return AccountOut.from_user(await _load_user(session, user_id))


@router.put("", response_model=AccountOut)
async def update_account(
    body: AccountUpdate, user_id: CurrentUserId, session: Session
) -> AccountOut:
    """Change email and/or password.

    - Account with a password: any change requires ``current_password``.
      Missing or wrong gives **403**, not 401, so the frontend doesn't read
      it as an expired session and log the user out.
    - Google-only account (no password): there is no password to confirm, so
      the valid JWT alone authorises setting a first password or changing
      the email. ``current_password`` is ignored.
    - A new email resets ``email_verified``. An email used by another
      account (case-insensitive) gives 409. Re-submitting the current email
      in a different case only updates its casing.
    """
    user = await _load_user(session, user_id)

    if user.password_hash is not None and not verify_password(
        body.current_password or "", user.password_hash
    ):
        raise _wrong_password()

    if body.email is not None:
        if body.email.lower() != user.email.lower():
            existing = await get_user_by_email(session, body.email)
            if existing is not None and existing.id != user.id:
                raise _email_taken()
            user.email_verified = False
        user.email = body.email

    if body.new_password is not None:
        user.password_hash = hash_password(body.new_password)

    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with another account taking the same email.
        await session.rollback()
        raise _email_taken() from None

    return AccountOut.from_user(user)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_403_FORBIDDEN: {"description": "Current password is incorrect"},
        status.HTTP_404_NOT_FOUND: {"description": "Account not found"},
    },
)
async def delete_account(
    user_id: CurrentUserId, session: Session, body: AccountDelete | None = None
) -> None:
    """Delete the account and every row it owns. Can't be undone.

    - Account with a password: requires ``current_password`` (403 like
      ``PUT``). A Google-only account needs only its token.
    - The database cascades from ``users`` to config, companies, jobs, runs,
      run company results and reset tokens.
    - The account's tokens stop working at once: every protected route
      checks the user still exists.
    """
    user = await _load_user(session, user_id)
    current = body.current_password if body is not None else None
    if user.password_hash is not None and not verify_password(
        current or "", user.password_hash
    ):
        raise _wrong_password()

    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
