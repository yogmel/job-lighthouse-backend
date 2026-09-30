"""BE-012: POST /auth/login."""

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.auth import decode_token
from job_lighthouse_backend.companies.auth import login as login_module

from .conftest import TEST_JWT_SECRET, needs_db

pytestmark = needs_db

EXPECTED_401 = {"detail": "Invalid email or password"}


def _login(client: TestClient, email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_login_success_returns_token_for_user(
    companies_client: TestClient, make_user: Callable[..., dict]
) -> None:
    user = make_user()
    response = _login(companies_client, user["email"], user["password"])

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert decode_token(body["access_token"], TEST_JWT_SECRET) == user["id"]


def test_login_email_is_case_insensitive(
    companies_client: TestClient, make_user: Callable[..., dict]
) -> None:
    user = make_user()
    response = _login(companies_client, user["email"].upper(), user["password"])

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert decode_token(token, TEST_JWT_SECRET) == user["id"]


def test_failures_are_indistinguishable(
    companies_client: TestClient,
    make_user: Callable[..., dict],
    unique_email: Callable[[], str],
) -> None:
    user = make_user()
    google_only = make_user(password=None, google_id=f"google-{uuid.uuid4().hex}")

    responses = [
        _login(companies_client, user["email"], "wrong password entirely"),
        _login(companies_client, unique_email(), "correct horse battery staple"),
        _login(companies_client, google_only["email"], "correct horse battery staple"),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.json() == EXPECTED_401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "access_token" not in response.text


def test_unknown_email_still_verifies_password(
    companies_client: TestClient,
    unique_email: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    real_verify = login_module.verify_password

    def spy(password: str, password_hash: str | None) -> bool:
        calls.append(password_hash)
        return real_verify(password, password_hash)

    monkeypatch.setattr(login_module, "verify_password", spy)
    response = _login(companies_client, unique_email(), "whatever password")

    assert response.status_code == 401
    assert calls == [None]


def test_oversized_password_is_rejected_by_validation(
    companies_client: TestClient, unique_email: Callable[[], str]
) -> None:
    response = _login(companies_client, unique_email(), "x" * 10_000)
    assert response.status_code == 422
