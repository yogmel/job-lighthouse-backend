"""BE-019: run lifecycle and advisory lock."""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from job_lighthouse_backend.common.db import create_engine
from job_lighthouse_backend.job_runner.models import Run
from job_lighthouse_backend.job_runner.runs import (
    MAX_ERROR_LENGTH,
    Pipeline,
    execute_run,
    lock_name,
)

from .conftest import needs_db

pytestmark = needs_db


def _run[T](fn: Callable[[AsyncEngine], Awaitable[T]]) -> T:
    """Run ``fn`` with a fresh engine on a fresh event loop."""

    async def main() -> T:
        engine = create_engine(os.environ["DATABASE_URL"])
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(main())


def _execute(user_id: uuid.UUID, pipeline: Pipeline) -> Run | None:
    return _run(lambda engine: execute_run(engine, user_id, "manual", pipeline))


def _runs(db: psycopg.Connection, user_id: uuid.UUID) -> list[tuple]:
    return db.execute(
        "SELECT id, status, trigger, jobs_found, error, finished_at IS NOT NULL"
        " FROM runs WHERE user_id = %s",
        (user_id,),
    ).fetchall()


def _try_lock(db: psycopg.Connection, user_id: uuid.UUID) -> bool:
    row = db.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_name(user_id),)
    ).fetchone()
    assert row is not None
    return row[0]


def _unlock(db: psycopg.Connection, user_id: uuid.UUID) -> None:
    db.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_name(user_id),)
    )


def test_success_closes_run(db, make_user):
    user = make_user()
    seen: dict = {}

    async def pipeline(session: AsyncSession, run: Run) -> int:
        seen["status"] = _runs(db, user["id"])[0][1]
        return 4

    run = _execute(user["id"], pipeline)
    assert run is not None
    assert run.status == "success"
    assert run.jobs_found == 4
    assert run.finished_at is not None
    # The row is committed as "running" before the pipeline starts.
    assert seen["status"] == "running"
    assert _runs(db, user["id"]) == [(run.id, "success", "manual", 4, None, True)]


def test_exception_closes_run_as_failed(db, make_user):
    user = make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        raise RuntimeError("board exploded")

    run = _execute(user["id"], pipeline)
    assert run is not None
    assert run.status == "failed"
    assert _runs(db, user["id"]) == [
        (run.id, "failed", "manual", 0, "RuntimeError: board exploded", True)
    ]


def test_aborted_transaction_still_closes_run(db, make_user):
    user = make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        await session.execute(text("SELECT 1 / 0"))
        return 1

    run = _execute(user["id"], pipeline)
    assert run is not None
    assert run.status == "failed"
    assert run.error is not None
    assert "division by zero" in run.error


def test_long_error_is_truncated(db, make_user):
    user = make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        raise ValueError("x" * (MAX_ERROR_LENGTH * 2))

    run = _execute(user["id"], pipeline)
    assert run is not None
    assert run.error is not None
    assert len(run.error) == MAX_ERROR_LENGTH


def test_cancellation_closes_run_and_propagates(db, make_user):
    user = make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _execute(user["id"], pipeline)
    [(_, status, *_rest)] = _runs(db, user["id"])
    assert status == "failed"


def test_lock_held_during_run_and_released_after(db, make_user):
    user = make_user()
    seen: dict = {}

    async def pipeline(session: AsyncSession, run: Run) -> int:
        seen["free_during_run"] = _try_lock(db, user["id"])
        return 0

    _execute(user["id"], pipeline)
    assert seen["free_during_run"] is False
    assert _try_lock(db, user["id"]) is True
    _unlock(db, user["id"])


def test_lock_released_after_failure(db, make_user):
    user = make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        raise RuntimeError("boom")

    _execute(user["id"], pipeline)
    assert _try_lock(db, user["id"]) is True
    _unlock(db, user["id"])


def test_contention_noops_without_a_runs_row(db, make_user):
    user = make_user()
    called = False

    async def pipeline(session: AsyncSession, run: Run) -> int:
        nonlocal called
        called = True
        return 0

    assert _try_lock(db, user["id"])
    try:
        assert _execute(user["id"], pipeline) is None
    finally:
        _unlock(db, user["id"])
    assert called is False
    assert _runs(db, user["id"]) == []


def test_lock_is_per_user(db, make_user):
    busy, other = make_user(), make_user()

    async def pipeline(session: AsyncSession, run: Run) -> int:
        return 0

    assert _try_lock(db, busy["id"])
    try:
        run = _execute(other["id"], pipeline)
    finally:
        _unlock(db, busy["id"])
    assert run is not None
    assert run.status == "success"
