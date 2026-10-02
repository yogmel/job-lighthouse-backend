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


# BE-046: paging.


def _page(
    client: TestClient, headers: dict[str, str], **params: str | int
) -> tuple[list[str], str | None]:
    resp = client.get("/jobs", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [j["id"] for j in resp.json()], resp.headers.get("X-Next-Cursor")


def _all_pages(
    client: TestClient, headers: dict[str, str], **params: str | int
) -> list[list[str]]:
    pages = []
    cursor: str | None = None
    while True:
        extra = {"cursor": cursor} if cursor else {}
        ids, cursor = _page(client, headers, **params, **extra)
        pages.append(ids)
        if cursor is None:
            return pages


def test_default_limit_is_bounded(
    runner_client, make_user, make_company, make_job, auth_header
):
    from job_lighthouse_backend.job_runner.jobs import DEFAULT_LIMIT

    user = make_user()
    company = make_company(user["id"])
    for i in range(DEFAULT_LIMIT + 1):
        make_job(user["id"], company, date=T0 + timedelta(minutes=i))

    ids, cursor = _page(runner_client, auth_header(user["id"]))
    assert len(ids) == DEFAULT_LIMIT
    assert cursor is not None


def test_pages_cover_every_job_once_in_order(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    company = make_company(user["id"])
    # Two pairs share a date, so the id breaks the tie across a page edge.
    dates = [T0, T0, T0 + timedelta(days=1), T0 + timedelta(days=1), T0 - timedelta(1)]
    for d in dates:
        make_job(user["id"], company, date=d)
    headers = auth_header(user["id"])
    everything = _ids(runner_client, headers, limit=200)

    pages = _all_pages(runner_client, headers, limit=2)
    assert [len(p) for p in pages] == [2, 2, 1]
    assert [i for p in pages for i in p] == everything


def test_exact_multiple_has_no_next_cursor(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    company = make_company(user["id"])
    make_job(user["id"], company)
    make_job(user["id"], company)

    ids, cursor = _page(runner_client, auth_header(user["id"]), limit=2)
    assert len(ids) == 2
    assert cursor is None


def test_new_job_does_not_shift_pages(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    company = make_company(user["id"])
    jobs = [
        make_job(user["id"], company, date=T0 + timedelta(days=i)) for i in range(4)
    ]
    headers = auth_header(user["id"])

    first, cursor = _page(runner_client, headers, limit=2)
    assert cursor is not None
    # A run adds a newer job between page loads.
    make_job(user["id"], company, date=T0 + timedelta(days=10))
    second, _ = _page(runner_client, headers, limit=2, cursor=cursor)
    assert first + second == [str(j) for j in reversed(jobs)]


def test_paging_keeps_filters(
    runner_client, make_user, make_company, make_job, auth_header
):
    user = make_user()
    tier1 = make_company(user["id"], tier=1)
    tier2 = make_company(user["id"], tier=2)
    wanted = []
    for i in range(3):
        wanted.append(make_job(user["id"], tier1, date=T0 + timedelta(days=i)))
        make_job(user["id"], tier1, active=False, date=T0 + timedelta(days=i))
        make_job(user["id"], tier2, date=T0 + timedelta(days=i))
    headers = auth_header(user["id"])

    pages = _all_pages(
        runner_client, headers, limit=2, active="true", tier=1, company_id=str(tier1)
    )
    assert [i for p in pages for i in p] == [str(j) for j in reversed(wanted)]


def test_cursor_is_scoped_to_the_caller(
    runner_client, make_user, make_company, make_job, auth_header
):
    me, other = make_user(), make_user()
    make_job(other["id"], make_company(other["id"]), date=T0)
    make_job(other["id"], make_company(other["id"]), date=T0)
    _, cursor = _page(runner_client, auth_header(other["id"]), limit=1)
    assert cursor is not None
    mine = make_job(me["id"], make_company(me["id"]), date=T0 - timedelta(days=1))

    ids, _ = _page(runner_client, auth_header(me["id"]), cursor=cursor)
    assert ids == [str(mine)]


def test_bad_limit_or_cursor_is_422(runner_client, make_user, auth_header):
    import base64

    headers = auth_header(make_user()["id"])

    def b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode()).decode()

    for params in (
        {"limit": 0},
        {"limit": 201},
        {"cursor": ""},
        {"cursor": "not base64!"},
        {"cursor": b64("no separator")},
        {"cursor": b64(f"2026-09-01T00:00:00|{uuid.uuid4()}")},  # no timezone
        {"cursor": b64("2026-09-01T00:00:00+00:00|not-a-uuid")},
        {"cursor": base64.urlsafe_b64encode(b"\xff\xfe").decode()},
    ):
        resp = runner_client.get("/jobs", headers=headers, params=params)
        assert resp.status_code == 422, params
