"""BE-018: PUT /companies/{id}."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import BOARD_SOURCE, needs_db

pytestmark = needs_db

SCRAPER_SOURCE = {
    "kind": "scraper",
    "strategy": "static",
    "selectors": {
        "careers_url": "https://example.com/careers",
        "job": ".job",
        "title": ".title",
        "link": "a",
        "location": None,
    },
}


def _stored(db: psycopg.Connection, company_id: uuid.UUID) -> tuple:
    row = db.execute(
        "SELECT name, tier, website_url, active, source FROM companies WHERE id = %s",
        (company_id,),
    ).fetchone()
    assert row is not None
    return row


def _add_job(db: psycopg.Connection, user_id: uuid.UUID, company_id: uuid.UUID):
    row = db.execute(
        "INSERT INTO jobs (user_id, company_id, company, title, url, location,"
        " description) VALUES (%s, %s, 'Stripe', 'Engineer', %s, 'Remote', '')"
        " RETURNING id",
        (user_id, company_id, f"https://example.com/jobs/{uuid.uuid4().hex}"),
    ).fetchone()
    assert row is not None
    return row[0]


def _job_active(db: psycopg.Connection, job_id: uuid.UUID) -> bool:
    row = db.execute("SELECT active FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row is not None
    return row[0]


def test_requires_token(companies_client: TestClient):
    resp = companies_client.put(f"/companies/{uuid.uuid4()}", json={"tier": 2})
    assert resp.status_code == 401


def test_tier_only_change(companies_client, make_user, make_company, auth_header, db):
    user = make_user()
    company_id = make_company(user["id"])
    before = _stored(db, company_id)

    resp = companies_client.put(
        f"/companies/{company_id}", headers=auth_header(user["id"]), json={"tier": 3}
    )
    assert resp.status_code == 200
    assert resp.json()["tier"] == 3
    assert _stored(db, company_id) == (before[0], 3, *before[2:])


def test_change_every_field(companies_client, make_user, make_company, auth_header, db):
    user = make_user()
    company_id = make_company(user["id"])
    resp = companies_client.put(
        f"/companies/{company_id}",
        headers=auth_header(user["id"]),
        json={
            "name": "Acme",
            "tier": 2,
            "website_url": "https://acme.example/",
            "active": False,
            "source": SCRAPER_SOURCE,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(company_id)
    assert body["source"] == SCRAPER_SOURCE
    assert _stored(db, company_id) == (
        "Acme",
        2,
        "https://acme.example/",
        False,
        SCRAPER_SOURCE,
    )


def test_source_is_replaced_not_merged(
    companies_client, make_user, make_company, auth_header, db
):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    resp = companies_client.put(
        f"/companies/{company_id}",
        headers=auth_header(user["id"]),
        json={"source": BOARD_SOURCE},
    )
    assert resp.status_code == 200
    assert _stored(db, company_id)[4] == BOARD_SOURCE


def test_other_users_company_is_404(
    companies_client, make_user, make_company, auth_header, db
):
    owner, intruder = make_user(), make_user()
    company_id = make_company(owner["id"])
    before = _stored(db, company_id)

    resp = companies_client.put(
        f"/companies/{company_id}",
        headers=auth_header(intruder["id"]),
        json={"tier": 9, "active": False},
    )
    assert resp.status_code == 404
    assert _stored(db, company_id) == before


def test_missing_company_is_404(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.put(
        f"/companies/{uuid.uuid4()}", headers=auth_header(user["id"]), json={"tier": 2}
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "source",
    [
        pytest.param({"kind": "custom", "handler": "scrape_google"}, id="custom"),
        pytest.param({"kind": "board", "board": "lever"}, id="missing-board-id"),
        pytest.param({"kind": "rss"}, id="unknown-kind"),
    ],
)
def test_malformed_source_is_400(
    companies_client, make_user, make_company, auth_header, db, source
):
    user = make_user()
    company_id = make_company(user["id"])
    before = _stored(db, company_id)
    resp = companies_client.put(
        f"/companies/{company_id}",
        headers=auth_header(user["id"]),
        json={"source": source},
    )
    assert resp.status_code == 400
    assert _stored(db, company_id) == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty"),
        pytest.param({"name": None}, id="null-name"),
        pytest.param({"active": None}, id="null-active"),
        pytest.param({"source": None}, id="null-source"),
        pytest.param({"name": ""}, id="blank-name"),
        pytest.param({"website_url": "nope"}, id="bad-website"),
    ],
)
def test_invalid_body_is_422(
    companies_client, make_user, make_company, auth_header, body
):
    user = make_user()
    company_id = make_company(user["id"])
    resp = companies_client.put(
        f"/companies/{company_id}", headers=auth_header(user["id"]), json=body
    )
    assert resp.status_code == 422


def test_bad_id_is_422(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.put(
        "/companies/not-a-uuid", headers=auth_header(user["id"]), json={"tier": 2}
    )
    assert resp.status_code == 422


def test_pause_closes_jobs_and_resume_keeps_them_closed(
    companies_client, make_user, make_company, auth_header, db
):
    user = make_user()
    company_id = make_company(user["id"])
    other_company_id = make_company(user["id"], name="Other")
    job = _add_job(db, user["id"], company_id)
    other_job = _add_job(db, user["id"], other_company_id)
    headers = auth_header(user["id"])

    resp = companies_client.put(
        f"/companies/{company_id}", headers=headers, json={"active": False}
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert _job_active(db, job) is False
    assert _job_active(db, other_job) is True

    resp = companies_client.put(
        f"/companies/{company_id}", headers=headers, json={"active": True}
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is True
    assert _job_active(db, job) is False


def test_non_pause_edit_leaves_jobs_alone(
    companies_client, make_user, make_company, auth_header, db
):
    user = make_user()
    company_id = make_company(user["id"])
    job = _add_job(db, user["id"], company_id)
    resp = companies_client.put(
        f"/companies/{company_id}",
        headers=auth_header(user["id"]),
        json={"tier": 2, "active": True},
    )
    assert resp.status_code == 200
    assert _job_active(db, job) is True
