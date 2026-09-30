"""BE-022: diff fetched openings by URL and insert new Jobs."""

import uuid

import psycopg
from sqlalchemy import select

from job_lighthouse_backend.job_runner.models import Company
from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.sync import insert_new_jobs

from .aio import in_session
from .conftest import needs_db

pytestmark = needs_db


def _insert(company_id: uuid.UUID, openings: list[Opening]) -> list[uuid.UUID]:
    async def fn(session):
        company = await session.scalar(select(Company).where(Company.id == company_id))
        return await insert_new_jobs(session, company, openings)

    return in_session(fn)


def _jobs(db: psycopg.Connection, user_id: uuid.UUID) -> list[tuple]:
    return db.execute(
        "SELECT company_id, company, title, url, location, description, active,"
        " match_score, match_description, profile_version, notified_at"
        " FROM jobs WHERE user_id = %s ORDER BY url",
        (user_id,),
    ).fetchall()


def _url() -> str:
    return f"https://jobs.example/{uuid.uuid4().hex}"


def test_inserts_new_openings_with_scoring_unset(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"], name="Acme")
    a, b = sorted([_url(), _url()])
    ids = _insert(
        company_id,
        [Opening("Engineer", a, "Berlin", "Build"), Opening("Designer", b)],
    )
    assert len(ids) == 2
    assert _jobs(db, user["id"]) == [
        (company_id, "Acme", "Engineer", a, "Berlin", "Build", True) + (None,) * 4,
        (company_id, "Acme", "Designer", b, "", "", True) + (None,) * 4,
    ]


def test_existing_url_is_not_reinserted_or_changed(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    old, new = _url(), _url()
    _insert(company_id, [Opening("Engineer", old)])
    ids = _insert(company_id, [Opening("Renamed", old), Opening("New", new)])

    assert len(ids) == 1
    rows = {r[3]: r for r in _jobs(db, user["id"])}
    assert len(rows) == 2
    # No content diffing: the stored title stays.
    assert rows[old][2] == "Engineer"
    assert rows[new][2] == "New"
    assert ids == [
        db.execute("SELECT id FROM jobs WHERE url = %s", (new,)).fetchone()[0]
    ]


def test_rerun_with_same_openings_inserts_nothing(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    openings = [Opening("A", _url()), Opening("B", _url())]
    _insert(company_id, openings)
    assert _insert(company_id, openings) == []
    assert len(_jobs(db, user["id"])) == 2


def test_duplicate_url_in_one_fetch_is_one_job(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    url = _url()
    ids = _insert(company_id, [Opening("First", url), Opening("Second", url)])
    assert len(ids) == 1
    [row] = _jobs(db, user["id"])
    assert row[2] == "First"


def test_inactive_existing_job_is_not_duplicated(db, make_user, make_company):
    user = make_user()
    company_id = make_company(user["id"])
    url = _url()
    _insert(company_id, [Opening("A", url)])
    db.execute("UPDATE jobs SET active = false WHERE url = %s", (url,))
    assert _insert(company_id, [Opening("A", url)]) == []
    assert len(_jobs(db, user["id"])) == 1


def test_url_owned_by_another_company_of_same_user_is_kept(db, make_user, make_company):
    user = make_user()
    first = make_company(user["id"], name="First")
    second = make_company(user["id"], name="Second")
    url = _url()
    _insert(first, [Opening("A", url)])
    assert _insert(second, [Opening("A", url)]) == []
    [row] = _jobs(db, user["id"])
    assert row[0] == first


def test_same_url_for_two_users_gives_two_jobs(db, make_user, make_company):
    alice, bob = make_user(), make_user()
    url = _url()
    assert len(_insert(make_company(alice["id"]), [Opening("A", url)])) == 1
    assert len(_insert(make_company(bob["id"]), [Opening("A", url)])) == 1
    assert len(_jobs(db, alice["id"])) == 1
    assert len(_jobs(db, bob["id"])) == 1


def test_no_openings_is_a_noop(db, make_user, make_company):
    user = make_user()
    assert _insert(make_company(user["id"]), []) == []
    assert _jobs(db, user["id"]) == []
