"""BE-023: close disappeared jobs (and reopen ones listed again)."""

import uuid

import psycopg
import pytest
from sqlalchemy import select

from job_lighthouse_backend.job_runner.models import Company
from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.sync import can_close, sync_active

from .aio import in_session
from .conftest import BOARD_SOURCE, needs_db

SCRAPER_SOURCE = {
    "kind": "scraper",
    "strategy": "static",
    "selectors": {
        "careers_url": "https://example.com/careers",
        "job": ".job",
        "title": ".title",
        "link": "a",
    },
}
CUSTOM_SOURCE = {"kind": "custom", "handler": "acme"}


def _sync(company_id: uuid.UUID, openings: list[Opening]) -> tuple[int, int]:
    async def fn(session):
        company = await session.scalar(select(Company).where(Company.id == company_id))
        return await sync_active(session, company, openings)

    return in_session(fn)


def _add_job(db, user_id, company_id, active=True) -> str:
    url = f"https://jobs.example/{uuid.uuid4().hex}"
    db.execute(
        "INSERT INTO jobs (user_id, company_id, company, title, url, location,"
        " description, active) VALUES (%s, %s, 'Acme', 'Job', %s, '', '', %s)",
        (user_id, company_id, url, active),
    )
    return url


def _active(db: psycopg.Connection, url: str) -> bool:
    row = db.execute("SELECT active FROM jobs WHERE url = %s", (url,)).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize(
    ("kind", "openings", "expected"),
    [
        ("board", [], True),
        ("board", [Opening("A", "https://x/1")], True),
        ("scraper", [], False),
        ("scraper", [Opening("A", "https://x/1")], True),
        ("custom", [], True),
        ("unknown", [Opening("A", "https://x/1")], False),
    ],
)
def test_can_close(kind, openings, expected):
    assert can_close(kind, openings) is expected


@needs_db
def test_board_closes_missing_keeps_listed(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=BOARD_SOURCE)
    kept, gone = (
        _add_job(db, user["id"], company_id),
        _add_job(db, user["id"], company_id),
    )

    assert _sync(company_id, [Opening("Job", kept)]) == (1, 0)
    assert _active(db, kept) is True
    assert _active(db, gone) is False


@needs_db
def test_board_empty_result_closes_everything(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=BOARD_SOURCE)
    urls = [_add_job(db, user["id"], company_id) for _ in range(2)]

    assert _sync(company_id, []) == (2, 0)
    assert [_active(db, u) for u in urls] == [False, False]


@needs_db
def test_scraper_empty_result_closes_nothing(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    url = _add_job(db, user["id"], company_id)

    assert _sync(company_id, []) == (0, 0)
    assert _active(db, url) is True


@needs_db
def test_scraper_non_empty_result_closes_missing(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    kept, gone = (
        _add_job(db, user["id"], company_id),
        _add_job(db, user["id"], company_id),
    )

    assert _sync(company_id, [Opening("Job", kept)]) == (1, 0)
    assert _active(db, kept) is True
    assert _active(db, gone) is False


@needs_db
def test_custom_success_closes_missing(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=CUSTOM_SOURCE)
    url = _add_job(db, user["id"], company_id)
    assert _sync(company_id, []) == (1, 0)
    assert _active(db, url) is False


@needs_db
def test_only_this_companys_jobs_are_closed(db, make_user, make_company):
    user, other_user = make_user(), make_user()
    company_id = make_company(user["id"])
    sibling = _add_job(db, user["id"], make_company(user["id"], name="Sibling"))
    foreign = _add_job(db, other_user["id"], make_company(other_user["id"]))

    _sync(company_id, [])
    assert _active(db, sibling) is True
    assert _active(db, foreign) is True


@needs_db
def test_listed_inactive_job_is_reopened(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    url = _add_job(db, user["id"], company_id, active=False)
    db.execute("UPDATE jobs SET notified_at = now() WHERE url = %s", (url,))

    assert _sync(company_id, [Opening("Job", url)]) == (0, 1)
    assert _active(db, url) is True
    # Already emailed once: reopening doesn't make it "new" again.
    row = db.execute("SELECT notified_at FROM jobs WHERE url = %s", (url,)).fetchone()
    assert row is not None and row[0] is not None


@needs_db
def test_reopen_ignores_other_companies(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    other = make_company(user["id"], name="Other")
    url = _add_job(db, user["id"], other, active=False)

    assert _sync(company_id, [Opening("Job", url)]) == (0, 0)
    assert _active(db, url) is False


@needs_db
def test_already_inactive_missing_job_is_not_counted(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    _add_job(db, user["id"], company_id, active=False)
    assert _sync(company_id, []) == (0, 0)
