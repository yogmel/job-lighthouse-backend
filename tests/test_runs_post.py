"""BE-025: POST /runs wires the whole pipeline together (BE-028: scoring).

BE-045: the run goes on in the background; the response is the open row.
"""

import asyncio
import threading
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.job_runner import pipeline
from job_lighthouse_backend.job_runner.openings import FetchError, Opening
from job_lighthouse_backend.job_runner.runs import lock_name
from job_lighthouse_backend.job_runner.runs_api import get_fetcher

from .conftest import BOARD_SOURCE, TEST_JWT_SECRET, needs_db, wait_for_run
from .test_sync_close import SCRAPER_SOURCE

pytestmark = needs_db


class FakeFetch:
    """Openings per board_id / careers_url; an Exception value is raised."""

    def __init__(self) -> None:
        self.by_key: dict[str, list[Opening] | Exception] = {}
        self.calls: list[str] = []

    def __call__(self, source):
        key = getattr(source, "board_id", None) or str(source.selectors.careers_url)
        self.calls.append(key)
        value = self.by_key.get(key, [])
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def fake_fetch() -> FakeFetch:
    return FakeFetch()


@pytest.fixture
def client(db: psycopg.Connection, fake_fetch: FakeFetch) -> Iterator[TestClient]:
    from job_lighthouse_backend.job_runner.main import app

    app.dependency_overrides[get_fetcher] = lambda: fake_fetch
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _board(board_id: str) -> dict:
    return {**BOARD_SOURCE, "board_id": board_id}


def _url() -> str:
    return f"https://jobs.example/{uuid.uuid4().hex}"


def _add_job(db, user_id, company_id) -> str:
    url = _url()
    db.execute(
        "INSERT INTO jobs (user_id, company_id, company, title, url, location,"
        " description) VALUES (%s, %s, 'Acme', 'Job', %s, '', '')",
        (user_id, company_id, url),
    )
    return url


def _active(db, url) -> bool:
    row = db.execute("SELECT active FROM jobs WHERE url = %s", (url,)).fetchone()
    assert row is not None
    return row[0]


