"""BE-027: GET/PUT /config."""

import uuid

from fastapi.testclient import TestClient

from .conftest import needs_db

pytestmark = needs_db

BODY = {
    "keywords_include": ["backend"],
    "keywords_exclude": ["intern"],
    "location": "Berlin",
    "cron": "0 8 * * 1-5",
    "profile": "# Profile\nBackend engineer.",
    "notify_email": True,
    "notify_empty_company": True,
    "notify_min_score": 40,
}


def _put(client: TestClient, headers: dict[str, str], **changes: object) -> dict:
    resp = client.put("/config", headers=headers, json={**BODY, **changes})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_requires_token(runner_client: TestClient):
    assert runner_client.get("/config").status_code == 401
    assert runner_client.put("/config", json=BODY).status_code == 401


def test_get_creates_defaults_once(runner_client, make_user, auth_header, db):
    user = make_user()
    headers = auth_header(user["id"])

    first = runner_client.get("/config", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body == {
        "id": body["id"],
        "user_id": str(user["id"]),
        "keywords_include": [],
        "keywords_exclude": [],
        "location": "",
        "cron": "0 7 * * *",
        "profile": "",
        "profile_version": 1,
        "notify_email": True,
        "notify_empty_company": True,
        "notify_min_score": 40,
    }
    assert runner_client.get("/config", headers=headers).json() == body
    count = db.execute(
        "SELECT count(*) FROM config WHERE user_id = %s", (user["id"],)
    ).fetchone()
    assert count == (1,)


def test_put_without_row_creates_it(runner_client, make_user, auth_header):
    user = make_user()
    headers = auth_header(user["id"])

    body = _put(runner_client, headers)
    assert {k: body[k] for k in BODY} == BODY
    assert body["profile_version"] == 1
    assert runner_client.get("/config", headers=headers).json() == body


def test_put_replaces_fields(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    _put(runner_client, headers)

    body = _put(
        runner_client,
        headers,
        keywords_include=[],
        keywords_exclude=["senior", "staff"],
        location="Remote",
        cron="*/30 * * * *",
    )
    assert body["keywords_include"] == []
    assert body["keywords_exclude"] == ["senior", "staff"]
    assert body["location"] == "Remote"
    assert body["cron"] == "*/30 * * * *"


def test_profile_change_bumps_version(runner_client, make_user, auth_header, db):
    user = make_user()
    headers = auth_header(user["id"])
    assert runner_client.get("/config", headers=headers).json()["profile_version"] == 1

    assert _put(runner_client, headers)["profile_version"] == 2
    assert _put(runner_client, headers, profile="v3")["profile_version"] == 3
    # Back to an earlier text is still a change.
    assert _put(runner_client, headers)["profile_version"] == 4
    # Only the latest text is stored; there is no history table.
    row = db.execute(
        "SELECT profile FROM config WHERE user_id = %s", (user["id"],)
    ).fetchone()
    assert row == (BODY["profile"],)


def test_same_profile_or_other_fields_keep_version(
    runner_client, make_user, auth_header
):
    headers = auth_header(make_user()["id"])
    assert _put(runner_client, headers)["profile_version"] == 1

    assert _put(runner_client, headers)["profile_version"] == 1
    assert _put(runner_client, headers, cron="0 9 * * *")["profile_version"] == 1


def test_client_cannot_set_version(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    body = _put(runner_client, headers, profile_version=99)
    assert body["profile_version"] == 1


def test_users_are_isolated(runner_client, make_user, auth_header):
    me, other = make_user(), make_user()
    _put(runner_client, auth_header(other["id"]), profile="theirs")

    mine = runner_client.get("/config", headers=auth_header(me["id"])).json()
    assert mine["user_id"] == str(me["id"])
    assert mine["profile"] == ""
    assert _put(runner_client, auth_header(me["id"]))["profile_version"] == 2
    theirs = runner_client.get("/config", headers=auth_header(other["id"])).json()
    assert theirs["profile"] == "theirs"
    assert theirs["profile_version"] == 1


def test_put_validation(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    missing = {k: v for k, v in BODY.items() if k != "location"}
    for body in (
        missing,
        {**BODY, "cron": "  "},
        {**BODY, "keywords_exclude": [""]},
        {**BODY, "keywords_include": "backend"},
        {**BODY, "notify_min_score": -1},
        {**BODY, "notify_min_score": 101},
        {**BODY, "notify_email": "maybe"},
        {k: v for k, v in BODY.items() if k != "notify_email"},
    ):
        resp = runner_client.put("/config", headers=headers, json=body)
        assert resp.status_code == 422, body


def test_deleted_account_is_401(runner_client, auth_header):
    headers = auth_header(uuid.uuid4())
    assert runner_client.get("/config", headers=headers).status_code == 401
    assert runner_client.put("/config", headers=headers, json=BODY).status_code == 401


def test_put_stores_notification_prefs(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    prefs = {
        "notify_email": False,
        "notify_empty_company": False,
        "notify_min_score": 0,
    }
    assert _put(runner_client, headers, **prefs).items() >= prefs.items()
    assert runner_client.get("/config", headers=headers).json().items() >= prefs.items()
    assert _put(runner_client, headers, notify_min_score=100)["notify_min_score"] == 100
