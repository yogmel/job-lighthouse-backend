"""Queries on ``users`` shared by the auth and account routes."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    # lower() on both sides matches the unique index ix_users_email_lower.
    result = await session.execute(
        select(User).where(func.lower(User.email) == email.lower())
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)
