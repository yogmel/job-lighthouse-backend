"""BE-043: GET /account/export."""

import uuid

import psycopg
from fastapi.testclient import TestClient

from .conftest import BOARD_SOURCE, needs_db

pytestmark = needs_db


def _seed(db: psycopg.Connection, make_company, make_job, user_id) -> dict:
    """One of every owned row for ``user_id``. Returns their ids."""
    db.execute(
        "INSERT INTO config (user_id, location, cron, profile)"
        " VALUES (%s, 'Remote', '0 8 * * *', 'my profile')",
        (user_id,),
    )
    company_id = make_company(user_id)
    job_id = make_job(user_id, company_id)
    run = db.execute(
        "INSERT INTO runs (user_id, status, trigger, finished_at)"
        " VALUES (%s, 'success', 'manual', now()) RETURNING id",
        (user_id,),
    ).fetchone()
    assert run is not None
    result = db.execute(
        "INSERT INTO run_company_results (run_id, company_id, status, jobs_found)"
        " VALUES (%s, %s, 'ok', 1) RETURNING id",
        (run[0], company_id),
    ).fetchone()
    assert result is not None
    db.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)"
        " VALUES (%s, %s, now())",
        (user_id, uuid.uuid4().hex),
    )
    return {
        "company": str(company_id),
        "job": str(job_id),
        "run": str(run[0]),
        "result": str(result[0]),
    }


def _export(client: TestClient, headers: dict) -> dict:
    resp = client.get("/account/export", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_requires_token(companies_client: TestClient):
    assert companies_client.get("/account/export").status_code == 401


def test_exports_every_owned_table(
    companies_client, make_user, make_company, make_job, auth_header, db
):
    user = make_user()
    ids = _seed(db, make_company, make_job, user["id"])

    body = _export(companies_client, auth_header(user["id"]))
    assert set(body) == {
        "exported_at",
        "account",
        "config",
        "companies",
        "jobs",
        "runs",
        "run_company_results",
    }
    assert body["account"]["id"] == str(user["id"])
    assert body["account"]["email"] == user["email"]
    assert body["config"]["profile"] == "my profile"
    assert body["config"]["user_id"] == str(user["id"])
    [company] = body["companies"]
    assert (company["id"], company["source"]) == (ids["company"], BOARD_SOURCE)
    [job] = body["jobs"]
    assert (job["id"], job["company_id"]) == (ids["job"], ids["company"])
    assert {"url", "match_score", "notified_at", "active"} <= set(job)
    [run] = body["runs"]
    assert (run["id"], run["status"]) == (ids["run"], "success")
    [result] = body["run_company_results"]
    assert (result["id"], result["run_id"]) == (ids["result"], ids["run"])


def test_leaves_out_secrets(
    companies_client, make_user, make_company, make_job, auth_header, db
):
    user = make_user()
    _seed(db, make_company, make_job, user["id"])
    password_hash = db.execute(
        "SELECT password_hash FROM users WHERE id = %s", (user["id"],)
    ).fetchone()
    assert password_hash is not None

    resp = companies_client.get("/account/export", headers=auth_header(user["id"]))
    assert "password_hash" not in resp.text
    assert password_hash[0] not in resp.text
    assert "token_hash" not in resp.text


def test_only_the_callers_rows(
    companies_client, make_user, make_company, make_job, auth_header, db
):
    me, other = make_user(), make_user()
    mine = _seed(db, make_company, make_job, me["id"])
    theirs = _seed(db, make_company, make_job, other["id"])

    body = _export(companies_client, auth_header(me["id"]))
    text = str(body)
    for row_id in theirs.values():
        assert row_id not in text
    assert str(other["id"]) not in text
    assert [c["id"] for c in body["companies"]] == [mine["company"]]
    assert [r["id"] for r in body["run_company_results"]] == [mine["result"]]


def test_new_account_exports_empty(companies_client, make_user, auth_header):
    user = make_user()
    body = _export(companies_client, auth_header(user["id"]))
    assert body["config"] is None
    for table in ("companies", "jobs", "runs", "run_company_results"):
        assert body[table] == []


def test_unknown_user_is_404(companies_client, auth_header):
    resp = companies_client.get("/account/export", headers=auth_header(uuid.uuid4()))
    assert resp.status_code == 404
