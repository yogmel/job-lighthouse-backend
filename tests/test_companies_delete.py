"""BE-056: DELETE /companies/{id}."""

import uuid

import psycopg
from fastapi.testclient import TestClient

from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.runs import lock_name
from job_lighthouse_backend.job_runner.runs_api import get_fetcher

from .conftest import BOARD_SOURCE, needs_db, wait_for_run

pytestmark = needs_db


def _count(db: psycopg.Connection, table: str, column: str, value) -> int:
    row = db.execute(
        f"SELECT count(*) FROM {table} WHERE {column} = %s",  # noqa: S608 -- test helper, fixed names
        (value,),
    ).fetchone()
    assert row is not None
    return row[0]


def _delete(client, company_id, headers) -> int:
    return client.delete(f"/companies/{company_id}", headers=headers).status_code


def test_requires_token(companies_client: TestClient):
    assert companies_client.delete(f"/companies/{uuid.uuid4()}").status_code == 401


def test_deletes_company_and_its_jobs(
    companies_client, db, make_user, make_company, make_job, auth_header
):
    user = make_user()
    company_id = make_company(user["id"])
    other_id = make_company(user["id"], name="Other")
    make_job(user["id"], company_id)
    make_job(user["id"], company_id, active=False)
    kept = make_job(user["id"], other_id)

    resp = companies_client.delete(
        f"/companies/{company_id}", headers=auth_header(user["id"])
    )

    assert resp.status_code == 204
    assert resp.content == b""
    assert _count(db, "companies", "id", company_id) == 0
    assert _count(db, "jobs", "company_id", company_id) == 0
    assert _count(db, "jobs", "id", kept) == 1
    assert _count(db, "companies", "id", other_id) == 1


def test_missing_company_is_404(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.delete(
        f"/companies/{uuid.uuid4()}", headers=auth_header(user["id"])
    )
    assert resp.status_code == 404


def test_other_users_company_is_404_and_untouched(
    companies_client, db, make_user, make_company, make_job, auth_header
):
    owner, intruder = make_user(), make_user()
    company_id = make_company(owner["id"])
    make_job(owner["id"], company_id)

    resp = companies_client.delete(
        f"/companies/{company_id}", headers=auth_header(intruder["id"])
    )

    assert resp.status_code == 404
    assert _count(db, "companies", "id", company_id) == 1
    assert _count(db, "jobs", "company_id", company_id) == 1


def test_bad_id_is_422(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.delete("/companies/nope", headers=auth_header(user["id"]))
    assert resp.status_code == 422


def test_run_in_progress_is_409_and_lock_is_released(
    companies_client, db, make_user, make_company, make_job, auth_header
):
    user = make_user()
    company_id = make_company(user["id"])
    make_job(user["id"], company_id)
    # Another session plays the running run: it holds the user's lock.
    with psycopg.connect(db.info.dsn, password=db.info.password) as holder:
        holder.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (lock_name(user["id"]),),
        )
        resp = companies_client.delete(
            f"/companies/{company_id}", headers=auth_header(user["id"])
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "A run is in progress"
        assert _count(db, "companies", "id", company_id) == 1
        assert _count(db, "jobs", "company_id", company_id) == 1
        holder.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
            (lock_name(user["id"]),),
        )

    # A successful delete frees the lock again.
    resp = companies_client.delete(
        f"/companies/{company_id}", headers=auth_header(user["id"])
    )
    assert resp.status_code == 204
    free = db.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_name(user["id"]),)
    ).fetchone()
    assert free == (True,)
    db.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_name(user["id"]),)
    )


def test_run_history_stays(companies_client, db, make_user, make_company, auth_header):
    user = make_user()
    company_id = make_company(user["id"], name="Stripe")
    run_id = db.execute(
        "INSERT INTO runs (user_id, status, trigger, jobs_found)"
        " VALUES (%s, 'success', 'manual', 3) RETURNING id",
        (user["id"],),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO run_company_results"
        " (run_id, company_id, company_name, status, jobs_found)"
        " VALUES (%s, %s, 'Stripe', 'ok', 3)",
        (run_id, company_id),
    )

    resp = companies_client.delete(
        f"/companies/{company_id}", headers=auth_header(user["id"])
    )
    assert resp.status_code == 204

    assert db.execute(
        "SELECT jobs_found, status FROM runs WHERE id = %s", (run_id,)
    ).fetchone() == (3, "success")
    assert db.execute(
        "SELECT company_id, company_name, jobs_found FROM run_company_results"
        " WHERE run_id = %s",
        (run_id,),
    ).fetchall() == [(None, "Stripe", 3)]


def test_run_breakdown_shows_stored_name_with_null_id(
    companies_client, runner_client, db, make_user, make_company, auth_header
):
    user = make_user()
    company_id = make_company(user["id"], name="Stripe")
    run_id = db.execute(
        "INSERT INTO runs (user_id, status, trigger) VALUES (%s, 'success', 'manual')"
        " RETURNING id",
        (user["id"],),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO run_company_results (run_id, company_id, company_name, status)"
        " VALUES (%s, %s, 'Stripe', 'ok')",
        (run_id, company_id),
    )
    headers = auth_header(user["id"])
    assert _delete(companies_client, company_id, headers) == 204

    resp = runner_client.get(f"/runs/{run_id}/companies", headers=headers)

    assert resp.status_code == 200
    (row,) = resp.json()
    assert row["company_id"] is None
    assert row["company"] == "Stripe"


def test_readding_the_company_stores_postings_as_new_jobs(
    companies_client, runner_client, db, make_user, make_company, make_job, auth_header
):
    from job_lighthouse_backend.job_runner.main import app as runner_app

    user = make_user()
    headers = auth_header(user["id"])
    company_id = make_company(
        user["id"], name="Acme", source={**BOARD_SOURCE, "board_id": "acme"}
    )
    acme = {**BOARD_SOURCE, "board_id": "acme"}
    posting = f"https://jobs.example/{uuid.uuid4().hex}"
    db.execute(
        "INSERT INTO jobs (user_id, company_id, company, title, url, location,"
        " description) VALUES (%s, %s, 'Acme', 'Old', %s, '', '')",
        (user["id"], company_id, posting),
    )
    assert _delete(companies_client, company_id, headers) == 204

    created = companies_client.post(
        "/companies",
        headers=headers,
        json={
            "name": "Acme",
            "tier": 1,
            "website_url": "https://acme.example/",
            "active": True,
            "source": acme,
        },
    )
    assert created.status_code == 201
    new_id = created.json()["id"]

    runner_app.dependency_overrides[get_fetcher] = lambda: (
        lambda source: [Opening("New", posting)]
    )
    try:
        resp = runner_client.post("/runs", headers=headers)
        assert resp.status_code == 202
        assert wait_for_run(db, resp.json()["id"])[:2] == ("success", 1)
    finally:
        runner_app.dependency_overrides.clear()

    rows = db.execute(
        "SELECT company_id::text, title FROM jobs WHERE url = %s", (posting,)
    ).fetchall()
    assert rows == [(new_id, "New")]


def test_export_no_longer_has_company_or_jobs(
    companies_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    headers = auth_header(user["id"])
    company_id = make_company(user["id"])
    make_job(user["id"], company_id)
    assert _delete(companies_client, company_id, headers) == 204

    body = companies_client.get("/account/export", headers=headers).json()

    assert body["companies"] == []
    assert body["jobs"] == []
