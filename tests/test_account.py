"""BE-015: GET/PUT /account."""

import uuid

import psycopg
from fastapi.testclient import TestClient

from job_lighthouse_backend.companies.auth.passwords import verify_password

from .conftest import needs_db

pytestmark = needs_db

NEW_PASSWORD = "a brand new passphrase"


def _row(db: psycopg.Connection, user_id: uuid.UUID) -> tuple:
    return db.execute(
        "SELECT email, password_hash, email_verified FROM users WHERE id = %s",
        (user_id,),
    ).fetchone()


def test_get_account_returns_own_account(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.get("/account", headers=auth_header(user["id"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(user["id"])
    assert body["email"] == user["email"]
    assert body["email_verified"] is False
    assert body["has_password"] is True
    assert body["google_linked"] is False
    assert "created_at" in body
    assert "password_hash" not in body


def test_get_account_google_only_flags(companies_client, make_user, auth_header):
    user = make_user(password=None, google_id=f"g-{uuid.uuid4().hex}")
    body = companies_client.get("/account", headers=auth_header(user["id"])).json()
    assert body["has_password"] is False
    assert body["google_linked"] is True


def test_requires_token(companies_client: TestClient):
    assert companies_client.get("/account").status_code == 401
    resp = companies_client.put("/account", json={"new_password": NEW_PASSWORD})
    assert resp.status_code == 401


def test_nonexistent_user_is_404(companies_client, auth_header):
    headers = auth_header(uuid.uuid4())
    assert companies_client.get("/account", headers=headers).status_code == 404
    resp = companies_client.put(
        "/account", headers=headers, json={"new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 404


def test_change_password_with_current_password(
    companies_client, make_user, auth_header, db
):
    user = make_user()
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"current_password": user["password"], "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200
    assert "password_hash" not in resp.json()
    _, stored_hash, _ = _row(db, user["id"])
    assert verify_password(NEW_PASSWORD, stored_hash)
    assert not verify_password(user["password"], stored_hash)


def test_missing_or_wrong_current_password_is_403(
    companies_client, make_user, auth_header, db, unique_email
):
    user = make_user()
    before = _row(db, user["id"])
    headers = auth_header(user["id"])
    for body in (
        {"new_password": NEW_PASSWORD},
        {"current_password": "wrong password", "new_password": NEW_PASSWORD},
        {"email": unique_email()},
        {"current_password": "wrong password", "email": unique_email()},
    ):
        resp = companies_client.put("/account", headers=headers, json=body)
        assert resp.status_code == 403, body
        assert resp.json()["detail"] == "Current password is incorrect"
    assert _row(db, user["id"]) == before


def test_change_email(companies_client, make_user, auth_header, db, unique_email):
    user = make_user()
    db.execute("UPDATE users SET email_verified = true WHERE id = %s", (user["id"],))
    new_email = unique_email()
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"current_password": user["password"], "email": new_email},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == new_email
    assert resp.json()["email_verified"] is False
    email, _, verified = _row(db, user["id"])
    assert email == new_email
    assert verified is False


def test_change_to_own_email_different_case(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"current_password": user["password"], "email": user["email"].upper()},
    )
    assert resp.status_code == 200


def test_email_taken_by_other_user_is_409(companies_client, make_user, auth_header, db):
    other = make_user()
    user = make_user()
    before = _row(db, user["id"])
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"current_password": user["password"], "email": other["email"].upper()},
    )
    assert resp.status_code == 409
    assert _row(db, user["id"]) == before


def test_google_only_user_sets_first_password(
    companies_client, make_user, auth_header, db
):
    user = make_user(password=None, google_id=f"g-{uuid.uuid4().hex}")
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["has_password"] is True
    _, stored_hash, _ = _row(db, user["id"])
    assert verify_password(NEW_PASSWORD, stored_hash)


def test_empty_body_is_422(companies_client, make_user, auth_header):
    user = make_user()
    headers = auth_header(user["id"])
    for body in ({}, {"current_password": user["password"]}):
        resp = companies_client.put("/account", headers=headers, json=body)
        assert resp.status_code == 422, body


def test_short_new_password_is_422(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.put(
        "/account",
        headers=auth_header(user["id"]),
        json={"current_password": user["password"], "new_password": "short"},
    )
    assert resp.status_code == 422