def _results(db, run_id) -> dict:
    rows = db.execute(
        "SELECT company_id, status, jobs_found FROM run_company_results"
        " WHERE run_id = %s",
        (run_id,),
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_requires_token(client):
    assert client.post("/runs").status_code == 401


def test_full_run(client, db, make_user, make_company, auth_header, fake_fetch):
    user = make_user()
    ok = make_company(user["id"], name="Ok", source=_board("ok"))
    down = make_company(user["id"], name="Down", source=_board("down"))
    scraped = make_company(user["id"], name="Scraped", source=SCRAPER_SOURCE)
    paused = make_company(user["id"], name="Paused", active=False, source=_board("p"))
    gone = _add_job(db, user["id"], ok)
    kept_on_failure = _add_job(db, user["id"], down)
    kept_on_empty_scrape = _add_job(db, user["id"], scraped)
    new_a, new_b = _url(), _url()
    fake_fetch.by_key = {
        "ok": [Opening("A", new_a), Opening("B", new_b)],
        "down": FetchError("HTTP 503"),
        # Scraper returns [] by default: ambiguous, closes nothing.
    }

    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["trigger"] == "manual"
    assert body["jobs_found"] == 0
    assert body["finished_at"] is None
    assert body["error"] is None

    run_id = uuid.UUID(body["id"])
    assert wait_for_run(db, run_id) == ("success", 2, None)
    # Exactly one result per active company; the paused one isn't in the run.
    assert _results(db, run_id) == {
        ok: ("ok", 2),
        down: ("failed", 0),
        scraped: ("ok", 0),
    }
    assert paused not in _results(db, run_id)
    assert "p" not in fake_fetch.calls
    assert _active(db, gone) is False
    assert _active(db, kept_on_failure) is True
    assert _active(db, kept_on_empty_scrape) is True
    assert _active(db, new_a) and _active(db, new_b)
    row = db.execute(
        "SELECT status, jobs_found FROM runs WHERE id = %s", (run_id,)
    ).fetchone()
    assert row == ("success", 2)


def test_second_run_finds_no_new_jobs(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    user = make_user()
    make_company(user["id"], source=_board("acme"))
    fake_fetch.by_key = {"acme": [Opening("A", _url())]}
    first = client.post("/runs", headers=auth_header(user["id"])).json()
    assert wait_for_run(db, first["id"])[1] == 1
    second = client.post("/runs", headers=auth_header(user["id"])).json()
    assert wait_for_run(db, second["id"])[1] == 0
    assert first["id"] != second["id"]


def test_only_own_companies_are_run(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    user, other = make_user(), make_user()
    make_company(other["id"], source=_board("theirs"))
    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 202
    assert wait_for_run(db, resp.json()["id"])[0] == "success"
    assert fake_fetch.calls == []
    assert _results(db, uuid.UUID(resp.json()["id"])) == {}


def test_contention_noops_with_409(client, db, make_user, make_company, auth_header):
    user = make_user()
    make_company(user["id"])
    key = lock_name(user["id"])
    db.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
    try:
        resp = client.post("/runs", headers=auth_header(user["id"]))
    finally:
        db.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    assert resp.status_code == 409
    count = db.execute(
        "SELECT count(*) FROM runs WHERE user_id = %s", (user["id"],)
    ).fetchone()
    assert count == (0,)


def test_pipeline_error_closes_run_as_failed(
    client, db, make_user, make_company, auth_header, monkeypatch
):
    user = make_user()
    make_company(user["id"])

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "run_company", broken)
    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 202
    assert resp.json()["status"] == "running"
    assert wait_for_run(db, resp.json()["id"]) == ("failed", 0, "RuntimeError: boom")


def test_responds_before_the_run_finishes(
    client, db, make_user, make_company, auth_header, monkeypatch
):
    """A run longer than the proxy timeout: the client still gets the row."""
    user = make_user()
    make_company(user["id"])
    release = asyncio.Event()
    started = threading.Event()
    loop: list[asyncio.AbstractEventLoop] = []

    async def slow(*args, **kwargs):
        loop.append(asyncio.get_running_loop())
        started.set()
        await release.wait()
        raise RuntimeError("done waiting")

    monkeypatch.setattr(pipeline, "run_company", slow)
    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 202
    run_id = resp.json()["id"]
    row = db.execute("SELECT status FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("running",)

    # The lock is still held: a second request no-ops.
    again = client.post("/runs", headers=auth_header(user["id"]))
    assert again.status_code == 409

    assert started.wait(5)
    loop[0].call_soon_threadsafe(release.set)
    assert wait_for_run(db, run_id)[0] == "failed"
    # Lock released: the next run starts.
    assert client.post("/runs", headers=auth_header(user["id"])).status_code == 202


def test_shutdown_closes_a_running_run(
    db, make_user, make_company, auth_header, fake_fetch, monkeypatch
):
    """Shutdown cancels the run, which still closes as failed."""
    from job_lighthouse_backend.job_runner.main import app

    user = make_user()
    make_company(user["id"])

    async def hangs(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(pipeline, "run_company", hangs)
    app.dependency_overrides[get_fetcher] = lambda: fake_fetch
    try:
        with TestClient(app) as c:
            resp = c.post("/runs", headers=auth_header(user["id"]))
            assert resp.status_code == 202
    finally:
        app.dependency_overrides.clear()
    status_, _, error = wait_for_run(db, resp.json()["id"], timeout=1)
    assert (status_, error) == ("failed", "CancelledError")


def test_deleted_account_is_401(client, auth_header):
    resp = client.post("/runs", headers=auth_header(uuid.uuid4()))
    assert resp.status_code == 401


def test_default_fetcher_is_the_real_one():
    from job_lighthouse_backend.job_runner.company_run import fetch_openings

    assert get_fetcher() is fetch_openings


def test_new_jobs_scored_through_api(
    db, make_user, make_company, auth_header, fake_fetch
):
    from job_lighthouse_backend.job_runner.main import app
    from job_lighthouse_backend.job_runner.runs_api import get_scorer
    from job_lighthouse_backend.job_runner.scoring import Match

    async def scorer(profile, posting):
        return Match(score=90, description="fits")

    user = make_user()
    make_company(user["id"], source=_board("acme"))
    db.execute(
        "INSERT INTO config (user_id, location, cron, profile, profile_version)"
        " VALUES (%s, '', '0 7 * * *', 'me', 4)",
        (user["id"],),
    )
    url = _url()
    fake_fetch.by_key = {"acme": [Opening("A", url)]}
    app.dependency_overrides[get_fetcher] = lambda: fake_fetch
    app.dependency_overrides[get_scorer] = lambda: scorer
    try:
        with TestClient(app) as c:
            resp = c.post("/runs", headers=auth_header(user["id"]))
            assert resp.status_code == 202
            assert wait_for_run(db, resp.json()["id"])[0] == "success"
    finally:
        app.dependency_overrides.clear()
    row = db.execute(
        "SELECT match_score, match_description, profile_version FROM jobs"
        " WHERE url = %s",
        (url,),
    ).fetchone()
    assert row == (90.0, "fits", 4)


def test_scorer_needs_api_key():
    from types import SimpleNamespace

    from job_lighthouse_backend.common.settings import Settings
    from job_lighthouse_backend.job_runner.runs_api import get_scorer

    def request(**overrides):
        settings = Settings(
            database_url="unused", jwt_secret=TEST_JWT_SECRET, **overrides
        )
        state = SimpleNamespace(settings=settings)
        return SimpleNamespace(app=SimpleNamespace(state=state))

    assert get_scorer(request()) is None
    with_key = request(openai_api_key="sk-test")
    scorer = get_scorer(with_key)
    assert scorer is not None
    # Built once, then reused.
    assert get_scorer(with_key) is scorer


# --- BE-058: Single-company run -------------------------------------------


def _post_single(client, auth_header, user, company_id):
    return client.post(
        "/runs",
        headers=auth_header(user["id"]),
        json={"company_id": str(company_id)},
    )


def _run_count(db, user_id) -> int:
    row = db.execute(
        "SELECT count(*) FROM runs WHERE user_id = %s", (user_id,)
    ).fetchone()
    assert row is not None
    return row[0]


def test_single_company_run(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    user = make_user()
    target = make_company(user["id"], name="Target", source=_board("target"))
    other = make_company(user["id"], name="Other", source=_board("other"))
    gone = _add_job(db, user["id"], target)
    other_job = _add_job(db, user["id"], other)
    new = _url()
    fake_fetch.by_key = {"target": [Opening("A", new)]}

    resp = _post_single(client, auth_header, user, target)

    assert resp.status_code == 202
    body = resp.json()
    assert body["scope"] == "company"
    assert body["company_id"] == str(target)
    assert body["trigger"] == "manual"
    run_id = uuid.UUID(body["id"])
    assert wait_for_run(db, run_id) == ("success", 1, None)
    assert fake_fetch.calls == ["target"]
    assert _results(db, run_id) == {target: ("ok", 1)}
    # Same close rules as a Full run; other companies are untouched.
    assert _active(db, gone) is False
    assert _active(db, other_job) is True
    assert _active(db, new)


def test_run_without_body_is_a_full_run(client, db, make_user, auth_header):
    user = make_user()
    body = client.post("/runs", headers=auth_header(user["id"])).json()
    assert body["scope"] == "all"
    assert body["company_id"] is None
    wait_for_run(db, body["id"])


def test_single_company_run_sends_the_digest(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    from job_lighthouse_backend.job_runner.runs_api import get_mailer

    from .test_digest import FakeMailer

    mailer = FakeMailer()
    client.app.dependency_overrides[get_mailer] = lambda: mailer
    user = make_user()
    target = make_company(user["id"], name="Target", source=_board("target"))
    fake_fetch.by_key = {"target": [Opening("A", _url())]}

    resp = _post_single(client, auth_header, user, target)

    wait_for_run(db, resp.json()["id"])
    assert len(mailer.sent) == 1


def test_single_company_run_missing_company_is_404(
    client, db, make_user, auth_header, fake_fetch
):
    user = make_user()

    resp = _post_single(client, auth_header, user, uuid.uuid4())

    assert resp.status_code == 404
    assert _run_count(db, user["id"]) == 0


def test_single_company_run_other_users_company_is_404(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    owner, intruder = make_user(), make_user()
    company_id = make_company(owner["id"], source=_board("x"))

    resp = _post_single(client, auth_header, intruder, company_id)

    assert resp.status_code == 404
    assert fake_fetch.calls == []
    assert _run_count(db, intruder["id"]) == 0
    assert _run_count(db, owner["id"]) == 0


def test_single_company_run_paused_company_is_409(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    user = make_user()
    company_id = make_company(user["id"], active=False, source=_board("p"))

    resp = _post_single(client, auth_header, user, company_id)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Company is paused"
    assert fake_fetch.calls == []
    assert _run_count(db, user["id"]) == 0


def test_single_company_run_contention_is_409(
    client, db, make_user, make_company, auth_header
):
    user = make_user()
    company_id = make_company(user["id"], source=_board("x"))
    key = lock_name(user["id"])
    db.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
    try:
        resp = _post_single(client, auth_header, user, company_id)
    finally:
        db.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "A run is already in progress"
    assert _run_count(db, user["id"]) == 0


def test_single_company_run_outlives_its_company(
    client, companies_client, db, make_user, make_company, auth_header
):
    user = make_user()
    company_id = make_company(user["id"], name="Gone", source=_board("g"))
    run_id = _post_single(client, auth_header, user, company_id).json()["id"]
    wait_for_run(db, run_id)

    deleted = companies_client.delete(
        f"/companies/{company_id}", headers=auth_header(user["id"])
    )
    assert deleted.status_code == 204

    runs = client.get("/runs", headers=auth_header(user["id"])).json()
    assert [(r["id"], r["scope"], r["company_id"]) for r in runs] == [
        (run_id, "company", None)
    ]
