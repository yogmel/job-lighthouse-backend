"""BE-035: POST /companies/detect."""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from job_lighthouse_backend.common.settings import Settings
from job_lighthouse_backend.companies import detect
from job_lighthouse_backend.companies.detect import (
    SAMPLE_SIZE,
    get_fetcher,
    get_page_loader,
    get_proposer,
    get_scorer,
)
from job_lighthouse_backend.companies.main import app
from job_lighthouse_backend.companies.selector_discovery import Proposal
from job_lighthouse_backend.job_runner.openings import FetchError, Opening
from job_lighthouse_backend.job_runner.scoring import Match, Posting

from .conftest import TEST_JWT_SECRET, needs_db

PROFILE = "# Profile\nBackend engineer."
PAGE = """
<html><body><ul>
  <li class="job"><h3 class="t">Engineer</h3><a href="/jobs/1">x</a>
    <span class="loc">Berlin</span></li>
  <li class="job"><h3 class="t">Designer</h3><a href="/jobs/2">x</a></li>
</ul></body></html>
"""
GOOD = Proposal(job="li.job", title=".t", link="a", location=".loc")


class Fakes:
    """Network stand-ins. Calls are recorded; unused ones fail loudly."""

    def __init__(self) -> None:
        self.board_result: list[Opening] | Exception = []
        self.page: tuple[str, str] | Exception = ("https://acme.example", PAGE)
        self.proposal: Proposal | None = GOOD
        self.fetched: list = []
        self.loaded: list[tuple[str, str]] = []
        self.proposed: list[str] = []
        self.scored: list[tuple[str, Posting]] = []
        # Seconds the LLM stand-ins take.
        self.propose_delay = 0.0
        self.score_delay = 0.0

    def fetch(self, source):
        self.fetched.append(source)
        if isinstance(self.board_result, Exception):
            raise self.board_result
        return self.board_result

    def load(self, url: str, strategy: str) -> tuple[str, str]:
        self.loaded.append((url, strategy))
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    async def propose(self, url: str, html: str) -> Proposal | None:
        self.proposed.append(url)
        await asyncio.sleep(self.propose_delay)
        return self.proposal

    async def score(self, profile: str, posting: Posting) -> Match:
        self.scored.append((profile, posting))
        await asyncio.sleep(self.score_delay)
        if "fail" in posting.title:
            raise RuntimeError("LLM down")
        return Match(score=len(posting.title), description=f"why {posting.title}")


@pytest.fixture
def fakes() -> Iterator[Fakes]:
    f = Fakes()
    app.dependency_overrides[get_fetcher] = lambda: f.fetch
    app.dependency_overrides[get_page_loader] = lambda: f.load
    app.dependency_overrides[get_proposer] = lambda: f.propose
    app.dependency_overrides[get_scorer] = lambda: f.score
    yield f
    app.dependency_overrides.clear()


def _set_profile(db, user_id, profile: str = PROFILE) -> None:
    db.execute(
        "INSERT INTO config (user_id, location, cron, profile, profile_version)"
        " VALUES (%s, '', '0 7 * * *', %s, 3)",
        (user_id, profile),
    )


def _detect(client, headers, url: str, expected: int = 200) -> dict:
    resp = client.post("/companies/detect", headers=headers, json={"url": url})
    assert resp.status_code == expected, resp.text
    return resp.json()


