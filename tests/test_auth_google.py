"""POST /auth/google. Google verification is stubbed: no network.

Route tests need Postgres (skipped via the ``db`` fixture otherwise); the
``verify_google_id_token`` unit tests don't.
"""

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.auth import decode_token
from job_lighthouse_backend.companies.auth import google as google_auth
from job_lighthouse_backend.companies.auth.google import GoogleIdentity

from .conftest import TEST_JWT_SECRET

TEST_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture
def google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", TEST_CLIENT_ID)


@pytest.fixture
def companies_client(google_env: None, companies_client: TestClient) -> TestClient:
    # Same client as conftest's, but with GOOGLE_CLIENT_ID set before startup.
    return companies_client


@pytest.fixture
def stub_google(monkeypatch: pytest.MonkeyPatch) -> Callable[..., GoogleIdentity]:
    """Make the verifier return the given identity for any token."""

    def _stub(email: str, sub: str | None = None, verified: bool = True):
        identity = GoogleIdentity(
            sub=sub or f"google-{uuid.uuid4().hex}",
            email=email,
            email_verified=verified,
        )

        def fake(token: str, client_id: str) -> GoogleIdentity:
            assert client_id == TEST_CLIENT_ID
            return identity

        monkeypatch.setattr(google_auth, "verify_google_id_token", fake)
        return identity

    return _stub


def _login(client: TestClient) -> uuid.UUID:
    resp = client.post("/auth/google", json={"id_token": "fake-token"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "password_hash" not in body
    return decode_token(body["access_token"], TEST_JWT_SECRET)


def _rows_for(db: psycopg.Connection, sub: str) -> list[tuple]:
    return db.execute(
        "SELECT id, email, password_hash, google_id, email_verified"
        " FROM users WHERE google_id = %s",
        (sub,),
    ).fetchall()


def test_first_login_creates_google_only_user(
    companies_client: TestClient,
    db: psycopg.Connection,
    stub_google: Callable[..., GoogleIdentity],
    unique_email: Callable[[], str],
) -> None:
    identity = stub_google(unique_email())

    user_id = _login(companies_client)

    rows = _rows_for(db, identity.sub)
    assert len(rows) == 1
    row_id, email, password_hash, google_id, email_verified = rows[0]
    assert row_id == user_id
    assert email == identity.email
    assert password_hash is None
    assert google_id == identity.sub
    assert email_verified is True


def test_repeat_login_matches_existing_row(
    companies_client: TestClient,
    db: psycopg.Connection,
    stub_google: Callable[..., GoogleIdentity],
    unique_email: Callable[[], str],
) -> None:
    identity = stub_google(unique_email())

    first = _login(companies_client)
    second = _login(companies_client)

    assert first == second
    assert len(_rows_for(db, identity.sub)) == 1


def test_verified_email_links_existing_password_account(
    companies_client: TestClient,
    db: psycopg.Connection,
    stub_google: Callable[..., GoogleIdentity],
    make_user: Callable[..., dict],
    unique_email: Callable[[], str],
) -> None:
    existing = make_user(email=unique_email())
    # Different casing still matches the same account.
    identity = stub_google(existing["email"].upper(), verified=True)

    user_id = _login(companies_client)

    assert user_id == existing["id"]
    row = db.execute(
        "SELECT google_id, email_verified, password_hash FROM users WHERE id = %s",
        (existing["id"],),
    ).fetchone()
    assert row is not None
    assert row[0] == identity.sub
    assert row[1] is True
    assert row[2] is not None  # password login keeps working
    count_row = db.execute(
        "SELECT count(*) FROM users WHERE lower(email) = lower(%s)",
        (existing["email"],),
    ).fetchone()
    assert count_row is not None
    count = count_row[0]
    assert count == 1


def test_unverified_email_with_existing_account_conflicts(
    companies_client: TestClient,
    db: psycopg.Connection,
    stub_google: Callable[..., GoogleIdentity],
    make_user: Callable[..., dict],
    unique_email: Callable[[], str],
) -> None:
    existing = make_user(email=unique_email())
    identity = stub_google(existing["email"], verified=False)

    resp = companies_client.post("/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 409
    assert "Log in with your password" in resp.json()["detail"]
    row = db.execute(
        "SELECT google_id FROM users WHERE id = %s", (existing["id"],)
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert _rows_for(db, identity.sub) == []


def test_email_linked_to_other_google_account_conflicts(
    companies_client: TestClient,
    db: psycopg.Connection,
    stub_google: Callable[..., GoogleIdentity],
    make_user: Callable[..., dict],
    unique_email: Callable[[], str],
) -> None:
    other_sub = f"google-{uuid.uuid4().hex}"
    existing = make_user(email=unique_email(), password=None, google_id=other_sub)
    stub_google(existing["email"], verified=True)

    resp = companies_client.post("/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 409
    row = db.execute(
        "SELECT google_id FROM users WHERE id = %s", (existing["id"],)
    ).fetchone()
    assert row is not None
    assert row[0] == other_sub


def test_invalid_token_returns_401(
    companies_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(token: str, client_id: str) -> GoogleIdentity:
        raise ValueError("Token expired")

    monkeypatch.setattr(google_auth, "verify_google_id_token", fake)

    resp = companies_client.post("/auth/google", json={"id_token": "bad"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid Google token"


def test_missing_client_id_returns_503(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_lighthouse_backend.companies.main import app

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    def fail(token: str, client_id: str) -> GoogleIdentity:
        raise AssertionError("must not verify without a client id")

    monkeypatch.setattr(google_auth, "verify_google_id_token", fail)

    with TestClient(app) as client:
        resp = client.post("/auth/google", json={"id_token": "whatever"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Google sign-in is not configured"


# --- verify_google_id_token (unit, no DB, no network) ---


@pytest.fixture
def fake_verify(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Stub the library call; returns the dict of captured args / claims."""
    state: dict = {
        "claims": {
            "iss": "https://accounts.google.com",
            "sub": "12345",
            "email": "someone@example.com",
            "email_verified": True,
        }
    }

    def fake(token, request, audience=None, **kwargs):
        state["token"] = token
        state["audience"] = audience
        return state["claims"]

    monkeypatch.setattr(google_auth.id_token, "verify_oauth2_token", fake)
    yield state


def test_verify_passes_audience(fake_verify: dict) -> None:
    identity = google_auth.verify_google_id_token("tok", TEST_CLIENT_ID)

    assert fake_verify["token"] == "tok"
    assert fake_verify["audience"] == TEST_CLIENT_ID
    assert identity == GoogleIdentity(
        sub="12345", email="someone@example.com", email_verified=True
    )


def test_verify_rejects_wrong_issuer(fake_verify: dict) -> None:
    fake_verify["claims"]["iss"] = "https://evil.example.com"

    with pytest.raises(ValueError):
        google_auth.verify_google_id_token("tok", TEST_CLIENT_ID)


def test_verify_requires_email(fake_verify: dict) -> None:
    del fake_verify["claims"]["email"]

    with pytest.raises(ValueError):
        google_auth.verify_google_id_token("tok", TEST_CLIENT_ID)
