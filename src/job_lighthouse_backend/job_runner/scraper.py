"""Fetch openings by scraping a careers page with the source's CSS selectors.

- ``static``: plain GET, parse the HTML.
- ``dynamic``: render in headless Chromium (Playwright), parse the result.

Both parse with the same ``Selectors``: every ``job`` card must yield a
``title`` and a ``link``. Network errors, a non-200, a bad selector or a card
missing either field raise ``FetchError``. Zero matched cards returns ``[]``:
ambiguous for a scraper, so BE-023 won't close jobs on it.

``careers_url`` is user-supplied and fetched from our server, so every URL we
request (redirects and page subresources included) must resolve only to
public addresses.
"""

import contextlib
import ipaddress
import socket
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag, UnicodeDammit
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers
from soupsieve import SelectorSyntaxError

from job_lighthouse_backend.companies.sources import ScraperSource, Selectors

from .openings import TIMEOUT, USER_AGENT, FetchError, Opening

MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024
# Playwright timeouts are in milliseconds.
RENDER_TIMEOUT_MS = 30_000
SELECTOR_WAIT_MS = 10_000
# Selector discovery has no selector to wait on: it waits this long at most
# for the network to go quiet, so job lists fetched after ``load`` show up.
SETTLE_WAIT_MS = 10_000

# Takes a URL, returns (final URL, HTML).
Renderer = Callable[[str, Selectors], tuple[str, str]]


class HtmlClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: tuple[float, float] | float | None = None,
        allow_redirects: bool = True,
        stream: bool = False,
    ) -> requests.Response: ...


def check_public_url(url: str) -> None:
    """Raise ``FetchError`` unless ``url`` is http(s) to a public address.

    Every address the host resolves to must be globally routable, so
    loopback, private, link-local (cloud metadata) and reserved ranges are
    refused. Known gap: DNS can change between this check and the request.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http(s) URLs can be fetched")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("host does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise FetchError("host resolves to a non-public address")


def _get_static(http: HtmlClient, url: str) -> tuple[str, str]:
    """GET ``url``, following redirects by hand so each hop is checked."""
    for _ in range(MAX_REDIRECTS + 1):
        check_public_url(url)
        try:
            resp = http.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise FetchError(f"request failed: {type(exc).__name__}") from exc
        with resp:
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code != 200:
                raise FetchError(f"HTTP {resp.status_code}")
            return url, _read_capped(resp)
    raise FetchError("too many redirects")


def _read_capped(resp: requests.Response) -> str:
    body = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise FetchError("page too large")
    except requests.RequestException as exc:
        raise FetchError(f"request failed: {type(exc).__name__}") from exc
    return _decode(bytes(body), resp.headers)


def _decode(body: bytes, headers: CaseInsensitiveDict[str]) -> str:
    """Decode HTML bytes: header charset, else ``<meta charset>``, else sniff.

    Not ``resp.text``: requests assumes ISO-8859-1 for ``text/html`` without
    a charset, which garbles UTF-8 pages that only declare it in a meta tag.
    """
    content_type = headers.get("content-type", "").lower()
    declared = (
        get_encoding_from_headers(headers) if "charset=" in content_type else None
    )
    dammit = UnicodeDammit(
        body, known_definite_encodings=[declared] if declared else [], is_html=True
    )
    if dammit.unicode_markup is None:
        raise FetchError("page encoding is unreadable")
    return dammit.unicode_markup


def fetch_static_page(url: str) -> tuple[str, str]:
    """GET ``url`` (redirects checked hop by hop); return (final URL, HTML)."""
    with requests.Session() as session:
        return _get_static(session, url)


def render_with_playwright(url: str, selectors: Selectors) -> tuple[str, str]:
    """Load ``url`` in headless Chromium and return (final URL, HTML).

    Every request the page makes goes through ``check_public_url``.
    """
    return render_page(url, wait_for=selectors.job)


def render_page(url: str, wait_for: str | None = None) -> tuple[str, str]:
    """Like ``render_with_playwright``; waits for ``wait_for`` if given, else
    for the network to go idle (bounded by ``SETTLE_WAIT_MS``).

    Network idle is waited for after ``load``, not as ``goto``'s condition:
    a page that polls never goes idle, and that must still parse.
    """
    check_public_url(url)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.route("**/*", _guard_route)
                resp = page.goto(url, wait_until="load", timeout=RENDER_TIMEOUT_MS)
                if resp is None or resp.status != 200:
                    status = "no response" if resp is None else f"HTTP {resp.status}"
                    raise FetchError(status)
                # No cards showing up is parsed as zero matches, not an error.
                with contextlib.suppress(PlaywrightError):
                    if wait_for is not None:
                        page.wait_for_selector(wait_for, timeout=SELECTOR_WAIT_MS)
                    else:
                        page.wait_for_load_state("networkidle", timeout=SETTLE_WAIT_MS)
                return page.url, page.content()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise FetchError(f"browser failed: {type(exc).__name__}") from exc


def _guard_route(route: Route) -> None:
    """Playwright route handler: abort requests to non-public hosts."""
    try:
        check_public_url(route.request.url)
    except FetchError:
        route.abort("blockedbyclient")
        return
    route.continue_()


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def parse_openings(html: str, base_url: str, selectors: Selectors) -> list[Opening]:
    """Apply ``selectors`` to ``html``. Links resolve against ``base_url``."""
    soup = BeautifulSoup(html, "html.parser")
    try:
        cards = soup.select(selectors.job)
        openings: dict[str, Opening] = {}
        for card in cards:
            title_tag = card.select_one(selectors.title)
            link_tag = card.select_one(selectors.link)
            location_tag = (
                card.select_one(selectors.location) if selectors.location else None
            )
            title = _text(title_tag) if title_tag else ""
            href = link_tag.get("href") if link_tag else None
            if not title or not isinstance(href, str) or not href.strip():
                raise FetchError("a job card has no title or link")
            url = urljoin(base_url, href.strip())
            if urlsplit(url).scheme not in ("http", "https"):
                raise FetchError("a job link is not an http(s) URL")
            location = _text(location_tag) if location_tag else ""
            # Same URL twice on one page is one job; keep the first.
            openings.setdefault(url, Opening(title=title, url=url, location=location))
    except SelectorSyntaxError as exc:
        raise FetchError("invalid CSS selector") from exc
    return list(openings.values())


def fetch_scraper(
    source: ScraperSource,
    http: HtmlClient | None = None,
    render: Renderer | None = None,
) -> list[Opening]:
    """Current openings on ``source.selectors.careers_url``.

    Raises ``FetchError`` on failure. ``[]`` means no card matched.
    """
    selectors = source.selectors
    url = str(selectors.careers_url)
    if source.strategy == "dynamic":
        final_url, html = (render or render_with_playwright)(url, selectors)
    elif http is not None:
        final_url, html = _get_static(http, url)
    else:
        final_url, html = fetch_static_page(url)
    return parse_openings(html, final_url, selectors)
