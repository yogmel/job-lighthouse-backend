"""BE-026: GET /jobs."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from .conftest import needs_db

pytestmark = needs_db

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _ids(client: TestClient, headers: dict[str, str], **params: str | int) -> list[str]:
    resp = client.get("/jobs", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [j["id"] for j in resp.json()]


def test_requires_token(runner_client: TestClient):
    assert runner_client.get("/jobs").status_code == 401


def test_empty_list(runner_client, make_user, auth_header):
    user = make_user()
    assert _ids(runner_client, auth_header(user["id"])) == []


def test_returns_only_own_jobs_newest_first(
    runner_client, make_user, make_company, make_job, auth_header
):
    me, other = make_user(), make_user()
    company = make_company(me["id"])
    older = make_job(me["id"], company, date=T0)
    newer = make_job(me["id"], company, date=T0 + timedelta(days=1))
    closed = make_job(me["id"], company, active=False, date=T0 - timedelta(days=1))
    make_job(other["id"], make_company(other["id"]))

    assert _ids(runner_client, auth_header(me["id"])) == [
        str(newer),
        str(older),
        str(closed),
    ]


def test_response_shape(runner_client, make_user, make_company, make_job, auth_header):
    user = make_user()
    company = make_company(user["id"])
    job = make_job(user["id"], company, title="Backend Engineer", date=T0)

    body = runner_client.get("/jobs", headers=auth_header(user["id"])).json()
    assert body == [
        {
            "id": str(job),
            "title": "Backend Engineer",
            "url": body[0]["url"],
            "location": "Remote",
            "description": "desc",
            "company_id": str(company),
            "company": "Stripe",
            "match_score": None,
            "match_description": None,
            "profile_version": None,
            "date": body[0]["date"],
            "notified_at": None,
            "active": True,
        }
    ]
    assert datetime.fromisoformat(body[0]["date"]) == T0
    assert "user_id" not in body[0]


def test_filter_active(runner_client, make_user, make_company, make_job, auth_header):
    user = make_user()
    company = make_company(user["id"])
    open_job = make_job(user["id"], company, active=True)
    closed = make_job(user["id"], company, active=False)
    headers = auth_header(user["id"])

    assert _ids(runner_client, headers, active="true") == [str(open_job)]
    assert _ids(runner_client, headers, active="false") == [str(closed)]


def test_filter_company(runner_client, make_user, make_company, make_job, auth_header):
    user = make_user()
    a = make_company(user["id"], name="A")
    b = make_company(user["id"], name="B")
    job_a = make_job(user["id"], a)
    make_job(user["id"], b)

    assert _ids(runner_client, auth_header(user["id"]), company_id=str(a)) == [
        str(job_a)
    ]


def test_filter_tier(runner_client, make_user, make_company, make_job, auth_header):
    user = make_user()
    tier1 = make_company(user["id"], tier=1)
    tier2 = make_company(user["id"], tier=2)
    make_job(user["id"], tier1)
    job2 = make_job(user["id"], tier2)
    headers = auth_header(user["id"])

    assert _ids(runner_client, headers, tier=2) == [str(job2)]
    assert _ids(runner_client, headers, tier=3) == []


def test_tier_uses_current_company_tier(
    runner_client, make_user, make_company, make_job, auth_header, db
):
    user = make_user()
    company = make_company(user["id"], tier=1)
    job = make_job(user["id"], company)
    db.execute("UPDATE companies SET tier = 2 WHERE id = %s", (company,))
    headers = auth_header(user["id"])

    assert _ids(runner_client, headers, tier=1) == []
    assert _ids(runner_client, headers, tier=2) == [str(job)]


def test_filter_active_and_tier(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    tier1 = make_company(user["id"], tier=1)
    tier2 = make_company(user["id"], tier=2)
    wanted = make_job(user["id"], tier1, active=True)
    make_job(user["id"], tier1, active=False)
    make_job(user["id"], tier2, active=True)

    assert _ids(runner_client, auth_header(user["id"]), active="true", tier=1) == [
        str(wanted)
    ]


def test_filter_company_and_active(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    a = make_company(user["id"], name="A")
    b = make_company(user["id"], name="B")
    make_job(user["id"], a, active=True)
    wanted = make_job(user["id"], a, active=False)
    make_job(user["id"], b, active=False)

    assert _ids(
        runner_client, auth_header(user["id"]), company_id=str(a), active="false"
    ) == [str(wanted)]


def test_all_filters_combined(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    a = make_company(user["id"], tier=1)
    wanted = make_job(user["id"], a, active=True)
    make_job(user["id"], a, active=False)
    headers = auth_header(user["id"])

    assert _ids(runner_client, headers, company_id=str(a), active="true", tier=1) == [
        str(wanted)
    ]
    assert _ids(runner_client, headers, company_id=str(a), tier=2) == []


def test_other_users_company_returns_empty(
    runner_client, make_user, make_company, make_job, auth_header
):
    me, other = make_user(), make_user()
    make_job(me["id"], make_company(me["id"]))
    theirs = make_company(other["id"])
    make_job(other["id"], theirs)
    headers = auth_header(me["id"])

    assert _ids(runner_client, headers, company_id=str(theirs)) == []
    assert _ids(runner_client, headers, company_id=str(uuid.uuid4())) == []


def test_other_users_jobs_not_matched_by_tier(
    runner_client, make_user, make_company, make_job, auth_header
):
    me, other = make_user(), make_user()
    make_job(other["id"], make_company(other["id"], tier=1))

    assert _ids(runner_client, auth_header(me["id"]), tier=1) == []


def test_bad_query_params_are_422(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    for params in ({"active": "maybe"}, {"company_id": "nope"}, {"tier": "high"}):
        resp = runner_client.get("/jobs", headers=headers, params=params)
        assert resp.status_code == 422, params
