"""Database URL handling, engine and session setup shared by both services."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM base. Tables are created by Alembic migrations, not ``create_all``."""


def normalize_database_url(url: str) -> str:
    """Point plain ``postgres://`` / ``postgresql://`` URLs at psycopg (v3).

    psycopg v3 serves both the sync (Alembic) and async (services) engines
    under the same ``postgresql+psycopg`` driver name.
    """
    parsed = make_url(url)
    if parsed.drivername in ("postgres", "postgresql"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        normalize_database_url(database_url), pool_pre_ping=True
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_connection(engine: AsyncEngine) -> None:
    """Run ``SELECT 1``. Raises if Postgres is unreachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Handlers commit explicitly."""
    async with request.app.state.sessionmaker() as session:
        yield session
