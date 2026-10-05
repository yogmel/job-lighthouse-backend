"""BE-044: DELETE /account."""

import uuid

import psycopg
from fastapi.testclient import TestClient

from .conftest import needs_db

pytestmark = needs_db

OWNED = (
    "config",
    "companies",
    "jobs",
    "runs",
    "password_reset_tokens",
)


def _seed(db: psycopg.Connection, make_company, make_job, user_id) -> uuid.UUID:
    """One of every owned row for ``user_id``. Returns the run id."""
    db.execute(
        "INSERT INTO config (user_id, location, cron)"
        " VALUES (%s, 'Remote', '0 8 * * *')",
        (user_id,),
    )
    company_id = make_company(user_id)
    make_job(user_id, company_id)
    run = db.execute(
        "INSERT INTO runs (user_id, status, trigger) VALUES (%s, 'success', 'manual')"
        " RETURNING id",
        (user_id,),
    ).fetchone()
    assert run is not None
    db.execute(
        "INSERT INTO run_company_results (run_id, company_id, status)"
        " VALUES (%s, %s, 'ok')",
        (run[0], company_id),
    )
    db.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)"
        " VALUES (%s, %s, now())",
        (user_id, uuid.uuid4().hex),
    )
    return run[0]


def _counts(db: psycopg.Connection, user_id, run_id) -> dict[str, int]:
    counts = {}
    for name in OWNED:
        row = db.execute(
            f"SELECT count(*) FROM {name} WHERE user_id = %s",  # noqa: S608 -- table names are constants
            (user_id,),
        ).fetchone()
        assert row is not None
        counts[name] = row[0]
    row = db.execute(
        "SELECT count(*) FROM run_company_results WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    counts["run_company_results"] = row[0]
    row = db.execute("SELECT count(*) FROM users WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    counts["users"] = row[0]
    return counts


def _delete(client: TestClient, headers: dict, body: dict | None = None):
    return client.request("DELETE", "/account", headers=headers, json=body)


def test_requires_token(companies_client: TestClient):
    assert companies_client.delete("/account").status_code == 401


def test_delete_cascades_to_every_owned_row(
    companies_client, make_user, make_company, make_job, auth_header, db
):
    user = make_user()
    run_id = _seed(db, make_company, make_job, user["id"])
    assert set(_counts(db, user["id"], run_id).values()) == {1}

    resp = _delete(
        companies_client,
        auth_header(user["id"]),
        {"current_password": user["password"]},
    )
    assert resp.status_code == 204
    assert set(_counts(db, user["id"], run_id).values()) == {0}


def test_other_users_rows_are_kept(
    companies_client, make_user, make_company, make_job, auth_header, db
):
    me, other = make_user(), make_user()
    _seed(db, make_company, make_job, me["id"])
    their_run = _seed(db, make_company, make_job, other["id"])

    resp = _delete(
        companies_client, auth_header(me["id"]), {"current_password": me["password"]}
    )
    assert resp.status_code == 204
    assert set(_counts(db, other["id"], their_run).values()) == {1}


def test_token_is_rejected_right_after(
    companies_client, runner_client, make_user, auth_header
):
    user = make_user()
    headers = auth_header(user["id"])
    assert (
        _delete(
            companies_client, headers, {"current_password": user["password"]}
        ).status_code
        == 204
    )

    # Both services, before the token expires.
    assert companies_client.get("/account", headers=headers).status_code == 401
    assert companies_client.get("/companies", headers=headers).status_code == 401
    assert runner_client.get("/jobs", headers=headers).status_code == 401
    assert _delete(companies_client, headers).status_code == 401


def test_wrong_or_missing_password_is_403(companies_client, make_user, auth_header, db):
    user = make_user()
    headers = auth_header(user["id"])
    assert _delete(companies_client, headers).status_code == 403
    resp = _delete(companies_client, headers, {"current_password": "wrong password"})
    assert resp.status_code == 403
    row = db.execute("SELECT 1 FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row is not None


def test_google_only_account_needs_no_password(
    companies_client, make_user, auth_header, db
):
    user = make_user(password=None, google_id=f"google-{uuid.uuid4().hex}")
    assert _delete(companies_client, auth_header(user["id"])).status_code == 204
    row = db.execute("SELECT 1 FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row is None


def test_email_can_sign_up_again(companies_client, make_user, auth_header):
    user = make_user()
    _delete(
        companies_client,
        auth_header(user["id"]),
        {"current_password": user["password"]},
    )
    resp = companies_client.post(
        "/auth/signup", json={"email": user["email"], "password": user["password"]}
    )
    assert resp.status_code == 201
