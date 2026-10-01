"""Shared fixtures.

DB-backed fixtures need a reachable Postgres (``make db-up``, migrated) and
``DATABASE_URL``; tests using them are skipped otherwise. Tests share one
database, so each test uses unique emails and removes the users it creates.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url

from job_lighthouse_backend.common.db import normalize_database_url
from job_lighthouse_backend.companies.auth.passwords import hash_password

TEST_JWT_SECRET = "test-only-jwt-secret-not-for-production-use"  # noqa: S105 -- test fixture

needs_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Startup requires JWT_SECRET. Tests never use a real one.
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    # Never call the real LLM from tests, even if the shell has a key.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Never send real email from tests either.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)


def _sync_dsn() -> str:
    url = make_url(normalize_database_url(os.environ["DATABASE_URL"]))
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class _EmailFactory:
    """Makes unique test emails and remembers them for cleanup."""

    def __init__(self) -> None:
        self.issued: list[str] = []

    def __call__(self) -> str:
        email = f"test-{uuid.uuid4().hex}@example.com"
        self.issued.append(email)
        return email


@pytest.fixture
def unique_email() -> _EmailFactory:
    return _EmailFactory()


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    """Autocommit connection for arranging and asserting DB state."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs Postgres")
    with psycopg.connect(_sync_dsn(), autocommit=True) as conn:
        yield conn


@pytest.fixture
def companies_client(
    db: psycopg.Connection, unique_email: _EmailFactory
) -> Iterator[TestClient]:
    from job_lighthouse_backend.companies.main import app

    with TestClient(app) as client:
        yield client
    # Only this test's emails: other test runs may share the database.
    db.execute(
        "DELETE FROM users WHERE lower(email) = ANY(%s)",
        ([e.lower() for e in unique_email.issued],),
    )


@pytest.fixture
def runner_client(
    db: psycopg.Connection, unique_email: _EmailFactory
) -> Iterator[TestClient]:
    from job_lighthouse_backend.job_runner.main import app

    with TestClient(app) as client:
        yield client
    db.execute(
        "DELETE FROM users WHERE lower(email) = ANY(%s)",
        ([e.lower() for e in unique_email.issued],),
    )


@pytest.fixture
def make_user(
    db: psycopg.Connection, unique_email: _EmailFactory
) -> Iterator[Callable[..., dict]]:
    """Insert a user. Returns ``{"id", "email", "password"}``."""
    created: list[uuid.UUID] = []

    def _make(
        email: str | None = None,
        password: str | None = "correct horse battery staple",  # noqa: S107 -- test fixture
        google_id: str | None = None,
    ) -> dict:
        email = email or unique_email()
        row = db.execute(
            "INSERT INTO users (email, password_hash, google_id)"
            " VALUES (%s, %s, %s) RETURNING id",
            (email, hash_password(password) if password else None, google_id),
        ).fetchone()
        assert row is not None
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


BOARD_SOURCE = {"kind": "board", "board": "greenhouse", "board_id": "stripe"}


@pytest.fixture
def make_company(db: psycopg.Connection) -> Callable[..., uuid.UUID]:
    """Insert a company for ``user_id``. Removed with its user (cascade)."""

    def _make(
        user_id: uuid.UUID,
        name: str = "Stripe",
        tier: int = 1,
        website_url: str = "https://stripe.com/",
        active: bool = True,
        source: dict | None = None,
    ) -> uuid.UUID:
        row = db.execute(
            "INSERT INTO companies (user_id, name, tier, website_url, active, source)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, name, tier, website_url, active, Jsonb(source or BOARD_SOURCE)),
        ).fetchone()
        assert row is not None
        return row[0]

    return _make


@pytest.fixture
def make_job(db: psycopg.Connection) -> Callable[..., uuid.UUID]:
    """Insert a job for ``user_id`` / ``company_id``. Removed with its user."""

    def _make(
        user_id: uuid.UUID,
        company_id: uuid.UUID,
        title: str = "Engineer",
        active: bool = True,
        date: datetime | None = None,
    ) -> uuid.UUID:
        row = db.execute(
            "INSERT INTO jobs"
            " (user_id, company_id, company, title, url, location, description,"
            " active, date)"
            " VALUES (%s, %s, 'Stripe', %s, %s, 'Remote', 'desc', %s,"
            " coalesce(%s::timestamptz, now())) RETURNING id",
            (
                user_id,
                company_id,
                title,
                f"https://example.com/jobs/{uuid.uuid4().hex}",
                active,
                date,
            ),
        ).fetchone()
        assert row is not None
        return row[0]

    return _make
