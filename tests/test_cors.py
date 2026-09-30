"""CORS for the Vercel frontend.

No DB needed: the client is used without ``with``, so lifespan never runs,
and the middleware answers preflight on its own.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.app import create_app
from job_lighthouse_backend.common.settings import cors_allowed_origins_from_env

ALLOWED = "https://job-lighthouse.vercel.app"
SERVICE_MODULES = [
    "job_lighthouse_backend.job_runner.main",
    "job_lighthouse_backend.companies.main",
]


def _preflight(client: TestClient, origin: str, path: str = "/auth/signup"):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )


@pytest.fixture
def allowed_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{ALLOWED}, http://localhost:3000")


@pytest.mark.usefixtures("allowed_origins")
@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_preflight_from_allowed_origin(module: str) -> None:
    # Each service builds its app at import, so reload to pick up the env.
    app = importlib.reload(importlib.import_module(module)).app
    response = _preflight(TestClient(app), ALLOWED)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.usefixtures("allowed_origins")
@pytest.mark.parametrize("module", SERVICE_MODULES)
def test_preflight_from_other_origin_gets_no_allow_origin(module: str) -> None:
    app = importlib.reload(importlib.import_module(module)).app
    response = _preflight(TestClient(app), "https://evil.example.com")
    assert "access-control-allow-origin" not in response.headers


def test_no_origins_allowed_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    response = _preflight(TestClient(create_app("test")), ALLOWED)
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.usefixtures("allowed_origins")
def test_simple_request_carries_allow_origin() -> None:
    client = TestClient(create_app("test"))
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers["access-control-allow-origin"] == ALLOWED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        (" , ", []),
        (f"{ALLOWED}/", [ALLOWED]),
        (f" {ALLOWED} ,http://localhost:3000,", [ALLOWED, "http://localhost:3000"]),
    ],
)
def test_parse_origins(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", raw)
    assert cors_allowed_origins_from_env() == expected
