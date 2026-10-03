"""BE-017: POST /companies (manual)."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import BOARD_SOURCE, needs_db

pytestmark = needs_db

SELECTORS = {
    "careers_url": "https://example.com/careers",
    "job": ".job",
    "title": ".title",
    "link": "a",
    "location": ".location",
}
SCRAPER_SOURCE = {"kind": "scraper", "strategy": "dynamic", "selectors": SELECTORS}


def _payload(**overrides) -> dict:
    return {
        "name": "Stripe",
        "tier": 1,
        "website_url": "https://stripe.com/",
        "source": BOARD_SOURCE,
    } | overrides


def _stored(db: psycopg.Connection, company_id: str) -> tuple:
    row = db.execute(
        "SELECT user_id, name, tier, website_url, active, source"
        " FROM companies WHERE id = %s",
        (company_id,),
    ).fetchone()
    assert row is not None
    return row


def test_requires_token(companies_client: TestClient):
    assert companies_client.post("/companies", json=_payload()).status_code == 401


def test_create_board_company(companies_client, make_user, auth_header, db):
    user = make_user()
    resp = companies_client.post(
        "/companies", headers=auth_header(user["id"]), json=_payload()
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Stripe"
    assert body["tier"] == 1
    assert body["active"] is True
    assert body["source"] == BOARD_SOURCE
    assert "added_at" in body
    assert _stored(db, body["id"]) == (
        user["id"],
        "Stripe",
        1,
        "https://stripe.com/",
        True,
        BOARD_SOURCE,
    )

    listed = companies_client.get("/companies", headers=auth_header(user["id"]))
    assert [c["id"] for c in listed.json()] == [body["id"]]


def test_create_scraper_company_paused(companies_client, make_user, auth_header, db):
    user = make_user()
    resp = companies_client.post(
        "/companies",
        headers=auth_header(user["id"]),
        json=_payload(name="Acme", tier=3, active=False, source=SCRAPER_SOURCE),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["active"] is False
    assert body["source"] == SCRAPER_SOURCE
    _, _, tier, _, active, source = _stored(db, body["id"])
    assert (tier, active, source) == (3, False, SCRAPER_SOURCE)


def test_create_custom_company_paused(companies_client, make_user, auth_header, db):
    # BE-039: no handler is shipped under this name yet; that's allowed.
    source = {"kind": "custom", "handler": "scrape_google"}
    user = make_user()
    resp = companies_client.post(
        "/companies",
        headers=auth_header(user["id"]),
        json=_payload(name="Google", active=False, source=source),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert (body["active"], body["source"]) == (False, source)
    _, _, _, _, active, stored = _stored(db, body["id"])
    assert (active, stored) == (False, source)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param({"kind": "rss", "url": "https://x.com/feed"}, id="unknown-kind"),
        pytest.param({"kind": "custom"}, id="custom-no-handler"),
        pytest.param({"kind": "custom", "handler": " "}, id="custom-blank-handler"),
        pytest.param({"board": "lever", "board_id": "x"}, id="no-kind"),
        pytest.param({"kind": "board", "board": "lever"}, id="missing-board-id"),
        pytest.param(
            {"kind": "board", "board": "workday", "board_id": "x"}, id="bad-board"
        ),
        pytest.param(
            {"kind": "board", "board": "lever", "board_id": " "}, id="blank-board-id"
        ),
        pytest.param(BOARD_SOURCE | {"strategy": "static"}, id="extra-field"),
        pytest.param(BOARD_SOURCE | {"region": "eu"}, id="region-on-greenhouse"),
        pytest.param(
            {"kind": "board", "board": "lever", "board_id": "x", "region": "us"},
            id="unknown-region",
        ),
        pytest.param({"kind": "scraper", "strategy": "static"}, id="no-selectors"),
        pytest.param(SCRAPER_SOURCE | {"strategy": "headless"}, id="bad-strategy"),
        pytest.param(
            SCRAPER_SOURCE | {"selectors": SELECTORS | {"careers_url": "nope"}},
            id="bad-careers-url",
        ),
        pytest.param("board", id="not-an-object"),
        pytest.param(None, id="null"),
    ],
)
def test_malformed_source_is_400(companies_client, make_user, auth_header, db, source):
    user = make_user()
    resp = companies_client.post(
        "/companies", headers=auth_header(user["id"]), json=_payload(source=source)
    )
    assert resp.status_code == 400
    assert any(e["loc"][:2] == ["body", "source"] for e in resp.json()["detail"])
    count = db.execute(
        "SELECT count(*) FROM companies WHERE user_id = %s", (user["id"],)
    ).fetchone()
    assert count == (0,)


def test_missing_source_is_400(companies_client, make_user, auth_header):
    user = make_user()
    payload = _payload()
    del payload["source"]
    resp = companies_client.post(
        "/companies", headers=auth_header(user["id"]), json=payload
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"name": ""}, id="blank-name"),
        pytest.param({"tier": "high"}, id="bad-tier"),
        pytest.param({"website_url": "not a url"}, id="bad-website"),
    ],
)
def test_other_field_errors_stay_422(
    companies_client, make_user, auth_header, overrides
):
    user = make_user()
    resp = companies_client.post(
        "/companies", headers=auth_header(user["id"]), json=_payload(**overrides)
    )
    assert resp.status_code == 422


def test_deleted_user_is_404(companies_client, auth_header):
    resp = companies_client.post(
        "/companies", headers=auth_header(uuid.uuid4()), json=_payload()
    )
    assert resp.status_code == 404
