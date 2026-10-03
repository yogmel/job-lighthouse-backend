"""BE-041, BE-042: POST /auth/password-reset/request and /confirm."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from fastapi.testclient import TestClient

from job_lighthouse_backend.common.email import Email, EmailError
from job_lighthouse_backend.common.settings import Settings
from job_lighthouse_backend.companies.auth.password_reset import (
    RESET_REQUESTS_PER_HOUR,
    build_reset_email,
    get_reset_mailer,
)

from .conftest import needs_db

RESET_URL = "https://app.example/reset-password"
NEW_PASSWORD = "a brand new password"  # noqa: S105 -- test fixture


class FakeMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Email] = []
        self.fail = fail

    async def __call__(self, email: Email) -> str:
        if self.fail:
            raise EmailError("HTTP 500")
        self.sent.append(email)
        return "msg-1"


@pytest.fixture
def reset_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PASSWORD_RESET_URL", RESET_URL)
    return RESET_URL


@pytest.fixture
def use_mailer(companies_client: TestClient) -> Iterator:
    # The client's app: test_cors reloads the module, replacing ``main.app``.
    overrides = companies_client.app.dependency_overrides  # type: ignore[attr-defined]

    def _use(fake: FakeMailer) -> FakeMailer:
        overrides[get_reset_mailer] = lambda: fake
        return fake

    yield _use
    overrides.pop(get_reset_mailer, None)


@pytest.fixture
def mailer(reset_url: str, use_mailer) -> FakeMailer:
    return use_mailer(FakeMailer())


def _request(client: TestClient, email: str):
    return client.post("/auth/password-reset/request", json={"email": email})


def _confirm(client: TestClient, token: str, password: str = NEW_PASSWORD):
    return client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": password},
    )


def _token_from(email: Email) -> str:
    link = email.text.split("\n")[3]
    assert link.startswith(RESET_URL + "?")
    return parse_qs(urlsplit(link).query)["token"][0]


def _tokens(db: psycopg.Connection, user_id) -> list[tuple]:
    return db.execute(
        "SELECT token_hash, expires_at, used_at FROM password_reset_tokens"
        " WHERE user_id = %s",
        (user_id,),
    ).fetchall()


def _login(client: TestClient, email: str, password: str) -> int:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    return resp.status_code


# --- request -----------------------------------------------------------------


@needs_db
def test_known_email_gets_a_link_and_only_the_hash_is_stored(
    mailer, companies_client, make_user, db
):
    user = make_user()
    resp = _request(companies_client, user["email"])
    assert resp.status_code == 202

    [email] = mailer.sent
    assert email.to == user["email"]
    token = _token_from(email)
    assert email.html is not None and token in email.html
    [(token_hash, expires_at, used_at)] = _tokens(db, user["id"])
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token_hash != token
    assert used_at is None
    expected = datetime.now(UTC) + timedelta(hours=1)
    assert abs(expires_at - expected) < timedelta(minutes=1)


@needs_db
def test_unknown_email_gets_the_same_response_and_no_email(
    mailer, companies_client, make_user, unique_email
):
    known = _request(companies_client, make_user()["email"])
    unknown = _request(companies_client, unique_email())
    assert (unknown.status_code, unknown.content) == (known.status_code, known.content)
    assert len(mailer.sent) == 1


@needs_db
def test_email_matches_case_insensitively(mailer, companies_client, make_user):
    user = make_user()
    _request(companies_client, user["email"].upper())
    assert [e.to for e in mailer.sent] == [user["email"]]


@needs_db
def test_google_only_account_gets_a_link(mailer, companies_client, make_user):
    user = make_user(password=None, google_id="google-sub-reset")
    _request(companies_client, user["email"])
    assert len(mailer.sent) == 1


@needs_db
def test_requests_over_the_limit_send_nothing(mailer, companies_client, make_user):
    user = make_user()
    for _ in range(RESET_REQUESTS_PER_HOUR + 1):
        assert _request(companies_client, user["email"]).status_code == 202
    assert len(mailer.sent) == RESET_REQUESTS_PER_HOUR


@needs_db
def test_send_failure_still_gets_the_same_response(
    reset_url, use_mailer, companies_client, make_user
):
    use_mailer(FakeMailer(fail=True))
    resp = _request(companies_client, make_user()["email"])
    assert resp.status_code == 202


@needs_db
def test_without_reset_url_nothing_is_sent(use_mailer, companies_client, make_user, db):
    fake = use_mailer(FakeMailer())
    user = make_user()
    resp = _request(companies_client, user["email"])
    assert resp.status_code == 202
    assert fake.sent == []
    assert _tokens(db, user["id"]) == []


@needs_db
def test_without_resend_key_nothing_is_sent(reset_url, companies_client, make_user):
    # The real dependency: conftest unsets RESEND_API_KEY.
    resp = _request(companies_client, make_user()["email"])
    assert resp.status_code == 202


@needs_db
def test_malformed_email_is_422(companies_client):
    assert _request(companies_client, "not-an-email").status_code == 422


# --- confirm -----------------------------------------------------------------


def _issue(client: TestClient, mailer: FakeMailer, email: str) -> str:
    _request(client, email)
    return _token_from(mailer.sent[-1])


@needs_db
def test_confirm_sets_the_password_and_uses_the_token(
    mailer, companies_client, make_user, db
):
    user = make_user()
    token = _issue(companies_client, mailer, user["email"])

    assert _confirm(companies_client, token).status_code == 204
    assert _login(companies_client, user["email"], NEW_PASSWORD) == 200
    assert _login(companies_client, user["email"], user["password"]) == 401
    [(_, _, used_at)] = _tokens(db, user["id"])
    assert used_at is not None
    verified = db.execute(
        "SELECT email_verified FROM users WHERE id = %s", (user["id"],)
    ).fetchone()
    assert verified == (True,)


@needs_db
def test_token_cannot_be_reused(mailer, companies_client, make_user):
    user = make_user()
    token = _issue(companies_client, mailer, user["email"])
    assert _confirm(companies_client, token).status_code == 204

    resp = _confirm(companies_client, token, "yet another password")
    assert resp.status_code == 400
    assert _login(companies_client, user["email"], NEW_PASSWORD) == 200


@needs_db
def test_expired_token_is_rejected(mailer, companies_client, make_user, db):
    user = make_user()
    token = _issue(companies_client, mailer, user["email"])
    db.execute(
        "UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'"
        " WHERE user_id = %s",
        (user["id"],),
    )

    assert _confirm(companies_client, token).status_code == 400
    assert _login(companies_client, user["email"], user["password"]) == 200


@needs_db
def test_unknown_token_is_rejected(companies_client):
    resp = _confirm(companies_client, "no-such-token")
    assert resp.status_code == 400
    assert resp.json()["detail"]


@needs_db
def test_confirm_voids_the_users_other_tokens(mailer, companies_client, make_user):
    user = make_user()
    first = _issue(companies_client, mailer, user["email"])
    second = _issue(companies_client, mailer, user["email"])

    assert _confirm(companies_client, second).status_code == 204
    assert _confirm(companies_client, first).status_code == 400


@needs_db
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"token": "x", "new_password": "short"}, id="short-password"),
        pytest.param({"token": "", "new_password": NEW_PASSWORD}, id="empty-token"),
        pytest.param({"token": "x" * 129, "new_password": NEW_PASSWORD}, id="long"),
        pytest.param({"new_password": NEW_PASSWORD}, id="no-token"),
    ],
)
def test_invalid_confirm_body_is_422(companies_client, body):
    resp = companies_client.post("/auth/password-reset/confirm", json=body)
    assert resp.status_code == 422


# --- units -------------------------------------------------------------------


def test_reset_email_escapes_the_link():
    email = build_reset_email("a@example.com", 'https://x/?token="><b>')
    assert email.html is not None
    assert '"><b>' not in email.html
    assert "&quot;&gt;&lt;b&gt;" in email.html
    assert 'https://x/?token="><b>' in email.text


def test_settings_read_password_reset_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("PASSWORD_RESET_URL", RESET_URL)
    assert Settings.from_env().password_reset_url == RESET_URL
    monkeypatch.setenv("PASSWORD_RESET_URL", "")
    assert Settings.from_env().password_reset_url is None


def test_reset_mailer_is_built_once_from_settings(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from job_lighthouse_backend.companies.auth import password_reset

    monkeypatch.setattr(
        password_reset, "resend_mailer", lambda key, sender: ("mailer", key, sender)
    )
    settings = Settings(
        database_url="unused",
        jwt_secret="unused",  # noqa: S106 -- test fixture
        resend_api_key="r",
        email_from="a@example.com",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings))
    )
    first = get_reset_mailer(request)  # type: ignore[arg-type]
    assert first == ("mailer", "r", "a@example.com")
    assert get_reset_mailer(request) is first  # type: ignore[arg-type]
