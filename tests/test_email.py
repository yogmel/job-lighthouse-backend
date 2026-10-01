"""BE-029: transactional email client (Resend)."""

import asyncio
from typing import Any

import pytest
import requests

from job_lighthouse_backend.common.email import (
    RESEND_URL,
    TIMEOUT,
    Email,
    EmailError,
    resend_mailer,
    send_resend,
)
from job_lighthouse_backend.common.settings import Settings, SettingsError

SENDER = "Job Lighthouse <digest@example.com>"
EMAIL = Email(to="user@example.com", subject="New jobs", text="Hello")


class FakeResponse:
    def __init__(self, status: int, body: Any = None, text: str | None = None):
        self.status_code = status
        self._body = body
        self._text = text

    def json(self) -> Any:
        if self._text is not None:
            raise ValueError("not JSON")
        return self._body


class FakeHttp:
    """Records each POST and answers with ``response`` (or raises it)."""

    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_success_returns_message_id_and_sends_payload() -> None:
    http = FakeHttp(FakeResponse(200, {"id": "msg-1"}))

    assert send_resend(http, "key", SENDER, EMAIL) == "msg-1"
    assert http.calls == [
        {
            "url": RESEND_URL,
            "json": {
                "from": SENDER,
                "to": ["user@example.com"],
                "subject": "New jobs",
                "text": "Hello",
            },
            "headers": {"Authorization": "Bearer key"},
            "timeout": TIMEOUT,
        }
    ]


def test_html_is_sent_when_given() -> None:
    http = FakeHttp(FakeResponse(200, {"id": "msg-1"}))
    email = Email(to="user@example.com", subject="s", text="t", html="<p>t</p>")

    send_resend(http, "key", SENDER, email)
    assert http.calls[0]["json"]["html"] == "<p>t</p>"


@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 500, 503])
def test_non_2xx_raises(status: int) -> None:
    http = FakeHttp(FakeResponse(status, {"message": "user@example.com is bad"}))

    with pytest.raises(EmailError) as exc_info:
        send_resend(http, "key", SENDER, EMAIL)
    assert str(exc_info.value) == f"HTTP {status}"


@pytest.mark.parametrize(
    "exc", [requests.Timeout(), requests.ConnectionError(), requests.RequestException()]
)
def test_network_error_raises(exc: Exception) -> None:
    with pytest.raises(EmailError, match="request failed"):
        send_resend(FakeHttp(exc), "key", SENDER, EMAIL)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(200, text="<html>"),
        FakeResponse(200, {}),
        FakeResponse(200, []),
        FakeResponse(200, {"id": ""}),
        FakeResponse(200, {"id": 5}),
    ],
)
def test_2xx_without_message_id_raises(response: FakeResponse) -> None:
    with pytest.raises(EmailError, match="unexpected response shape"):
        send_resend(FakeHttp(response), "key", SENDER, EMAIL)


def test_error_never_carries_key_or_recipient() -> None:
    http = FakeHttp(FakeResponse(500, {"message": "user@example.com"}))

    with pytest.raises(EmailError) as exc_info:
        send_resend(http, "secret-key", SENDER, EMAIL)
    assert "secret-key" not in str(exc_info.value)
    assert "user@example.com" not in str(exc_info.value)


def test_resend_mailer_is_async() -> None:
    http = FakeHttp(FakeResponse(201, {"id": "msg-2"}))
    send = resend_mailer("key", SENDER, http)

    assert asyncio.run(send(EMAIL)) == "msg-2"


def test_resend_mailer_failure_raises() -> None:
    send = resend_mailer("key", SENDER, FakeHttp(FakeResponse(500)))

    with pytest.raises(EmailError):
        asyncio.run(send(EMAIL))


def test_resend_mailer_opens_its_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHttp(FakeResponse(200, {"id": "msg-3"}))

    class Session:
        def __enter__(self) -> FakeHttp:
            return http

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(requests, "Session", Session)
    assert asyncio.run(resend_mailer("key", SENDER)(EMAIL)) == "msg-3"
    assert len(http.calls) == 1


# --- settings ----------------------------------------------------------------


def test_email_settings_default_to_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    settings = Settings.from_env()
    assert settings.resend_api_key is None
    assert settings.email_from is None


def test_email_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("RESEND_API_KEY", "re_key")
    monkeypatch.setenv("EMAIL_FROM", SENDER)
    settings = Settings.from_env()
    assert settings.resend_api_key == "re_key"
    assert settings.email_from == SENDER


def test_key_without_sender_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("RESEND_API_KEY", "re_key")
    with pytest.raises(SettingsError, match="EMAIL_FROM"):
        Settings.from_env()
