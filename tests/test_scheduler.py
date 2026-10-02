"""BE-031: the cron tick loop and its due check."""

import asyncio
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.db import create_engine
from job_lighthouse_backend.common.settings import Settings, SettingsError
from job_lighthouse_backend.job_runner import main, scheduler
from job_lighthouse_backend.job_runner.scheduler import (
    InvalidCron,
    _seconds_to_next_tick,
    is_due,
    next_fire,
    tick,
)

from .conftest import needs_db

T = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)


# --- due rule -------------------------------------------------------------


def test_next_fire_is_strictly_after() -> None:
    assert next_fire("0 7 * * *", T) == T + timedelta(days=1)
    assert next_fire("0 7 * * *", T - timedelta(seconds=1)) == T


def test_next_fire_is_utc() -> None:
    tokyo = datetime(2026, 10, 3, 0, 59, tzinfo=timezone(timedelta(hours=9)))
    assert next_fire("0 16 * * *", tokyo) == datetime(2026, 10, 2, 16, tzinfo=UTC)


def test_due_once_fire_time_passes() -> None:
    last = T - timedelta(days=1)
    assert not is_due("0 7 * * *", last, T - timedelta(minutes=1))
    assert is_due("0 7 * * *", last, T)
    assert is_due("0 7 * * *", last, T + timedelta(seconds=30))


def test_not_due_again_after_running() -> None:
    assert not is_due("0 7 * * *", T + timedelta(seconds=2), T + timedelta(hours=1))


def test_missed_fires_catch_up_once() -> None:
    # Down for three days: one run is due, and after it, none until tomorrow.
    now = T + timedelta(days=3, hours=1)
    assert is_due("0 7 * * *", T - timedelta(days=1), now)
    assert not is_due("0 7 * * *", now, now + timedelta(hours=1))


def test_manual_run_after_fire_counts() -> None:
    manual = T + timedelta(minutes=5)
    assert not is_due("0 7 * * *", manual, T + timedelta(minutes=6))


@pytest.mark.parametrize(
    "cron", ["", "nonsense", "0 7 * *", "0 0 7 * * *", "61 * * * *"]
)
def test_invalid_cron(cron: str) -> None:
    with pytest.raises(InvalidCron):
        next_fire(cron, T)


def test_seconds_to_next_tick() -> None:
    assert _seconds_to_next_tick(T) == 60
    assert _seconds_to_next_tick(T + timedelta(seconds=45.5)) == 14.5


# --- settings -------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    if value is None:
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCHEDULER_ENABLED", value)
    return Settings.from_env()


def test_scheduler_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _env(monkeypatch, None).scheduler_enabled
    assert _env(monkeypatch, "").scheduler_enabled
    assert _env(monkeypatch, "True").scheduler_enabled


def test_scheduler_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not _env(monkeypatch, "false").scheduler_enabled


def test_scheduler_bad_value(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SettingsError):
        _env(monkeypatch, "0")


# --- tick (DB) ------------------------------------------------------------


def _config(db: psycopg.Connection, user_id: uuid.UUID, cron: str) -> None:
    db.execute(
        "INSERT INTO config (user_id, location, cron) VALUES (%s, '', %s)"
        " ON CONFLICT (user_id) DO UPDATE SET cron = excluded.cron",
        (user_id, cron),
    )


def _run(db: psycopg.Connection, user_id: uuid.UUID, started_at: datetime) -> None:
    db.execute(
        "INSERT INTO runs (user_id, status, trigger, started_at)"
        " VALUES (%s, 'success', 'manual', %s)",
        (user_id, started_at),
    )


def _user(
    db: psycopg.Connection, make_user: Callable[..., dict], cron: str, last: datetime
) -> uuid.UUID:
    user_id = make_user()["id"]
    _config(db, user_id, cron)
    _run(db, user_id, last)
    return user_id


def _tick(
    now: datetime, on_due: Callable[[uuid.UUID], None] | None = None
) -> list[uuid.UUID]:
    """Run one tick; returns the users ``on_due`` was called for."""
    called: list[uuid.UUID] = []

    async def record(user_id: uuid.UUID) -> None:
        called.append(user_id)
        if on_due is not None:
            on_due(user_id)

    async def go() -> None:
        engine = create_engine(os.environ["DATABASE_URL"])
        try:
            await tick(engine, now, record)
        finally:
            await engine.dispose()

    asyncio.run(go())
    return called


