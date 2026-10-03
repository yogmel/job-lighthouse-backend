"""BE-021: fetch openings by CSS-selector scraping. No network or browser."""

import io
import socket
from collections.abc import Callable

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from job_lighthouse_backend.companies.sources import ScraperSource, Selectors
from job_lighthouse_backend.job_runner import scraper
from job_lighthouse_backend.job_runner.openings import FetchError, Opening
from job_lighthouse_backend.job_runner.scraper import (
    check_public_url,
    fetch_scraper,
    parse_openings,
)

CAREERS = "https://acme.example/careers"

PAGE = """
<html><body>
  <ul>
    <li class="job">
      <h3 class="t">  Senior
        Engineer </h3>
      <a class="go" href="/jobs/1">Apply</a>
      <span class="loc">Berlin</span>
    </li>
    <li class="job">
      <h3 class="t">Designer</h3>
      <a class="go" href="https://other.example/jobs/2#x">Apply</a>
    </li>
    <li class="job">
      <h3 class="t">Designer (dup)</h3>
      <a class="go" href="https://other.example/jobs/2#x">Apply</a>
    </li>
  </ul>
</body></html>
"""

# Hostnames the tests use; anything else fails resolution.
ADDRESSES = {
    "acme.example": "93.184.216.34",
    "other.example": "93.184.216.35",
    "cdn.example": "2606:4700::1",
    "internal.example": "10.0.0.5",
    "localhost": "127.0.0.1",
    "metadata.example": "169.254.169.254",
}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def getaddrinfo(host, port, *args, **kwargs):
        if host not in ADDRESSES:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ADDRESSES[host], 0))]

    monkeypatch.setattr(scraper.socket, "getaddrinfo", getaddrinfo)


def _source(strategy: str = "static", **selectors) -> ScraperSource:
    return ScraperSource.model_validate(
        {
            "kind": "scraper",
            "strategy": strategy,
            "selectors": {
                "careers_url": CAREERS,
                "job": ".job",
                "title": ".t",
                "link": "a.go",
                "location": ".loc",
                **selectors,
            },
        }
    )


def _response(status=200, body=b"", headers=None, encoding="utf-8"):
    resp = requests.Response()
    resp.status_code = status
    resp.raw = io.BytesIO(body)
    resp.headers = CaseInsensitiveDict(headers or {})
    resp.encoding = encoding
    return resp


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(
        self, url, *, headers=None, timeout=None, allow_redirects=True, stream=False
    ):
        assert timeout is not None
        assert allow_redirects is False, "redirects must be checked hop by hop"
        self.calls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


EXPECTED = [
    Opening("Senior Engineer", "https://acme.example/jobs/1", "Berlin"),
    Opening("Designer", "https://other.example/jobs/2#x", ""),
]


def test_static_uses_source_selectors():
    http = FakeHttp(_response(body=PAGE.encode()))
    assert fetch_scraper(_source(), http) == EXPECTED
    assert http.calls == [CAREERS]


def test_selectors_come_from_the_source_not_hardcoded():
    html = '<div class="row"><b>QA</b><a href="q">x</a></div>'
    http = FakeHttp(_response(body=html.encode()))
    source = _source(job="div.row", title="b", link="a", location=None)
    assert fetch_scraper(source, http) == [Opening("QA", "https://acme.example/q")]


def test_zero_cards_is_empty_not_failure():
    http = FakeHttp(_response(body=b"<html><p>No roles right now</p></html>"))
    assert fetch_scraper(_source(), http) == []


def test_follows_redirect_and_resolves_links_against_final_url():
    http = FakeHttp(
        _response(301, headers={"Location": "https://other.example/c/"}),
        _response(
            body=b'<li class="job"><h3 class="t">X</h3><a class="go" href="x"></a></li>'
        ),
    )
    assert fetch_scraper(_source(), http) == [Opening("X", "https://other.example/c/x")]
    assert http.calls == [CAREERS, "https://other.example/c/"]


def test_redirect_to_private_address_is_blocked():
    http = FakeHttp(_response(302, headers={"Location": "http://internal.example/"}))
    with pytest.raises(FetchError, match="non-public"):
        fetch_scraper(_source(), http)
    assert http.calls == [CAREERS]


def test_too_many_redirects():
    loop = [_response(302, headers={"Location": CAREERS}) for _ in range(10)]
    with pytest.raises(FetchError, match="redirects"):
        fetch_scraper(_source(), FakeHttp(*loop))


@pytest.mark.parametrize("status", [403, 404, 500, 503])
def test_non_200_is_failure(status):
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        fetch_scraper(_source(), FakeHttp(_response(status, body=PAGE.encode())))


def test_network_error_is_failure():
    http = FakeHttp(requests.Timeout("slow"))
    with pytest.raises(FetchError, match="Timeout"):
        fetch_scraper(_source(), http)


