"""BE-037: GET /runs."""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from fastapi.testclient import TestClient

from .conftest import needs_db

pytestmark = needs_db

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _make_run(
    db: psycopg.Connection,
    user_id: uuid.UUID,
    started_at: datetime,
    status: str = "success",
    trigger: str = "cron",
    jobs_found: int = 0,
    error: str | None = None,
) -> uuid.UUID:
    finished_at = None if status == "running" else started_at + timedelta(minutes=1)
    row = db.execute(
        "INSERT INTO runs"
        " (user_id, started_at, finished_at, status, trigger, jobs_found, error)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, started_at, finished_at, status, trigger, jobs_found, error),
    ).fetchone()
    assert row is not None
    return row[0]


def _ids(client: TestClient, headers: dict[str, str], **params: int) -> list[str]:
    resp = client.get("/runs", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()]


def test_requires_token(runner_client: TestClient):
    assert runner_client.get("/runs").status_code == 401


def test_empty_list(runner_client, make_user, auth_header):
    user = make_user()
    assert _ids(runner_client, auth_header(user["id"])) == []


def test_returns_only_own_runs_most_recent_first(
    runner_client, db, make_user, auth_header
):
    me, other = make_user(), make_user()
    middle = _make_run(db, me["id"], T0)
    newest = _make_run(db, me["id"], T0 + timedelta(hours=1), status="running")
    oldest = _make_run(db, me["id"], T0 - timedelta(hours=1), status="failed")
    _make_run(db, other["id"], T0 + timedelta(hours=2))

    assert _ids(runner_client, auth_header(me["id"])) == [
        str(newest),
        str(middle),
        str(oldest),
    ]


def test_response_shape(runner_client, db, make_user, auth_header):
    user = make_user()
    run = _make_run(
        db,
        user["id"],
        T0,
        status="failed",
        trigger="manual",
        jobs_found=3,
        error="boom",
    )

    body = runner_client.get("/runs", headers=auth_header(user["id"])).json()
    assert body == [
        {
            "id": str(run),
            "status": "failed",
            "trigger": "manual",
            "started_at": body[0]["started_at"],
            "finished_at": body[0]["finished_at"],
            "jobs_found": 3,
            "error": "boom",
        }
    ]
    assert datetime.fromisoformat(body[0]["started_at"]) == T0
    assert datetime.fromisoformat(body[0]["finished_at"]) == T0 + timedelta(minutes=1)


def test_limit(runner_client, db, make_user, auth_header):
    user = make_user()
    runs = [_make_run(db, user["id"], T0 + timedelta(hours=i)) for i in range(3)]
    headers = auth_header(user["id"])

    assert _ids(runner_client, headers, limit=2) == [str(runs[2]), str(runs[1])]


def test_limit_bounds(runner_client, make_user, auth_header):
    headers = auth_header(make_user()["id"])
    for bad in (0, 201):
        resp = runner_client.get("/runs", headers=headers, params={"limit": bad})
        assert resp.status_code == 422
