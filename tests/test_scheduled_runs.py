"""BE-032: due ticks run the POST /runs pipeline under the same lock."""

import asyncio
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.datastructures import State

from job_lighthouse_backend.common.db import create_engine
from job_lighthouse_backend.common.settings import Settings
from job_lighthouse_backend.job_runner import main, pipeline
from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.runs import lock_name
from job_lighthouse_backend.job_runner.runs_api import run_scheduled
from job_lighthouse_backend.job_runner.scheduler import tick

from .conftest import BOARD_SOURCE, TEST_JWT_SECRET, needs_db, wait_for_run
from .test_runs_post import FakeFetch

pytestmark = needs_db


@pytest.fixture
def fake_fetch() -> FakeFetch:
    return FakeFetch()


def _due_user(db: psycopg.Connection, make_user, make_company) -> uuid.UUID:
    """A user with one company, due on every tick."""
    user_id = make_user()["id"]
    db.execute(
        "INSERT INTO config (user_id, location, cron) VALUES (%s, '', '* * * * *')",
        (user_id,),
    )
    make_company(user_id, source={**BOARD_SOURCE, "board_id": f"b-{user_id}"})
    return user_id


def _state(engine: AsyncEngine) -> State:
    """What ``app.state`` holds, without scoring or email configured."""
    settings = Settings(database_url="unused", jwt_secret=TEST_JWT_SECRET)
    return State({"engine": engine, "settings": settings})


def _tick(user_id: uuid.UUID, fetch: FakeFetch) -> None:
    """One tick, a minute from now, running only ``user_id``'s run."""

    async def go() -> None:
        engine = create_engine(os.environ["DATABASE_URL"])
        state = _state(engine)

        async def on_due(due: uuid.UUID) -> None:
            # Other tests' users in the shared DB may be due too.
            if due == user_id:
                await run_scheduled(state, due, fetch)

        try:
            await tick(engine, datetime.now(UTC) + timedelta(minutes=1), on_due)
        finally:
            await engine.dispose()

    asyncio.run(go())


def _runs(db: psycopg.Connection, user_id: uuid.UUID) -> list[tuple]:
    return db.execute(
        "SELECT trigger, status, jobs_found FROM runs WHERE user_id = %s"
        " ORDER BY started_at",
        (user_id,),
    ).fetchall()


def test_due_tick_runs_the_pipeline(db, make_user, make_company, fake_fetch) -> None:
    user_id = _due_user(db, make_user, make_company)
    url = f"https://jobs.example/{uuid.uuid4().hex}"
    fake_fetch.by_key = {f"b-{user_id}": [Opening("Engineer", url)]}

    _tick(user_id, fake_fetch)

    assert _runs(db, user_id) == [("cron", "success", 1)]
    assert fake_fetch.calls == [f"b-{user_id}"]
    row = db.execute("SELECT active FROM jobs WHERE url = %s", (url,)).fetchone()
    assert row == (True,)


def test_due_tick_noops_while_lock_held(
    db, make_user, make_company, fake_fetch
) -> None:
    user_id = _due_user(db, make_user, make_company)
    key = lock_name(user_id)
    db.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
    try:
        _tick(user_id, fake_fetch)
    finally:
        db.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    assert _runs(db, user_id) == []
    assert fake_fetch.calls == []

    # Lock free: still due, so the next tick runs.
    _tick(user_id, fake_fetch)
    assert _runs(db, user_id) == [("cron", "success", 0)]


@pytest.fixture
def client(fake_fetch: FakeFetch) -> Iterator[TestClient]:
    from job_lighthouse_backend.job_runner.runs_api import get_fetcher

    main.app.dependency_overrides[get_fetcher] = lambda: fake_fetch
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        main.app.dependency_overrides.clear()


def test_scheduled_run_during_manual_run_noops(
    client, db, make_user, make_company, auth_header, fake_fetch, monkeypatch
) -> None:
    """Acceptance: a due tick during an in-flight manual run doesn't double-run."""
    user_id = _due_user(db, make_user, make_company)
    release = asyncio.Event()
    started = threading.Event()
    loop: list[asyncio.AbstractEventLoop] = []

    async def slow(*args, **kwargs):
        loop.append(asyncio.get_running_loop())
        started.set()
        await release.wait()
        raise RuntimeError("done waiting")

    monkeypatch.setattr(pipeline, "run_company", slow)
    resp = client.post("/runs", headers=auth_header(user_id))
    assert resp.status_code == 202
    assert started.wait(5)
    try:
        # Straight to run_scheduled: the manual run's started_at alone would
        # make the tick skip this user, and this checks the lock.
        async def scheduled() -> object:
            engine = create_engine(os.environ["DATABASE_URL"])
            state = _state(engine)
            try:
                return await run_scheduled(state, user_id, fake_fetch)
            finally:
                await engine.dispose()

        assert asyncio.run(scheduled()) is None
    finally:
        loop[0].call_soon_threadsafe(release.set)
    wait_for_run(db, resp.json()["id"])

    assert [r[0] for r in _runs(db, user_id)] == ["manual"]


def test_runner_ticks_with_run_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    async def fake_loop(engine, on_due) -> None:
        captured.append(on_due)

    monkeypatch.setattr(main, "tick_loop", fake_loop)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")

    with TestClient(main.app) as client:
        state = client.app.state  # type: ignore[attr-defined]

    (on_due,) = captured
    assert isinstance(on_due, partial)
    assert on_due.func is run_scheduled
    assert on_due.args == (state,)


def test_scheduled_run_uses_app_scorer_and_mailer(monkeypatch) -> None:
    """Built from settings the same way as POST /runs, and cached on state."""
    monkeypatch.setattr(
        "job_lighthouse_backend.job_runner.runs_api.create_openai_scorer",
        lambda key, model: ("scorer", key, model),
    )
    monkeypatch.setattr(
        "job_lighthouse_backend.job_runner.runs_api.resend_mailer",
        lambda key, sender: ("mailer", key, sender),
    )
    from job_lighthouse_backend.job_runner.runs_api import mailer_for, scorer_for

    state = State(
        {
            "settings": Settings(
                database_url="unused",
                jwt_secret=TEST_JWT_SECRET,
                openai_api_key="k",
                resend_api_key="r",
                email_from="a@example.com",
            )
        }
    )
    assert scorer_for(state) == ("scorer", "k", Settings.openai_model)
    assert mailer_for(state) == ("mailer", "r", "a@example.com")
    assert scorer_for(state) is state.scorer
    assert mailer_for(state) is state.mailer
