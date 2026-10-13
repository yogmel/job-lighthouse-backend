"""Startup and health checks for the shared app factory.

The happy-path test needs a reachable Postgres (``make db-up``) and
``DATABASE_URL``; it is skipped otherwise.
"""

import importlib
import io
import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from job_lighthouse_backend.common.app import (
    PACKAGE_LOGGER,
    configure_logging,
    create_app,
)
from job_lighthouse_backend.common.db import normalize_database_url
from job_lighthouse_backend.common.settings import SettingsError


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")
def test_health_returns_200() -> None:
    with TestClient(create_app("test")) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_fails_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SettingsError), TestClient(create_app("test")):
        pass


def test_startup_fails_when_postgres_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Port 1 on localhost: nothing listens there, so the connect is refused.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    with pytest.raises(OperationalError), TestClient(create_app("test")):
        pass


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@h/db", "postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"],
)
def test_normalize_database_url_uses_psycopg(url: str) -> None:
    assert normalize_database_url(url) == "postgresql+psycopg://u:p@h/db"


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")
@pytest.mark.parametrize(
    "module",
    [
        "job_lighthouse_backend.job_runner.main",
        "job_lighthouse_backend.companies.main",
    ],
)
def test_service_apps_serve_health(module: str) -> None:
    app = importlib.import_module(module).app
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_configure_logging_emits_info_once() -> None:
    package = logging.getLogger(PACKAGE_LOGGER)
    configure_logging()
    configure_logging()
    handlers = [h for h in package.handlers if getattr(h, "_app_handler", False)]
    assert len(handlers) == 1

    stream = io.StringIO()
    handlers[0].setStream(stream)  # type: ignore[attr-defined]
    logging.getLogger(f"{PACKAGE_LOGGER}.companies.detect").info("timing %s", "ok")
    line = "INFO job_lighthouse_backend.companies.detect: timing ok"
    assert line in stream.getvalue()
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
