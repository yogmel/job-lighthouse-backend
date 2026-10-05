"""BE-016: GET /companies."""

import uuid

from fastapi.testclient import TestClient

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
CUSTOM_SOURCE = {"kind": "custom", "handler": "scrape_google"}


def test_requires_token(companies_client: TestClient):
    assert companies_client.get("/companies").status_code == 401


def test_empty_list(companies_client, make_user, auth_header):
    user = make_user()
    resp = companies_client.get("/companies", headers=auth_header(user["id"]))
    assert resp.status_code == 200
    assert resp.json() == []


def test_returns_only_own_companies(
    companies_client, make_user, make_company, auth_header
):
    me, other = make_user(), make_user()
    mine = make_company(me["id"], name="Mine")
    make_company(other["id"], name="Theirs")

    resp = companies_client.get("/companies", headers=auth_header(me["id"]))
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [str(mine)]


def test_returns_every_source_kind_and_paused(
    companies_client, make_user, make_company, auth_header
):
    user = make_user()
    ids = [
        make_company(user["id"], name="Board", source=BOARD_SOURCE),
        make_company(user["id"], name="Scraper", tier=2, source=SCRAPER_SOURCE),
        make_company(user["id"], name="Custom", active=False, source=CUSTOM_SOURCE),
    ]

    body = companies_client.get("/companies", headers=auth_header(user["id"])).json()
    assert [c["id"] for c in body] == [str(i) for i in ids]
    by_name = {c["name"]: c for c in body}
    assert by_name["Board"]["source"] == BOARD_SOURCE
    assert by_name["Scraper"]["source"]["selectors"]["location"] is None
    assert by_name["Scraper"]["tier"] == 2
    assert by_name["Custom"]["source"] == CUSTOM_SOURCE
    assert by_name["Custom"]["active"] is False
    assert set(by_name["Board"]) == {
        "id",
        "name",
        "tier",
        "added_at",
        "website_url",
        "active",
        "source",
    }


def test_unknown_user_is_401(companies_client, auth_header):
    # BE-044: a token whose user is gone (e.g. deleted) no longer works.
    resp = companies_client.get("/companies", headers=auth_header(uuid.uuid4()))
    assert resp.status_code == 401
