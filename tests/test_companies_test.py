"""BE-036: POST /companies/{id}/test."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.companies.companies import get_fetcher
from job_lighthouse_backend.companies.main import app
from job_lighthouse_backend.job_runner.openings import FetchError, Opening

from .conftest import BOARD_SOURCE, needs_db

pytestmark = needs_db

SCRAPER_SOURCE = {
    "kind": "scraper",
    "strategy": "static",
    "selectors": {
        "careers_url": "https://example.com/careers",
        "job": ".job",
        "title": ".title",
        "link": "a",
    },
}


class FakeFetch:
    """Returns ``result`` (or raises it) and records each source it got."""

    def __init__(self, result: list[Opening] | Exception) -> None:
        self.result = result
        self.sources: list = []

    def __call__(self, source):
        self.sources.append(source)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def use_fetch() -> Iterator:
    def _use(fetch: FakeFetch) -> FakeFetch:
        app.dependency_overrides[get_fetcher] = lambda: fetch
        return fetch

    yield _use
    app.dependency_overrides.pop(get_fetcher, None)


def _counts(db: psycopg.Connection, company_id: uuid.UUID) -> tuple[int, int]:
    row = db.execute(
        "SELECT (SELECT count(*) FROM jobs WHERE company_id = %s),"
        " (SELECT count(*) FROM run_company_results WHERE company_id = %s)",
        (company_id, company_id),
    ).fetchone()
    assert row is not None
    return row


def _test(client: TestClient, company_id, headers) -> dict:
    resp = client.post(f"/companies/{company_id}/test", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_requires_token(companies_client: TestClient):
    resp = companies_client.post(f"/companies/{uuid.uuid4()}/test")
    assert resp.status_code == 401


def test_reachable_reports_job_count(
    companies_client, make_user, make_company, auth_header, use_fetch, db
):
    user = make_user()
    company_id = make_company(user["id"])
    fetch = use_fetch(
        FakeFetch(
            [
                Opening("Eng", "https://jobs.example/1"),
                Opening("Eng (dup)", "https://jobs.example/1"),
                Opening("Lead", "https://jobs.example/2"),
            ]
        )
    )

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body == {"status": "ok", "jobs_found": 2, "error": None}
    assert fetch.sources[0].model_dump() == BOARD_SOURCE
    # Read-only: nothing stored.
    assert _counts(db, company_id) == (0, 0)


def test_reachable_zero_jobs_is_ok(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    user = make_user()
    company_id = make_company(user["id"], source=SCRAPER_SOURCE)
    use_fetch(FakeFetch([]))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body == {"status": "ok", "jobs_found": 0, "error": None}


def test_fetch_failure_is_distinct_from_zero_jobs(
    companies_client, make_user, make_company, auth_header, use_fetch, db
):
    user = make_user()
    company_id = make_company(user["id"])
    use_fetch(FakeFetch(FetchError("HTTP 404")))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body == {"status": "failed", "jobs_found": 0, "error": "HTTP 404"}
    assert _counts(db, company_id) == (0, 0)


def test_unexpected_error_hides_message(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    user = make_user()
    company_id = make_company(user["id"])
    use_fetch(FakeFetch(RuntimeError("secret detail")))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body == {
        "status": "failed",
        "jobs_found": 0,
        "error": "unexpected error: RuntimeError",
    }


def test_custom_source_is_skipped(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    user = make_user()
    company_id = make_company(user["id"], source={"kind": "custom", "handler": "x"})
    fetch = use_fetch(FakeFetch([]))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body["status"] == "skipped"
    assert body["jobs_found"] == 0
    assert fetch.sources == []


def test_invalid_stored_source_fails(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    user = make_user()
    company_id = make_company(user["id"], source={"kind": "board", "board": "nope"})
    use_fetch(FakeFetch([]))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body == {
        "status": "failed",
        "jobs_found": 0,
        "error": "stored source is invalid",
    }


def test_paused_company_can_be_tested(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    user = make_user()
    company_id = make_company(user["id"], active=False)
    use_fetch(FakeFetch([Opening("Eng", "https://jobs.example/1")]))

    body = _test(companies_client, company_id, auth_header(user["id"]))
    assert body["status"] == "ok"


def test_other_users_company_is_404(
    companies_client, make_user, make_company, auth_header, use_fetch
):
    owner, other = make_user(), make_user()
    company_id = make_company(owner["id"])
    fetch = use_fetch(FakeFetch([]))

    resp = companies_client.post(
        f"/companies/{company_id}/test", headers=auth_header(other["id"])
    )
    assert resp.status_code == 404
    assert fetch.sources == []


def test_missing_company_is_404(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.post(
        f"/companies/{uuid.uuid4()}/test", headers=auth_header(user["id"])
    )
    assert resp.status_code == 404
