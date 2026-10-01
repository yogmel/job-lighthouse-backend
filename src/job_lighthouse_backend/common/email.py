"""Transactional email, sent through Resend's HTTP API.

Shared by both services: the runner's digest and, later, password reset.

A send either returns (the provider confirmed it) or raises ``EmailError``.
Nothing in between, so a caller knows when it must not record the email as
sent (e.g. leave ``Job.notified_at`` null).
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
# (connect, read) seconds. A slow provider fails the send; it doesn't hang it.
TIMEOUT = (5, 20)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    # Plain text is required; HTML is optional. Escape anything untrusted.
    text: str
    html: str | None = None


# Returns the provider's message id once the send is confirmed. Raises
# ``EmailError`` otherwise.
Mailer = Callable[[Email], Coroutine[Any, Any, str]]


class EmailError(Exception):
    """The provider didn't confirm the send. Treat the email as not sent."""


class HttpPoster(Protocol):
    def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: tuple[float, float] | float | None = None,
    ) -> requests.Response: ...


def send_resend(http: HttpPoster, api_key: str, sender: str, email: Email) -> str:
    """POST ``email`` to Resend. Returns its message id.

    Only a 2xx with an ``id`` counts as sent. Errors carry the status code or
    exception type, never the response body: it may echo the recipient.
    """
    payload: dict[str, Any] = {
        "from": sender,
        "to": [email.to],
        "subject": email.subject,
        "text": email.text,
    }
    if email.html is not None:
        payload["html"] = email.html
    try:
        resp = http.post(
            RESEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise EmailError(f"request failed: {type(exc).__name__}") from exc
    if not 200 <= resp.status_code < 300:
        raise EmailError(f"HTTP {resp.status_code}")
    try:
        message_id = resp.json()["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise EmailError("unexpected response shape") from exc
    if not isinstance(message_id, str) or not message_id:
        raise EmailError("unexpected response shape")
    return message_id


def resend_mailer(api_key: str, sender: str, http: HttpPoster | None = None) -> Mailer:
    """A ``Mailer`` backed by Resend. Each send runs in a thread."""

    def post(email: Email) -> str:
        if http is not None:
            return send_resend(http, api_key, sender, email)
        with requests.Session() as session:
            return send_resend(session, api_key, sender, email)

    async def send(email: Email) -> str:
        return await asyncio.to_thread(post, email)

    return send