def test_read_error_is_failure():
    class Broken(io.RawIOBase):
        def read(self, *args):
            raise requests.ConnectionError("reset")

    resp = _response()
    resp.raw = Broken()
    with pytest.raises(FetchError, match="request failed"):
        fetch_scraper(_source(), FakeHttp(resp))


def test_page_too_large(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_PAGE_BYTES", 10)
    with pytest.raises(FetchError, match="too large"):
        fetch_scraper(_source(), FakeHttp(_response(body=b"x" * 100)))


@pytest.mark.parametrize(
    "card",
    [
        '<li class="job"><a class="go" href="/1"></a></li>',  # no title
        '<li class="job"><h3 class="t"> </h3><a class="go" href="/1"></a></li>',
        '<li class="job"><h3 class="t">T</h3></li>',  # no link
        '<li class="job"><h3 class="t">T</h3><a class="go"></a></li>',  # no href
        '<li class="job"><h3 class="t">T</h3><a class="go" href="  "></a></li>',
    ],
)
def test_incomplete_card_is_failure(card):
    # One good card doesn't save the page: a partial result could close jobs.
    good = '<li class="job"><h3 class="t">OK</h3><a class="go" href="/ok"></a></li>'
    with pytest.raises(FetchError, match="no title or link"):
        parse_openings(good + card, CAREERS, _source().selectors)


def test_non_http_link_is_failure():
    card = (
        '<li class="job"><h3 class="t">T</h3><a class="go" href="mailto:x@y"></a></li>'
    )
    with pytest.raises(FetchError, match="http"):
        parse_openings(card, CAREERS, _source().selectors)


def test_invalid_selector_is_failure():
    with pytest.raises(FetchError, match="invalid CSS selector"):
        parse_openings(PAGE, CAREERS, _source(job="li[").selectors)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://acme.example/", "http"),
        ("http://localhost:8001/", "non-public"),
        ("http://internal.example/", "non-public"),
        ("http://metadata.example/latest/", "non-public"),
        ("http://nowhere.example/", "resolve"),
    ],
)
def test_check_public_url_rejects(url, reason):
    with pytest.raises(FetchError, match=reason):
        check_public_url(url)


def test_check_public_url_accepts_public_v4_and_v6():
    check_public_url("https://acme.example/careers")
    check_public_url("https://cdn.example/app.js")


def test_private_careers_url_is_never_requested():
    http = FakeHttp()
    with pytest.raises(FetchError, match="non-public"):
        fetch_scraper(_source(careers_url="http://internal.example/jobs"), http)
    assert http.calls == []


def test_static_uses_a_fresh_session_by_default(monkeypatch):
    fake = FakeHttp(_response(body=PAGE.encode()))

    class _Session:
        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scraper.requests, "Session", _Session)
    assert fetch_scraper(_source()) == EXPECTED


def test_dynamic_uses_renderer_and_same_parsing():
    seen: list[tuple[str, Selectors]] = []

    def render(url: str, selectors: Selectors) -> tuple[str, str]:
        seen.append((url, selectors))
        return CAREERS, PAGE

    source = _source("dynamic")
    assert fetch_scraper(source, render=render) == EXPECTED
    assert seen == [(CAREERS, source.selectors)]


# --- Playwright, faked: no browser is launched.


class _FakeRequest:
    def __init__(self, url):
        self.url = url


class _FakeRoute:
    def __init__(self, url):
        self.request = _FakeRequest(url)
        self.outcome = None

    def abort(self, reason):
        self.outcome = f"abort:{reason}"

    def continue_(self):
        self.outcome = "continue"


class _FakePage:
    def __init__(self, status, wait_error=None, goto_error=None, idle_error=None):
        self.status = status
        self.wait_error = wait_error
        self.goto_error = goto_error
        self.idle_error = idle_error
        self.idle_waits: list[tuple[str, int]] = []
        self.url = "https://acme.example/careers#loaded"
        self.handler: Callable[[_FakeRoute], None] = lambda route: None

    def route(self, pattern, handler):
        self.handler = handler

    def goto(self, url, wait_until, timeout):
        if self.goto_error:
            raise self.goto_error
        return None if self.status is None else type("R", (), {"status": self.status})

    def wait_for_selector(self, selector, timeout):
        if self.wait_error:
            raise self.wait_error

    def wait_for_load_state(self, state, timeout):
        self.idle_waits.append((state, timeout))
        if self.idle_error:
            raise self.idle_error

    def content(self):
        return PAGE


class _FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self, user_agent):
        return self.page

    def close(self):
        self.closed = True


def _fake_playwright(monkeypatch, page):
    browser = _FakeBrowser(page)

    class _Pw:
        chromium = type("C", (), {"launch": staticmethod(lambda: browser)})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scraper, "sync_playwright", _Pw)
    return browser