@needs_db
def test_tick_calls_only_due_users(db, make_user) -> None:
    due = _user(db, make_user, "0 7 * * *", T - timedelta(days=1))
    ran = _user(db, make_user, "0 7 * * *", T + timedelta(seconds=1))
    later = _user(db, make_user, "0 8 * * *", T - timedelta(hours=20))

    called = _tick(T + timedelta(seconds=30))

    assert due in called
    assert ran not in called
    assert later not in called


@needs_db
def test_cron_change_applies_next_tick(db, make_user) -> None:
    """Acceptance: changing Config.cron changes the next tick, no restart."""
    now = T + timedelta(minutes=30)
    user_id = _user(db, make_user, "0 8 * * *", T - timedelta(hours=1))
    assert user_id not in _tick(now)

    _config(db, user_id, "30 7 * * *")

    assert user_id in _tick(now)


@needs_db
def test_user_without_runs_waits_for_first_fire_after_signup(db, make_user) -> None:
    user_id = make_user()["id"]
    _config(db, user_id, "* * * * *")
    row = db.execute("SELECT created_at FROM users WHERE id = %s", (user_id,))
    created_at = row.fetchone()[0]

    assert user_id not in _tick(created_at)
    assert user_id in _tick(created_at + timedelta(minutes=1))


@needs_db
def test_user_without_config_is_ignored(db, make_user) -> None:
    user_id = make_user()["id"]
    assert user_id not in _tick(datetime.now(UTC) + timedelta(days=1))


@needs_db
def test_invalid_cron_skipped_others_still_run(db, make_user, caplog) -> None:
    bad = _user(db, make_user, "every morning", T - timedelta(days=1))
    good = _user(db, make_user, "0 7 * * *", T - timedelta(days=1))

    called = _tick(T)

    assert bad not in called
    assert good in called
    assert f"Invalid cron for user {bad}" in caplog.text


@needs_db
def test_on_due_failure_doesnt_stop_others(db, make_user, caplog) -> None:
    first = _user(db, make_user, "0 7 * * *", T - timedelta(days=1))
    second = _user(db, make_user, "0 7 * * *", T - timedelta(days=1))

    def boom(user_id: uuid.UUID) -> None:
        if user_id == first:
            raise RuntimeError("boom")

    called = _tick(T, boom)

    assert first in called
    assert second in called
    assert f"Scheduled run for user {first} failed" in caplog.text


@needs_db
def test_user_rechecked_before_on_due(db, make_user) -> None:
    """A run started while an earlier user ran makes this user not due."""
    users = sorted(
        _user(db, make_user, "0 7 * * *", T - timedelta(days=1)) for _ in range(2)
    )

    def manual_run_for_the_other(user_id: uuid.UUID) -> None:
        if user_id == users[0]:
            _run(db, users[1], T + timedelta(seconds=10))

    called = _tick(T + timedelta(seconds=20), manual_run_for_the_other)

    assert users[0] in called
    assert users[1] not in called


# --- loop -----------------------------------------------------------------


def test_loop_survives_failed_tick(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    calls = 0

    async def fake_tick(engine, now, on_due) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db down")
        if calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "tick", fake_tick)
    monkeypatch.setattr(scheduler, "_seconds_to_next_tick", lambda now: 0)

    async def on_due(user_id: uuid.UUID) -> None:
        pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.tick_loop(None, on_due))  # type: ignore[arg-type]
    assert calls == 2
    assert "Tick failed" in caplog.text


# --- app wiring -----------------------------------------------------------


@needs_db
@pytest.mark.parametrize("enabled", [True, False])
def test_runner_starts_loop_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def fake_loop(engine, on_due) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(main, "tick_loop", fake_loop)
    monkeypatch.setenv("SCHEDULER_ENABLED", str(enabled).lower())

    with TestClient(main.app) as client:
        client.get("/health")
        tasks = client.app.state.background_tasks  # type: ignore[attr-defined]
        assert len(tasks) == (1 if enabled else 0)
        assert started.is_set() == enabled

    assert cancelled == ([True] if enabled else [])
