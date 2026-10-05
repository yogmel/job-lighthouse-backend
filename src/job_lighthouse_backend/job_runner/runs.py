"""Run lifecycle: advisory lock + ``Runs`` row around the pipeline.

One lock per user, so a scheduled and a manual run for the same user can't
overlap, while different users' runs don't block each other.

- The lock is session-level, so it is taken and released on one dedicated
  connection held for the whole run. If the unlock fails, that connection is
  invalidated (closed), which frees the lock too.
- The ``Runs`` row is opened and closed in their own short sessions, not the
  pipeline's. A DB error that aborts the pipeline's transaction can't stop
  the row from closing as ``failed``.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from job_lighthouse_backend.common.db import create_sessionmaker

from .models import Run

logger = logging.getLogger(__name__)

Trigger = Literal["cron", "manual"]

# Takes the pipeline's own session and the open run; returns the number of
# new jobs, stored as ``Runs.jobs_found``.
Pipeline = Callable[[AsyncSession, Run], Awaitable[int]]

# Called with the ``running`` row once it is committed, before the pipeline.
OnOpen = Callable[[Run], None]

# Keeps ``Runs.error`` readable; the full traceback goes to the log.
MAX_ERROR_LENGTH = 1000


def lock_name(user_id: uuid.UUID) -> str:
    """Text hashed into the advisory lock key (``hashtextextended``)."""
    return f"job_runner.run:{user_id}"


def _lock_key(user_id: uuid.UUID) -> ColumnElement[int]:
    return func.hashtextextended(lock_name(user_id), 0)


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:MAX_ERROR_LENGTH]


async def execute_run(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    trigger: Trigger,
    pipeline: Pipeline,
    on_open: OnOpen | None = None,
) -> Run | None:
    """Run ``pipeline`` for ``user_id`` under the lock.

    Returns the closed ``Runs`` row (``success`` or ``failed``), or ``None``
    if another run holds the lock. In that case no row is written and
    ``on_open`` isn't called.
    A pipeline exception closes the row as ``failed`` and is not re-raised;
    a cancellation also closes it, then propagates.
    """
    sessionmaker = create_sessionmaker(engine)
    async with user_run_lock(engine, user_id) as acquired:
        if not acquired:
            return None
        return await _run(sessionmaker, user_id, trigger, pipeline, on_open)


@asynccontextmanager
async def user_run_lock(engine: AsyncEngine, user_id: uuid.UUID) -> AsyncIterator[bool]:
    """Try (don't wait for) the user's run lock; yields whether it was taken.

    Released on exit, also when the body raises. Used by runs and by company
    deletes, so a delete never overlaps a run.
    """
    async with engine.connect() as lock_conn:
        acquired = await lock_conn.scalar(
            select(func.pg_try_advisory_lock(_lock_key(user_id)))
        )
        # End the implicit transaction; the lock outlives it.
        await lock_conn.commit()
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            try:
                await lock_conn.execute(
                    select(func.pg_advisory_unlock(_lock_key(user_id)))
                )
                await lock_conn.commit()
            except Exception:
                logger.exception("Advisory unlock failed; dropping the connection")
                await lock_conn.invalidate()


async def _run(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    trigger: Trigger,
    pipeline: Pipeline,
    on_open: OnOpen | None,
) -> Run:
    async with sessionmaker() as session:
        run = Run(user_id=user_id, trigger=trigger, status="running")
        session.add(run)
        await session.commit()
        await session.refresh(run)

    try:
        if on_open is not None:
            on_open(run)
        async with sessionmaker() as work:
            jobs_found = await pipeline(work, run)
    except BaseException as exc:
        logger.exception("Run %s failed", run.id)
        closed = await _close(sessionmaker, run.id, "failed", error=_error_text(exc))
        if not isinstance(exc, Exception):
            raise
        return closed
    return await _close(sessionmaker, run.id, "success", jobs_found=jobs_found)


async def _close(
    sessionmaker: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    status: Literal["success", "failed"],
    *,
    jobs_found: int = 0,
    error: str | None = None,
) -> Run:
    async with sessionmaker() as session:
        run = await session.scalar(
            update(Run)
            .where(Run.id == run_id)
            .values(
                status=status,
                finished_at=func.now(),
                jobs_found=jobs_found,
                error=error,
            )
            .returning(Run)
        )
        await session.commit()
    assert run is not None  # noqa: S101 -- opened above, never deleted mid-run
    return run