def test_render_with_playwright(monkeypatch):
    page = _FakePage(200)
    browser = _fake_playwright(monkeypatch, page)
    assert fetch_scraper(_source("dynamic")) == EXPECTED
    assert browser.closed

    # The route guard blocks private subresources and lets public ones through.
    blocked, allowed = _FakeRoute("http://metadata.example/"), _FakeRoute(CAREERS)
    page.handler(blocked)
    page.handler(allowed)
    assert blocked.outcome == "abort:blockedbyclient"
    assert allowed.outcome == "continue"


def test_render_no_cards_is_empty(monkeypatch):
    page = _FakePage(200, wait_error=scraper.PlaywrightError("timeout"))
    page.content = lambda: "<html></html>"  # type: ignore[method-assign]
    _fake_playwright(monkeypatch, page)
    assert fetch_scraper(_source("dynamic")) == []


@pytest.mark.parametrize(
    ("status", "match"), [(404, "HTTP 404"), (None, "no response")]
)
def test_render_bad_status_is_failure(monkeypatch, status, match):
    browser = _fake_playwright(monkeypatch, _FakePage(status))
    with pytest.raises(FetchError, match=match):
        fetch_scraper(_source("dynamic"))
    assert browser.closed


def test_render_browser_error_is_failure(monkeypatch):
    page = _FakePage(200, goto_error=scraper.PlaywrightError("net::ERR"))
    browser = _fake_playwright(monkeypatch, page)
    with pytest.raises(FetchError, match="browser failed"):
        fetch_scraper(_source("dynamic"))
    assert browser.closed


def test_render_checks_careers_url_first(monkeypatch):
    _fake_playwright(monkeypatch, _FakePage(200))
    with pytest.raises(FetchError, match="non-public"):
        fetch_scraper(_source("dynamic", careers_url="http://localhost:3000/"))


UTF8_PAGE = (
    '<html><head><meta charset="utf-8"></head><body>'
    '<li class="job"><h3 class="t">Ingenieur München</h3>'
    '<a class="go" href="/jobs/m\u00fcnchen">x</a></li></body></html>'
).encode()


def test_utf8_meta_charset_without_header_charset():
    # requests would guess ISO-8859-1 for text/html without a charset.
    resp = _response(body=UTF8_PAGE, headers={"Content-Type": "text/html"})
    resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
    assert fetch_scraper(_source(), FakeHttp(resp)) == [
        Opening("Ingenieur München", "https://acme.example/jobs/münchen")
    ]


def test_header_charset_wins():
    body = UTF8_PAGE.decode().replace('charset="utf-8"', "").encode("latin-1")
    resp = _response(
        body=body, headers={"Content-Type": "text/html; charset=ISO-8859-1"}
    )
    [opening] = fetch_scraper(_source(), FakeHttp(resp))
    assert opening.title == "Ingenieur München"


def test_no_charset_anywhere_is_sniffed():
    body = UTF8_PAGE.decode().replace('<meta charset="utf-8">', "").encode()
    resp = _response(body=body, headers={"Content-Type": "text/html"}, encoding=None)
    [opening] = fetch_scraper(_source(), FakeHttp(resp))
    assert opening.title == "Ingenieur München"


def test_undecodable_page_is_failure(monkeypatch):
    class _Unreadable:
        unicode_markup = None

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(scraper, "UnicodeDammit", _Unreadable)
    with pytest.raises(FetchError, match="encoding"):
        fetch_scraper(_source(), FakeHttp(_response(body=UTF8_PAGE)))


def test_render_page_without_wait_for(monkeypatch):
    # Selector discovery renders before it knows any selector: it waits for
    # the network to settle instead (BE-051).
    page = _FakePage(200, wait_error=AssertionError("must not wait"))
    _fake_playwright(monkeypatch, page)
    assert scraper.render_page(CAREERS) == (page.url, PAGE)
    assert page.idle_waits == [("networkidle", scraper.SETTLE_WAIT_MS)]


def test_render_page_never_idle_still_parses(monkeypatch):
    # A page that keeps polling never goes idle; take what has rendered.
    page = _FakePage(200, idle_error=scraper.PlaywrightError("timeout"))
    _fake_playwright(monkeypatch, page)
    assert scraper.render_page(CAREERS) == (page.url, PAGE)


def test_scheduled_render_waits_on_selector_not_idle(monkeypatch):
    page = _FakePage(200, idle_error=AssertionError("must not wait for idle"))
    _fake_playwright(monkeypatch, page)
    assert fetch_scraper(_source("dynamic")) == EXPECTED
    assert page.idle_waits == []


def test_fetch_static_page_uses_own_session(monkeypatch):
    http = FakeHttp(_response(body=PAGE.encode()))

    class _Session:
        def __enter__(self):
            return http

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scraper.requests, "Session", _Session)
    assert scraper.fetch_static_page(CAREERS) == (CAREERS, PAGE)
    assert http.calls == [CAREERS]