def _confirm(client, headers, source: dict) -> dict:
    resp = client.post(
        "/companies",
        headers=headers,
        json={
            "name": "Acme",
            "tier": 1,
            "website_url": "https://acme.example/",
            "source": source,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _stored_source(db, company_id: str) -> dict:
    row = db.execute(
        "SELECT source FROM companies WHERE id = %s", (company_id,)
    ).fetchone()
    assert row is not None
    return row[0]


@needs_db
def test_requires_token(companies_client):
    resp = companies_client.post("/companies/detect", json={"url": "x"})
    assert resp.status_code == 401


@needs_db
@pytest.mark.parametrize("url", ["", "   "])
def test_blank_url_is_rejected(companies_client, make_user, auth_header, url):
    user = make_user()
    _detect(companies_client, auth_header(user["id"]), url, expected=422)


@needs_db
def test_known_board_is_verified_without_llm(
    companies_client, make_user, auth_header, fakes, db
):
    user = make_user()
    _set_profile(db, user["id"])
    fakes.board_result = [
        Opening("Engineer", "https://jobs.example/1", "Berlin", "Python"),
        Opening("Engineer", "https://jobs.example/1"),
    ]

    body = _detect(companies_client, auth_header(user["id"]), "jobs.lever.co/acme/123")
    assert body == {
        "status": "detected",
        "method": "board",
        "source": {"kind": "board", "board": "lever", "board_id": "acme"},
        "jobs_found": 1,
        "sample": [
            {
                "title": "Engineer",
                "url": "https://jobs.example/1",
                "location": "Berlin",
                "match_score": 8,
                "match_description": "why Engineer",
            }
        ],
        "reason": None,
    }
    assert [s.board_id for s in fakes.fetched] == ["acme"]
    # No selector-discovery LLM call, no page load.
    assert fakes.proposed == []
    assert fakes.loaded == []
    # Scored by the run's scorer, with the user's profile.
    assert fakes.scored == [(PROFILE, Posting("Engineer", "acme", "Berlin", "Python"))]


@needs_db
def test_board_with_zero_openings_is_detected(
    companies_client, make_user, auth_header, fakes
):
    user = make_user()
    body = _detect(
        companies_client, auth_header(user["id"]), "https://jobs.ashbyhq.com/acme"
    )
    assert body["status"] == "detected"
    assert body["jobs_found"] == 0
    assert body["sample"] == []


@needs_db
def test_board_fetch_failure_is_422(companies_client, make_user, auth_header, fakes):
    user = make_user()
    fakes.board_result = FetchError("HTTP 404")
    body = _detect(
        companies_client,
        auth_header(user["id"]),
        "https://boards.greenhouse.io/nope",
        expected=422,
    )
    assert body["detail"] == "greenhouse board failed: HTTP 404"


@needs_db
def test_board_unexpected_error_is_422(companies_client, make_user, auth_header, fakes):
    user = make_user()
    fakes.board_result = RuntimeError("secret detail")
    body = _detect(
        companies_client,
        auth_header(user["id"]),
        "https://jobs.lever.co/acme",
        expected=422,
    )
    assert body["detail"] == "lever board failed: unexpected error: RuntimeError"


@needs_db
def test_unknown_url_uses_selector_discovery(
    companies_client, make_user, auth_header, fakes, db
):
    user = make_user()
    _set_profile(db, user["id"])

    body = _detect(companies_client, auth_header(user["id"]), "acme.example/jobs")
    assert body["status"] == "detected"
    assert body["method"] == "selectors"
    assert body["source"] == {
        "kind": "scraper",
        "strategy": "static",
        "selectors": {
            "careers_url": "https://acme.example/",
            "job": "li.job",
            "title": ".t",
            "link": "a",
            "location": ".loc",
        },
    }
    assert body["jobs_found"] == 2
    assert [j["match_score"] for j in body["sample"]] == [8, 8]
    assert fakes.loaded == [("https://acme.example/jobs", "static")]
    assert fakes.fetched == []
    assert {s[1].company for s in fakes.scored} == {"acme.example"}


@pytest.mark.parametrize(
    "source_url",
    ["https://jobs.lever.co/acme", "https://acme.example/careers"],
)
@needs_db
def test_confirm_persists_exactly_the_returned_source(
    companies_client, make_user, auth_header, fakes, db, source_url
):
    user = make_user()
    headers = auth_header(user["id"])
    body = _detect(companies_client, headers, source_url)

    created = _confirm(companies_client, headers, body["source"])
    assert created["source"] == body["source"]
    assert _stored_source(db, created["id"]) == body["source"]
    # Confirming doesn't detect again.
    assert len(fakes.fetched) + len(fakes.proposed) == 1


@needs_db
def test_no_job_list_needs_custom(companies_client, make_user, auth_header, fakes):
    user = make_user()
    fakes.proposal = None

    body = _detect(companies_client, auth_header(user["id"]), "https://acme.example")
    assert body == {
        "status": "needs_custom",
        "method": None,
        "source": None,
        "jobs_found": 0,
        "sample": [],
        "reason": "no job list could be found on the page",
    }
    # Static, then rendered.
    assert [s for _, s in fakes.loaded] == ["static", "dynamic"]


@needs_db
def test_without_llm_key_unknown_url_needs_custom(
    companies_client, make_user, auth_header, fakes
):
    user = make_user()
    app.dependency_overrides[get_proposer] = lambda: None

    body = _detect(companies_client, auth_header(user["id"]), "https://acme.example")
    assert body["status"] == "needs_custom"
    assert body["reason"] == "selector discovery is not configured"
    # Only the embed check's static load (BE-050).
    assert fakes.loaded == [("https://acme.example", "static")]


EMBED = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
EMBED_PAGE = f"<html><body><h1>Careers</h1>{EMBED}</script></body></html>"


@needs_db
def test_embedded_board_is_detected_without_llm(
    companies_client, make_user, auth_header, fakes, db
):
    # BE-050: works with no LLM key, and never calls one.
    user = make_user()
    _set_profile(db, user["id"])
    app.dependency_overrides[get_proposer] = lambda: None
    fakes.page = ("https://acme.example/careers", EMBED_PAGE)
    fakes.board_result = [Opening("Engineer", "https://jobs.example/1")]

    body = _detect(companies_client, auth_header(user["id"]), "acme.example/careers")
    assert body["status"] == "detected"
    assert body["method"] == "board"
    assert body["source"] == {
        "kind": "board",
        "board": "greenhouse",
        "board_id": "acme",
    }
    assert body["jobs_found"] == 1
    assert fakes.loaded == [("https://acme.example/careers", "static")]
    assert [s.board_id for s in fakes.fetched] == ["acme"]
    assert fakes.proposed == []
    # Scored under the board slug, like a pasted board URL.
    assert [p.company for _, p in fakes.scored] == ["acme"]


@needs_db
def test_failing_embedded_board_falls_through_to_discovery(
    companies_client, make_user, auth_header, fakes
):
    # A stale widget on a working page is not a bad URL.
    user = make_user()
    fakes.page = (
        "https://acme.example/",
        PAGE.replace("<ul>", f"{EMBED}</script><ul>"),
    )
    fakes.board_result = FetchError("HTTP 404")

    body = _detect(companies_client, auth_header(user["id"]), "https://acme.example")
    assert body["status"] == "detected"
    assert body["method"] == "selectors"
    assert body["jobs_found"] == 2
    assert len(fakes.fetched) == 1
    # The static page is fetched once and shared with discovery.
    assert fakes.loaded == [("https://acme.example", "static")]


@needs_db
def test_unreachable_page_is_422(companies_client, make_user, auth_header, fakes):
    user = make_user()
    fakes.page = FetchError("HTTP 404")
    body = _detect(
        companies_client, auth_header(user["id"]), "https://acme.example", 422
    )
    assert body["detail"] == "page could not be loaded: HTTP 404"
    assert fakes.proposed == []


@needs_db
def test_no_profile_leaves_sample_unscored(
    companies_client, make_user, auth_header, fakes
):
    user = make_user()
    fakes.board_result = [Opening("Engineer", "https://jobs.example/1")]

    body = _detect(companies_client, auth_header(user["id"]), "jobs.lever.co/acme")
    assert body["sample"][0]["match_score"] is None
    assert body["sample"][0]["match_description"] is None
    assert fakes.scored == []


@needs_db
def test_no_scorer_leaves_sample_unscored(
    companies_client, make_user, auth_header, fakes, db
):
    user = make_user()
    _set_profile(db, user["id"])
    app.dependency_overrides[get_scorer] = lambda: None
    fakes.board_result = [Opening("Engineer", "https://jobs.example/1")]

    body = _detect(companies_client, auth_header(user["id"]), "jobs.lever.co/acme")
    assert body["sample"][0]["match_score"] is None


@needs_db
def test_sample_is_capped_and_failures_stay_unscored(
    companies_client, make_user, auth_header, fakes, db
):
    user = make_user()
    _set_profile(db, user["id"])
    fakes.board_result = [
        Opening("fail" if i == 0 else f"Job {i}", f"https://jobs.example/{i}")
        for i in range(SAMPLE_SIZE + 3)
    ]

    body = _detect(companies_client, auth_header(user["id"]), "jobs.lever.co/acme")
    assert body["jobs_found"] == SAMPLE_SIZE + 3
    assert len(body["sample"]) == SAMPLE_SIZE
    assert len(fakes.scored) == SAMPLE_SIZE
    assert body["sample"][0]["match_score"] is None
    assert body["sample"][1]["match_score"] == len("Job 1")


@needs_db
def test_only_own_profile_is_used(companies_client, make_user, auth_header, fakes, db):
    me, other = make_user(), make_user()
    _set_profile(db, other["id"], "theirs")
    fakes.board_result = [Opening("Engineer", "https://jobs.example/1")]

    _detect(companies_client, auth_header(me["id"]), "jobs.lever.co/acme")
    assert fakes.scored == []


@needs_db
def test_slow_discovery_needs_custom_within_budget(
    companies_client, make_user, auth_header, fakes, monkeypatch
):
    # BE-048: answer before the proxy times out instead of a 504.
    monkeypatch.setattr(detect, "DETECT_BUDGET_SECONDS", 0.2)
    user = make_user()
    fakes.propose_delay = 5

    body = _detect(companies_client, auth_header(user["id"]), "https://acme.example")
    assert body["status"] == "needs_custom"
    assert body["reason"] == "detection took too long"
    assert fakes.proposed == ["https://acme.example"]


@needs_db
def test_slow_scoring_returns_sample_unscored(
    companies_client, make_user, auth_header, fakes, db, monkeypatch
):
    monkeypatch.setattr(detect, "DETECT_BUDGET_SECONDS", 0.2)
    user = make_user()
    _set_profile(db, user["id"])
    fakes.score_delay = 5
    fakes.board_result = [Opening("Engineer", "https://jobs.example/1")]

    body = _detect(companies_client, auth_header(user["id"]), "jobs.lever.co/acme")
    assert body["status"] == "detected"
    assert body["jobs_found"] == 1
    assert body["sample"][0]["title"] == "Engineer"
    assert body["sample"][0]["match_score"] is None
    assert len(fakes.scored) == 1


def _request(**settings) -> SimpleNamespace:
    state = SimpleNamespace(
        settings=Settings(database_url="unused", jwt_secret=TEST_JWT_SECRET, **settings)
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_get_proposer_needs_key():
    assert get_proposer(_request()) is None  # type: ignore[arg-type]


def test_get_proposer_is_built_once(monkeypatch):
    built: list[tuple[str, str]] = []

    def create(key: str, model: str):
        built.append((key, model))
        return object()

    monkeypatch.setattr(detect, "create_openai_proposer", create)
    request = _request(openai_api_key="k", openai_model="m")
    first = get_proposer(request)  # type: ignore[arg-type]
    assert get_proposer(request) is first  # type: ignore[arg-type]
    assert built == [("k", "m")]
