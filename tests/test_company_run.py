"""BE-024: one RunCompanyResult per company, whatever the outcome."""

import uuid

import psycopg
import pytest
from sqlalchemy import select

from job_lighthouse_backend.companies.sources import (
    BoardSource,
    CustomSource,
    ScraperSource,
)
from job_lighthouse_backend.job_runner import company_run, handlers
from job_lighthouse_backend.job_runner.company_run import (
    CompanyOutcome,
    fetch_openings,
    run_company,
)
from job_lighthouse_backend.job_runner.models import Company
from job_lighthouse_backend.job_runner.openings import FetchError, Opening

from .aio import in_session
from .conftest import BOARD_SOURCE, needs_db
from .test_sync_close import CUSTOM_SOURCE, SCRAPER_SOURCE


def _url() -> str:
    return f"https://jobs.example/{uuid.uuid4().hex}"


def _open_run(db: psycopg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    row = db.execute(
        "INSERT INTO runs (user_id, status, trigger) VALUES (%s, 'running', 'manual')"
        " RETURNING id",
        (user_id,),
    ).fetchone()
    assert row is not None
    return row[0]


def _process(run_id, company_ids, fetch) -> list[CompanyOutcome]:
    async def fn(session):
        outcomes = []
        for company_id in company_ids:
            company = await session.scalar(
                select(Company).where(Company.id == company_id)
            )
            outcomes.append(await run_company(session, run_id, company, fetch))
        return outcomes

    return in_session(fn)


def _results(db: psycopg.Connection, run_id: uuid.UUID) -> dict[uuid.UUID, tuple]:
    rows = db.execute(
        "SELECT company_id, status, jobs_found, error FROM run_company_results"
        " WHERE run_id = %s",
        (run_id,),
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def _jobs(db: psycopg.Connection, company_id: uuid.UUID) -> dict[str, bool]:
    rows = db.execute(
        "SELECT url, active FROM jobs WHERE company_id = %s", (company_id,)
    ).fetchall()
    return dict(rows)


def _add_job(db, user_id, company_id) -> str:
    url = _url()
    db.execute(
        "INSERT INTO jobs (user_id, company_id, company, title, url, location,"
        " description) VALUES (%s, %s, 'Acme', 'Job', %s, '', '')",
        (user_id, company_id, url),
    )
    return url


def _returns(*openings: Opening):
    return lambda source: list(openings)


def _raises(exc: Exception):
    def fetch(source):
        raise exc

    return fetch


@needs_db
def test_ok_inserts_closes_and_records(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=BOARD_SOURCE)
    gone = _add_job(db, user["id"], company_id)
    new_url = _url()
    run_id = _open_run(db, user["id"])

    [outcome] = _process(
        run_id,
        [company_id],
        _returns(Opening("A", new_url), Opening("A again", new_url)),
    )
    assert outcome.status == "ok"
    assert outcome.jobs_found == 1
    assert len(outcome.new_job_ids) == 1
    assert _results(db, run_id) == {company_id: ("ok", 1, None)}
    assert _jobs(db, company_id) == {gone: False, new_url: True}


@needs_db
def test_fetch_failure_records_failed_and_touches_no_jobs(db, make_user, make_company):
    user = make_user()
    # Board kind would close on an empty success; a failure must not.
    company_id = make_company(user["id"], source=BOARD_SOURCE)
    url = _add_job(db, user["id"], company_id)
    run_id = _open_run(db, user["id"])

    [outcome] = _process(run_id, [company_id], _raises(FetchError("HTTP 503")))
    assert outcome == CompanyOutcome("failed", error="HTTP 503")
    assert _results(db, run_id) == {company_id: ("failed", 0, "HTTP 503")}
    assert _jobs(db, company_id) == {url: True}


@needs_db
def test_scraper_empty_is_ok_with_zero(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    url = _add_job(db, user["id"], company_id)
    run_id = _open_run(db, user["id"])

    _process(run_id, [company_id], _returns())
    assert _results(db, run_id) == {company_id: ("ok", 0, None)}
    assert _jobs(db, company_id) == {url: True}


@pytest.fixture
def use_handler(monkeypatch):
    """Register ``fn`` as the handler ``CUSTOM_SOURCE`` names."""

    def _use(fn):
        monkeypatch.setitem(handlers.HANDLERS, CUSTOM_SOURCE["handler"], fn)

    return _use


@needs_db
def test_custom_without_handler_is_skipped(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=CUSTOM_SOURCE)
    url = _add_job(db, user["id"], company_id)
    run_id = _open_run(db, user["id"])

    [outcome] = _process(run_id, [company_id], fetch_openings)
    assert outcome.status == "skipped"
    assert _results(db, run_id) == {
        company_id: ("skipped", 0, "no handler named 'acme' yet")
    }
    assert _jobs(db, company_id) == {url: True}


@needs_db
def test_custom_handler_success_inserts_and_closes(
    db, make_user, make_company, use_handler
):
    user = make_user()
    company_id = make_company(user["id"], source=CUSTOM_SOURCE)
    gone = _add_job(db, user["id"], company_id)
    new_url = _url()
    run_id = _open_run(db, user["id"])
    use_handler(lambda: [Opening("A", new_url)])

    [outcome] = _process(run_id, [company_id], fetch_openings)
    assert outcome.status == "ok"
    assert _results(db, run_id) == {company_id: ("ok", 1, None)}
    assert _jobs(db, company_id) == {gone: False, new_url: True}


@needs_db
def test_custom_handler_empty_success_closes_all(
    db, make_user, make_company, use_handler
):
    user = make_user()
    company_id = make_company(user["id"], source=CUSTOM_SOURCE)
    gone = _add_job(db, user["id"], company_id)
    run_id = _open_run(db, user["id"])
    use_handler(lambda: [])

    _process(run_id, [company_id], fetch_openings)
    assert _results(db, run_id) == {company_id: ("ok", 0, None)}
    assert _jobs(db, company_id) == {gone: False}


@needs_db
def test_custom_handler_failure_closes_nothing(
    db, make_user, make_company, use_handler
):
    user = make_user()
    company_id = make_company(user["id"], source=CUSTOM_SOURCE)
    url = _add_job(db, user["id"], company_id)
    run_id = _open_run(db, user["id"])

    def broken():
        raise FetchError("page changed")

    use_handler(broken)

    [outcome] = _process(run_id, [company_id], fetch_openings)
    assert outcome == CompanyOutcome("failed", error="page changed")
    assert _results(db, run_id) == {company_id: ("failed", 0, "page changed")}
    assert _jobs(db, company_id) == {url: True}


@needs_db
def test_invalid_stored_source_is_failed(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source={"kind": "board", "board": "nope"})
    run_id = _open_run(db, user["id"])

    _process(run_id, [company_id], _returns())
    assert _results(db, run_id) == {
        company_id: ("failed", 0, "stored source is invalid")
    }


@needs_db
def test_unexpected_fetch_error_is_failed(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    run_id = _open_run(db, user["id"])

    _process(run_id, [company_id], _raises(KeyError("bug")))
    assert _results(db, run_id) == {
        company_id: ("failed", 0, "unexpected error: KeyError")
    }


@needs_db
def test_db_error_rolls_back_company_and_next_company_still_runs(
    db, make_user, make_company
):
    user = make_user()
    broken = make_company(user["id"], name="Broken")
    fine = make_company(user["id"], name="Fine")
    existing = _add_job(db, user["id"], broken)
    run_id = _open_run(db, user["id"])
    good_url = _url()

    # Postgres text can't hold NUL, so the first company's insert fails.
    batches = iter([[Opening("Bad\x00title", _url())], [Opening("Good", good_url)]])

    outcomes = _process(run_id, [broken, fine], lambda source: next(batches))
    assert [o.status for o in outcomes] == ["failed", "ok"]
    results = _results(db, run_id)
    assert results[broken][0] == "failed"
    assert results[broken][2].startswith("saving jobs failed")
    assert results[fine] == ("ok", 1, None)
    # The broken company's changes rolled back: no insert, nothing closed.
    assert _jobs(db, broken) == {existing: True}
    assert _jobs(db, fine) == {good_url: True}


def test_fetch_openings_dispatches_by_kind(monkeypatch):
    board = BoardSource.model_validate(BOARD_SOURCE)
    scraper = ScraperSource.model_validate(SCRAPER_SOURCE)
    monkeypatch.setattr(company_run, "fetch_board", lambda s: [Opening("B", "b")])
    monkeypatch.setattr(company_run, "fetch_scraper", lambda s: [Opening("S", "s")])
    assert fetch_openings(board) == [Opening("B", "b")]
    assert fetch_openings(scraper) == [Opening("S", "s")]


def test_fetch_openings_runs_the_named_handler(use_handler):
    use_handler(lambda: [Opening("C", "c")])
    assert fetch_openings(CustomSource.model_validate(CUSTOM_SOURCE)) == [
        Opening("C", "c")
    ]


def test_fetch_custom_without_handler_raises():
    source = CustomSource(kind="custom", handler="nobody")
    with pytest.raises(handlers.NoHandlerError, match="'nobody'"):
        handlers.fetch_custom(source, {})


@pytest.mark.parametrize("status", ["ok", "failed", "skipped"])
def test_outcome_defaults(status):
    outcome = CompanyOutcome(status)
    assert (outcome.jobs_found, outcome.new_job_ids, outcome.error) == (0, (), None)
