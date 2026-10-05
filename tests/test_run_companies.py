"""BE-038: GET /runs/{id}/companies."""

import uuid

import psycopg
from fastapi.testclient import TestClient

from .conftest import needs_db

pytestmark = needs_db


def _make_run(db: psycopg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    row = db.execute(
        "INSERT INTO runs (user_id, status, trigger, finished_at)"
        " VALUES (%s, 'success', 'cron', now()) RETURNING id",
        (user_id,),
    ).fetchone()
    assert row is not None
    return row[0]


def _make_result(
    db: psycopg.Connection,
    run_id: uuid.UUID,
    company_id: uuid.UUID,
    status: str = "ok",
    jobs_found: int = 0,
    error: str | None = None,
) -> uuid.UUID:
    row = db.execute(
        "INSERT INTO run_company_results"
        " (run_id, company_id, company_name, status, jobs_found, error)"
        " VALUES (%s, %s, (SELECT name FROM companies WHERE id = %s), %s, %s, %s)"
        " RETURNING id",
        (run_id, company_id, company_id, status, jobs_found, error),
    ).fetchone()
    assert row is not None
    return row[0]


def test_requires_token(runner_client: TestClient):
    assert runner_client.get(f"/runs/{uuid.uuid4()}/companies").status_code == 401


def test_returns_breakdown(runner_client, db, make_user, make_company, auth_header):
    user = make_user()
    northstar = make_company(user["id"], name="Northstar")
    acme = make_company(user["id"], name="Acme")
    paused = make_company(user["id"], name="Zeta", active=False)
    run = _make_run(db, user["id"])
    ok = _make_result(db, run, northstar, jobs_found=0)
    failed = _make_result(db, run, acme, status="failed", error="HTTP 500")
    skipped = _make_result(db, run, paused, status="skipped")
    # Another run's results don't leak in.
    _make_result(db, _make_run(db, user["id"]), acme, jobs_found=9)

    resp = runner_client.get(f"/runs/{run}/companies", headers=auth_header(user["id"]))
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {
            "id": str(failed),
            "company_id": str(acme),
            "company": "Acme",
            "status": "failed",
            "jobs_found": 0,
            "error": "HTTP 500",
        },
        {
            "id": str(ok),
            "company_id": str(northstar),
            "company": "Northstar",
            "status": "ok",
            "jobs_found": 0,
            "error": None,
        },
        {
            "id": str(skipped),
            "company_id": str(paused),
            "company": "Zeta",
            "status": "skipped",
            "jobs_found": 0,
            "error": None,
        },
    ]


def test_run_with_no_results(runner_client, db, make_user, auth_header):
    user = make_user()
    run = _make_run(db, user["id"])

    resp = runner_client.get(f"/runs/{run}/companies", headers=auth_header(user["id"]))
    assert resp.status_code == 200
    assert resp.json() == []


def test_other_users_run_is_404(
    runner_client, db, make_user, make_company, auth_header
):
    owner, me = make_user(), make_user()
    run = _make_run(db, owner["id"])
    _make_result(db, run, make_company(owner["id"]))

    resp = runner_client.get(f"/runs/{run}/companies", headers=auth_header(me["id"]))
    assert resp.status_code == 404


def test_unknown_run_is_404(runner_client, make_user, auth_header):
    user = make_user()
    resp = runner_client.get(
        f"/runs/{uuid.uuid4()}/companies", headers=auth_header(user["id"])
    )
    assert resp.status_code == 404


def test_bad_run_id_is_422(runner_client, make_user, auth_header):
    user = make_user()
    resp = runner_client.get(
        "/runs/not-a-uuid/companies", headers=auth_header(user["id"])
    )
    assert resp.status_code == 422


def test_renamed_company_shows_current_name(
    runner_client, db, make_user, make_company, auth_header
):
    user = make_user()
    company = make_company(user["id"], name="Old Name")
    run = _make_run(db, user["id"])
    _make_result(db, run, company)
    db.execute("UPDATE companies SET name = 'New Name' WHERE id = %s", (company,))

    resp = runner_client.get(f"/runs/{run}/companies", headers=auth_header(user["id"]))
    assert resp.status_code == 200, resp.text
    [item] = resp.json()
    assert item["company"] == "New Name"
    assert item["company_id"] == str(company)


def test_deleted_company_shows_stored_name(
    runner_client, db, make_user, make_company, auth_header
):
    user = make_user()
    gone = make_company(user["id"], name="Gone Inc")
    kept = make_company(user["id"], name="Kept")
    run = _make_run(db, user["id"])
    _make_result(db, run, gone)
    _make_result(db, run, kept)
    db.execute("DELETE FROM companies WHERE id = %s", (gone,))

    resp = runner_client.get(f"/runs/{run}/companies", headers=auth_header(user["id"]))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [(i["company"], i["company_id"]) for i in items] == [
        ("Gone Inc", None),
        ("Kept", str(kept)),
    ]
