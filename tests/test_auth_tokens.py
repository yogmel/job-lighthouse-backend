"""BE-014: JWT issuing and local validation in both services."""

import importlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.auth import (
    CurrentUserId,
    InvalidTokenError,
    decode_token,
    issue_token,
)
from job_lighthouse_backend.common.settings import Settings

from .conftest import TEST_JWT_SECRET, needs_db

SETTINGS = Settings(database_url="unused", jwt_secret=TEST_JWT_SECRET)
SERVICES = [
    "job_lighthouse_backend.job_runner.main",
    "job_lighthouse_backend.companies.main",
]


def test_issue_then_decode_round_trips() -> None:
    user_id = uuid.uuid4()
    assert decode_token(issue_token(user_id, SETTINGS), TEST_JWT_SECRET) == user_id


def test_decode_rejects_wrong_secret() -> None:
    token = issue_token(uuid.uuid4(), SETTINGS)
    with pytest.raises(InvalidTokenError):
        decode_token(token, "another-secret-of-sufficient-length-xx")


def test_decode_rejects_expired() -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": past, "exp": past + timedelta(hours=1)},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(token, TEST_JWT_SECRET)


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "not-a-uuid"},
        {},  # no sub
    ],
)
def test_decode_rejects_bad_sub(claims: dict) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {**claims, "iat": now, "exp": now + timedelta(hours=1)},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(token, TEST_JWT_SECRET)


def test_decode_rejects_alg_none() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(hours=1)},
        None,
        algorithm="none",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(token, TEST_JWT_SECRET)


@pytest.fixture(params=SERVICES)
def protected_client(request: pytest.FixtureRequest) -> TestClient:
    """A service app with a test-only protected route added."""
    app = importlib.import_module(request.param).app
    path = "/_test/whoami"
    if not any(getattr(r, "path", None) == path for r in app.routes):

        @app.get(path)
        async def whoami(user_id: CurrentUserId) -> dict[str, str]:
            return {"user_id": str(user_id)}

    with TestClient(app) as client:
        yield client


@needs_db
def test_valid_token_injects_user_id(protected_client: TestClient, auth_header) -> None:
    user_id = uuid.uuid4()
    response = protected_client.get("/_test/whoami", headers=auth_header(user_id))
    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id)}


@needs_db
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
)
def test_missing_or_invalid_token_returns_401(
    protected_client: TestClient, headers: dict
) -> None:
    response = protected_client.get("/_test/whoami", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@needs_db
def test_expired_token_returns_401(protected_client: TestClient) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": past, "exp": past + timedelta(hours=1)},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = protected_client.get(
        "/_test/whoami", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
