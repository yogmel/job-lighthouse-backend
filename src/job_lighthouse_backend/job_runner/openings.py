"""What a fetch returns: a company's current openings, or a failure.

Fetchers are synchronous (``requests``); the pipeline runs them in a thread.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import requests

# Every outbound request is bounded: (connect, read) seconds.
TIMEOUT = (5, 20)
USER_AGENT = "job-lighthouse/0.1 (+https://github.com/yogmel/job-lighthouse-backend)"


@dataclass(frozen=True)
class Opening:
    """One open posting. ``url`` is its identity (same URL = same job)."""

    title: str
    url: str
    location: str = ""
    description: str = ""


class FetchError(Exception):
    """The fetch failed. Never read as "zero openings"."""


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: tuple[float, float] | float | None = None,
    ) -> requests.Response: ...


def get_json(http: HttpClient, url: str, params: dict[str, Any] | None = None) -> Any:
    """GET ``url`` and decode JSON. Anything but a 200 with JSON is a
    ``FetchError``."""
    try:
        resp = http.get(
            url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        raise FetchError(f"request failed: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError("response is not JSON") from exc
