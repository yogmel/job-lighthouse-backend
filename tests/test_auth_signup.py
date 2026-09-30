"""BE-011: POST /auth/signup (email+password)."""

import uuid
from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.auth import decode_token
from job_lighthouse_backend.companies.auth.passwords import verify_password

from .conftest import TEST_JWT_SECRET, needs_db

PASSWORD = "a-long-enough-password"

pytestmark = needs_db


def _signup(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/auth/signup", json={"email": email, "password": password})


def _row(db: psycopg.Connection, email: str) -> tuple | None:
    return db.execute(
        "SELECT id, password_hash, google_id, email_verified FROM users"
        " WHERE lower(email) = lower(%s)",
        (email,),
    ).fetchone()


def test_signup_returns_201_and_token_for_new_user(
    companies_client: TestClient,
    db: psycopg.Connection,
    unique_email: Callable[[], str],
) -> None:
    email = unique_email()
    response = _signup(companies_client, email)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    row = _row(db, email)
    assert row is not None
    user_id, _, google_id, email_verified = row
    assert decode_token(body["access_token"], TEST_JWT_SECRET) == user_id
    assert google_id is None
    assert email_verified is False


def test_signup_stores_hash_not_plaintext(
    companies_client: TestClient,
    db: psycopg.Connection,
    unique_email: Callable[[], str],
) -> None:
    email = unique_email()
    assert _signup(companies_client, email).status_code == 201

    _, password_hash, _, _ = _row(db, email)
    assert password_hash
    assert password_hash != PASSWORD
    assert PASSWORD not in password_hash
    assert verify_password(PASSWORD, password_hash)


def test_signup_response_has_no_password_hash(
    companies_client: TestClient, unique_email: Callable[[], str]
) -> None:
    response = _signup(companies_client, unique_email())

    assert response.status_code == 201
    assert set(response.json()) == {"access_token", "token_type"}
    assert "password_hash" not in response.text
    assert "argon2" not in response.text


def test_duplicate_email_returns_409(
    companies_client: TestClient,
    db: psycopg.Connection,
    unique_email: Callable[[], str],
) -> None:
    email = unique_email()
    assert _signup(companies_client, email).status_code == 201

    response = _signup(companies_client, email, "a-different-password")

    assert response.status_code == 409
    assert response.json()["detail"] == "An account with this email already exists."
    count = db.execute(
        "SELECT count(*) FROM users WHERE lower(email) = lower(%s)", (email,)
    ).fetchone()[0]
    assert count == 1


def test_duplicate_email_different_case_returns_409(
    companies_client: TestClient, make_user, unique_email: Callable[[], str]
) -> None:
    email = unique_email()
    make_user(email=email)

    response = _signup(companies_client, email.upper())

    assert response.status_code == 409


def test_google_only_account_email_returns_409(
    companies_client: TestClient, make_user, unique_email: Callable[[], str]
) -> None:
    email = unique_email()
    make_user(email=email, password=None, google_id=f"google-{uuid.uuid4().hex}")

    response = _signup(companies_client, email)

    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "short"},  # below NewPassword minimum
        {"password": "x" * 257},  # above NewPassword maximum
        {"email": "not-an-email"},
        {"email": None},
        {"password": None},
    ],
)
def test_invalid_input_returns_422(
    companies_client: TestClient,
    db: psycopg.Connection,
    unique_email: Callable[[], str],
    payload: dict,
) -> None:
    email = unique_email()
    body = {"email": email, "password": PASSWORD, **payload}
    body = {k: v for k, v in body.items() if v is not None}

    response = companies_client.post("/auth/signup", json=body)

    assert response.status_code == 422
    assert _row(db, email) is None


def test_insert_race_on_unique_index_returns_409(
    companies_client: TestClient,
    make_user,
    unique_email: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the pre-check misses a concurrent signup, the unique index still wins."""
    from job_lighthouse_backend.companies.auth import signup

    async def _miss(session, email):
        return None

    email = unique_email()
    make_user(email=email)
    monkeypatch.setattr(signup, "get_user_by_email", _miss)

    response = _signup(companies_client, email.upper())

    assert response.status_code == 409
