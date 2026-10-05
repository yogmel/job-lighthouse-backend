"""BE-030: digest send + notified_at stamping (pipeline step 8)."""

import uuid
from types import SimpleNamespace

from job_lighthouse_backend.common.email import Email, EmailError
from job_lighthouse_backend.job_runner.digest import build_digest
from job_lighthouse_backend.job_runner.models import Job, Run
from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.pipeline import run_pipeline

from .aio import in_session
from .conftest import BOARD_SOURCE, TEST_JWT_SECRET, needs_db, wait_for_run


class FakeMailer:
    """Records each email. Raises ``EmailError`` while ``fail`` is set."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[Email] = []

    async def __call__(self, email: Email) -> str:
        if self.fail:
            raise EmailError("HTTP 500")
        self.sent.append(email)
        return f"msg-{len(self.sent)}"


def _url() -> str:
    return f"https://jobs.example/{uuid.uuid4().hex}"


def _fetch_fails(source):
    # A failed fetch closes nothing, so jobs seeded with make_job stay open.
    raise RuntimeError("board down")


def _run(db, user_id, fetch=_fetch_fails, mailer=None) -> int:
    row = db.execute(
        "INSERT INTO runs (user_id, status, trigger) VALUES (%s, 'running', 'manual')"
        " RETURNING id",
        (user_id,),
    ).fetchone()
    run = Run(id=row[0], user_id=user_id, status="running", trigger="manual")
    return in_session(lambda s: run_pipeline(s, run, fetch, None, mailer))


def _notified(db, user_id) -> dict[str, bool]:
    rows = db.execute(
        "SELECT title, notified_at IS NOT NULL FROM jobs WHERE user_id = %s",
        (user_id,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _titles(email: Email) -> list[str]:
    return [line for line in email.text.splitlines() if line.startswith("Job ")]


# --- pipeline --------------------------------------------------------------


@needs_db
def test_sends_unnotified_active_jobs_and_stamps_them(db, make_user, make_company):
    user = make_user()
    make_company(user["id"])
    fetch = [Opening("Job A", _url()), Opening("Job B", _url())]
    mailer = FakeMailer()

    _run(db, user["id"], lambda s: fetch, mailer)
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == user["email"]
    assert mailer.sent[0].subject == "2 new jobs on Job Lighthouse"
    assert sorted(_titles(mailer.sent[0])) == ["Job A", "Job B"]
    assert _notified(db, user["id"]) == {"Job A": True, "Job B": True}


@needs_db
def test_empty_digest_sends_nothing(db, make_user, make_company):
    user = make_user()
    make_company(user["id"])
    mailer = FakeMailer()

    _run(db, user["id"], lambda s: [], mailer)
    assert mailer.sent == []


@needs_db
def test_notified_jobs_are_not_sent_again(db, make_user, make_company):
    user = make_user()
    make_company(user["id"])
    old = Opening("Job Old", _url())
    mailer = FakeMailer()
    _run(db, user["id"], lambda s: [old], mailer)

    _run(db, user["id"], lambda s: [old], mailer)
    assert len(mailer.sent) == 1
    _run(db, user["id"], lambda s: [old, Opening("Job New", _url())], mailer)
    assert _titles(mailer.sent[-1]) == ["Job New"]


@needs_db
def test_failed_send_leaves_jobs_for_next_digest(db, make_user, make_company):
    user = make_user()
    make_company(user["id"])
    first = Opening("Job Tue", _url())
    mailer = FakeMailer(fail=True)

    # A failed send doesn't fail the run.
    assert _run(db, user["id"], lambda s: [first], mailer) == 1
    assert _notified(db, user["id"]) == {"Job Tue": False}

    mailer.fail = False
    _run(db, user["id"], lambda s: [first, Opening("Job Wed", _url())], mailer)
    assert sorted(_titles(mailer.sent[0])) == ["Job Tue", "Job Wed"]
    assert _notified(db, user["id"]) == {"Job Tue": True, "Job Wed": True}


@needs_db
def test_inactive_jobs_are_excluded(db, make_user, make_company, make_job):
    user = make_user()
    company = make_company(user["id"])
    make_job(user["id"], company, title="Job Closed", active=False)
    make_job(user["id"], company, title="Job Open")
    mailer = FakeMailer()

    _run(db, user["id"], mailer=mailer)
    assert _titles(mailer.sent[0]) == ["Job Open"]
    assert _notified(db, user["id"]) == {"Job Closed": False, "Job Open": True}


@needs_db
def test_jobs_of_paused_companies_are_excluded(db, make_user, make_company, make_job):
    user = make_user()
    company = make_company(user["id"], active=False)
    make_job(user["id"], company, title="Job Paused")
    mailer = FakeMailer()

    _run(db, user["id"], mailer=mailer)
    assert mailer.sent == []
    assert _notified(db, user["id"]) == {"Job Paused": False}


@needs_db
def test_resumed_company_jobs_go_out_in_next_digest(
    db, make_user, make_company, make_job
):
    user = make_user()
    company = make_company(user["id"], active=False)
    make_job(user["id"], company, title="Job Waiting")
    mailer = FakeMailer()

    _run(db, user["id"], mailer=mailer)
    assert mailer.sent == []

    db.execute("UPDATE companies SET active = true WHERE id = %s", (company,))
    _run(db, user["id"], mailer=mailer)
    assert _titles(mailer.sent[0]) == ["Job Waiting"]
    assert _notified(db, user["id"]) == {"Job Waiting": True}


@needs_db
def test_other_users_jobs_are_excluded(db, make_user, make_company, make_job):
    user, other = make_user(), make_user()
    make_company(user["id"])
    other_company = make_company(other["id"])
    make_job(other["id"], other_company, title="Job Other")
    mailer = FakeMailer()

    _run(db, user["id"], lambda s: [Opening("Job Mine", _url())], mailer)
    assert _titles(mailer.sent[0]) == ["Job Mine"]
    assert _notified(db, other["id"]) == {"Job Other": False}


@needs_db
def test_jobs_added_during_send_are_not_stamped(db, make_user, make_company, make_job):
    user = make_user()
    company = make_company(user["id"])
    make_job(user["id"], company, title="Job Sent")

    class AddsJobMidSend(FakeMailer):
        async def __call__(self, email: Email) -> str:
            make_job(user["id"], company, title="Job Late")
            return await super().__call__(email)

    _run(db, user["id"], mailer=AddsJobMidSend())
    assert _notified(db, user["id"]) == {"Job Sent": True, "Job Late": False}


@needs_db
def test_no_mailer_leaves_notified_at_null(db, make_user, make_company):
    user = make_user()
    make_company(user["id"])

    _run(db, user["id"], lambda s: [Opening("Job A", _url())], None)
    assert _notified(db, user["id"]) == {"Job A": False}


@needs_db
def test_jobs_sorted_by_score_unscored_last(db, make_user, make_company, make_job):
    user = make_user()
    company = make_company(user["id"])
    for title, score in [("Job Low", 10), ("Job None", None), ("Job High", 90)]:
        job_id = make_job(user["id"], company, title=title)
        db.execute("UPDATE jobs SET match_score = %s WHERE id = %s", (score, job_id))
    mailer = FakeMailer()

    _run(db, user["id"], mailer=mailer)
    assert _titles(mailer.sent[0]) == ["Job High", "Job Low", "Job None"]


# --- POST /runs --------------------------------------------------------------


@needs_db
def test_post_runs_sends_digest(db, make_user, make_company, auth_header):
    from fastapi.testclient import TestClient

    from job_lighthouse_backend.job_runner.main import app
    from job_lighthouse_backend.job_runner.runs_api import get_fetcher, get_mailer

    user = make_user()
    make_company(user["id"], source={**BOARD_SOURCE, "board_id": "x"})
    mailer = FakeMailer()
    app.dependency_overrides[get_fetcher] = lambda: lambda s: [Opening("Job A", _url())]
    app.dependency_overrides[get_mailer] = lambda: mailer
    try:
        with TestClient(app) as client:
            resp = client.post("/runs", headers=auth_header(user["id"]))
            assert resp.status_code == 202
            assert wait_for_run(db, resp.json()["id"])[0] == "success"
    finally:
        app.dependency_overrides.clear()
    assert [e.to for e in mailer.sent] == [user["email"]]
    assert _notified(db, user["id"]) == {"Job A": True}


def test_mailer_needs_api_key():
    from job_lighthouse_backend.common.settings import Settings
    from job_lighthouse_backend.job_runner.runs_api import get_mailer

    def request(**overrides):
        settings = Settings(
            database_url="unused", jwt_secret=TEST_JWT_SECRET, **overrides
        )
        state = SimpleNamespace(settings=settings)
        return SimpleNamespace(app=SimpleNamespace(state=state))

    assert get_mailer(request()) is None
    with_key = request(resend_api_key="re_test", email_from="a@example.com")
    mailer = get_mailer(with_key)
    assert mailer is not None
    # Built once, then reused.
    assert get_mailer(with_key) is mailer


# --- email content -----------------------------------------------------------


def _job(**fields) -> Job:
    base = {
        "title": "Engineer",
        "company": "Acme",
        "location": "Berlin",
        "url": "https://acme.example/1",
        "match_score": 87.0,
    }
    return Job(**{**base, **fields})


def test_digest_content():
    email = build_digest(
        "u@example.com", [_job(), _job(title="PM", location="", match_score=None)]
    )
    assert email.to == "u@example.com"
    assert email.subject == "2 new jobs on Job Lighthouse"
    assert "Engineer\nAcme · Berlin · match 87/100\nhttps://acme.example/1" in (
        email.text
    )
    assert "PM\nAcme · match unscored\n" in email.text
    assert email.html is not None
    assert '<a href="https://acme.example/1">Engineer</a>' in email.html


def test_digest_subject_singular():
    assert build_digest("u@example.com", [_job()]).subject == (
        "1 new job on Job Lighthouse"
    )


def test_digest_html_escapes_scraped_text():
    job = _job(
        title="<script>x</script>",
        company='A&B "Co"',
        url='https://e.example/?a=1&b="><img>',
    )
    html = build_digest("u@example.com", [job]).html
    assert html is not None
    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "A&amp;B &quot;Co&quot;" in html
    assert 'href="https://e.example/?a=1&amp;b=&quot;&gt;&lt;img&gt;"' in html


def test_digest_links_only_web_urls():
    html = build_digest("u@example.com", [_job(url="javascript:alert(1)")]).html
    assert html is not None
    assert "href" not in html
    assert "javascript:alert(1)" not in html
