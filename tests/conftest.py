"""Shared fixtures.

DB-backed fixtures need a reachable Postgres (``make db-up``, migrated) and
``DATABASE_URL``; tests using them are skipped otherwise. Tests share one
database, so each test uses unique emails and removes the users it creates.
"""

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from job_lighthouse_backend.common.db import normalize_database_url
from job_lighthouse_backend.companies.auth.passwords import hash_password

TEST_JWT_SECRET = "test-only-jwt-secret-not-for-production-use"

needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Startup requires JWT_SECRET. Tests never use a real one.
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)


def _sync_dsn() -> str:
    url = make_url(normalize_database_url(os.environ["DATABASE_URL"]))
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture
def unique_email() -> Callable[[], str]:
    return lambda: f"test-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    """Autocommit connection for arranging and asserting DB state."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs Postgres")
    with psycopg.connect(_sync_dsn(), autocommit=True) as conn:
        yield conn


@pytest.fixture
def companies_client(db: psycopg.Connection) -> Iterator[TestClient]:
    from job_lighthouse_backend.companies.main import app

    with TestClient(app) as client:
        yield client
    # Clean up any test users the requests created.
    db.execute("DELETE FROM users WHERE email LIKE 'test-%@example.com'")


@pytest.fixture
def make_user(
    db: psycopg.Connection, unique_email: Callable[[], str]
) -> Iterator[Callable[..., dict]]:
    """Insert a user. Returns ``{"id", "email", "password"}``."""
    created: list[uuid.UUID] = []

    def _make(
        email: str | None = None,
        password: str | None = "correct horse battery staple",
        google_id: str | None = None,
    ) -> dict:
        email = email or unique_email()
        row = db.execute(
            "INSERT INTO users (email, password_hash, google_id)"
            " VALUES (%s, %s, %s) RETURNING id",
            (email, hash_password(password) if password else None, google_id),
        ).fetchone()
        created.append(row[0])
        return {"id": row[0], "email": email, "password": password}

    yield _make
    for user_id in created:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))


@pytest.fixture
def auth_header() -> Callable[[uuid.UUID], dict[str, str]]:
    """``Authorization`` header with a valid token for ``user_id``."""
    from job_lighthouse_backend.common.auth import issue_token
    from job_lighthouse_backend.common.settings import Settings

    settings = Settings(database_url="unused", jwt_secret=TEST_JWT_SECRET)
    return lambda user_id: {"Authorization": f"Bearer {issue_token(user_id, settings)}"}
