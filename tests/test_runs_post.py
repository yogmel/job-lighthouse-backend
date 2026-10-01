"""BE-025: POST /runs wires the whole pipeline together (BE-028: scoring)."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.job_runner import pipeline
from job_lighthouse_backend.job_runner.openings import FetchError, Opening
from job_lighthouse_backend.job_runner.runs import lock_name
from job_lighthouse_backend.job_runner.runs_api import get_fetcher

from .conftest import BOARD_SOURCE, TEST_JWT_SECRET, needs_db
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
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "success"
    assert body["trigger"] == "manual"
    assert body["jobs_found"] == 2
    assert body["finished_at"] is not None
    assert body["error"] is None

    run_id = uuid.UUID(body["id"])
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
    second = client.post("/runs", headers=auth_header(user["id"])).json()
    assert (first["jobs_found"], second["jobs_found"]) == (1, 0)
    assert first["id"] != second["id"]


def test_only_own_companies_are_run(
    client, db, make_user, make_company, auth_header, fake_fetch
):
    user, other = make_user(), make_user()
    make_company(other["id"], source=_board("theirs"))
    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 201
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


def test_pipeline_error_returns_failed_run(
    client, db, make_user, make_company, auth_header, monkeypatch
):
    user = make_user()
    make_company(user["id"])

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "run_company", broken)
    resp = client.post("/runs", headers=auth_header(user["id"]))
    assert resp.status_code == 201
    assert resp.json()["status"] == "failed"
    assert resp.json()["error"] == "RuntimeError: boom"


def test_deleted_account_is_404(client, auth_header):
    resp = client.post("/runs", headers=auth_header(uuid.uuid4()))
    assert resp.status_code == 404


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
            assert c.post("/runs", headers=auth_header(user["id"])).status_code == 201
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
