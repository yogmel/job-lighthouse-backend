"""BE-020: fetch openings from board APIs. No network: HTTP is faked."""

import json
from typing import Any

import pytest
import requests

from job_lighthouse_backend.companies.sources import BoardSource
from job_lighthouse_backend.job_runner import boards
from job_lighthouse_backend.job_runner.boards import fetch_board
from job_lighthouse_backend.job_runner.openings import FetchError, Opening


def _response(status: int = 200, body: Any = None, raw: bytes | None = None):
    resp = requests.Response()
    resp.status_code = status
    resp._content = raw if raw is not None else json.dumps(body).encode()
    return resp


class FakeHttp:
    """Returns queued responses in order and records each call."""

    def __init__(self, *responses: requests.Response | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        assert timeout is not None, "every request needs a timeout"
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _source(board: str, board_id: str = "acme") -> BoardSource:
    return BoardSource.model_validate(
        {"kind": "board", "board": board, "board_id": board_id}
    )


def test_greenhouse():
    http = FakeHttp(
        _response(
            body={
                "jobs": [
                    {
                        "title": "Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "location": {"name": "Berlin"},
                        "id": 1,
                    },
                    {
                        "title": "Designer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                    },
                ],
                "meta": {"total": 2},
            }
        )
    )
    assert fetch_board(_source("greenhouse"), http) == [
        Opening("Engineer", "https://boards.greenhouse.io/acme/jobs/1", "Berlin"),
        Opening("Designer", "https://boards.greenhouse.io/acme/jobs/2", ""),
    ]
    assert http.calls == [
        ("https://boards-api.greenhouse.io/v1/boards/acme/jobs", None)
    ]


def test_lever():
    http = FakeHttp(
        _response(
            body=[
                {
                    "text": "Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                    "categories": {"location": "Remote", "team": "Eng"},
                    "descriptionPlain": "Build things",
                },
                {"text": "PM", "hostedUrl": "https://jobs.lever.co/acme/def"},
            ]
        )
    )
    assert fetch_board(_source("lever"), http) == [
        Opening("Engineer", "https://jobs.lever.co/acme/abc", "Remote", "Build things"),
        Opening("PM", "https://jobs.lever.co/acme/def"),
    ]
    assert http.calls == [("https://api.lever.co/v0/postings/acme", {"mode": "json"})]


def test_lever_eu_uses_eu_api_host():
    # BE-052: jobs.eu.lever.co boards 404 on api.lever.co.
    http = FakeHttp(
        _response(body=[{"text": "PM", "hostedUrl": "https://jobs.eu.lever.co/acme/d"}])
    )
    source = BoardSource.model_validate(
        {"kind": "board", "board": "lever", "board_id": "acme", "region": "eu"}
    )
    assert fetch_board(source, http) == [
        Opening("PM", "https://jobs.eu.lever.co/acme/d")
    ]
    assert http.calls == [
        ("https://api.eu.lever.co/v0/postings/acme", {"mode": "json"})
    ]


def test_ashby_skips_unlisted():
    http = FakeHttp(
        _response(
            body={
                "jobs": [
                    {
                        "title": "Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                        "location": "London",
                        "descriptionPlain": "Hi",
                    },
                    {
                        "title": "Hidden",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/2",
                        "isListed": False,
                    },
                ]
            }
        )
    )
    assert fetch_board(_source("ashby"), http) == [
        Opening("Engineer", "https://jobs.ashbyhq.com/acme/1", "London", "Hi")
    ]
    assert http.calls == [("https://api.ashbyhq.com/posting-api/job-board/acme", None)]


def test_smartrecruiters_pages_until_total():
    page_size = boards.SMARTRECRUITERS_PAGE_SIZE
    first = [
        {"id": str(i), "name": f"Job {i}", "location": {"city": "Porto"}}
        for i in range(page_size)
    ]
    second = [
        {
            "id": "last",
            "name": "Last",
            "location": {"fullLocation": "Lisbon, Portugal", "city": "Lisbon"},
        }
    ]
    http = FakeHttp(
        _response(body={"totalFound": page_size + 1, "content": first}),
        _response(body={"totalFound": page_size + 1, "content": second}),
    )
    openings = fetch_board(_source("smartrecruiters"), http)
    assert len(openings) == page_size + 1
    assert openings[0] == Opening(
        "Job 0", "https://jobs.smartrecruiters.com/acme/0", "Porto"
    )
    assert openings[-1] == Opening(
        "Last", "https://jobs.smartrecruiters.com/acme/last", "Lisbon, Portugal"
    )
    url = "https://api.smartrecruiters.com/v1/companies/acme/postings"
    assert http.calls == [
        (url, {"limit": page_size, "offset": 0}),
        (url, {"limit": page_size, "offset": page_size}),
    ]


def test_smartrecruiters_location_parts():
    http = FakeHttp(
        _response(
            body={
                "totalFound": 2,
                "content": [
                    {
                        "id": "1",
                        "name": "A",
                        "location": {"city": "Austin", "region": "TX", "country": "us"},
                    },
                    {"id": "2", "name": "B"},
                ],
            }
        )
    )
    openings = fetch_board(_source("smartrecruiters"), http)
    assert [o.location for o in openings] == ["Austin, TX, us", ""]


def test_smartrecruiters_stops_on_empty_page():
    http = FakeHttp(_response(body={"totalFound": 999, "content": []}))
    assert fetch_board(_source("smartrecruiters"), http) == []
    assert len(http.calls) == 1


def test_smartrecruiters_page_cap_is_a_failure(monkeypatch):
    monkeypatch.setattr(boards, "SMARTRECRUITERS_MAX_PAGES", 2)
    monkeypatch.setattr(boards, "SMARTRECRUITERS_PAGE_SIZE", 1)
    page = {"totalFound": 10, "content": [{"id": "1", "name": "A"}]}
    http = FakeHttp(_response(body=page), _response(body=page))
    with pytest.raises(FetchError):
        fetch_board(_source("smartrecruiters"), http)


@pytest.mark.parametrize(
    ("board", "empty"),
    [
        ("greenhouse", {"jobs": []}),
        ("lever", []),
        ("ashby", {"jobs": []}),
        ("smartrecruiters", {"totalFound": 0, "content": []}),
    ],
)
def test_empty_board_is_empty_list_not_failure(board, empty):
    assert fetch_board(_source(board), FakeHttp(_response(body=empty))) == []


def test_board_id_is_url_quoted():
    http = FakeHttp(_response(body={"jobs": []}))
    fetch_board(_source("greenhouse", "../admin?x=1"), http)
    assert http.calls[0][0] == (
        "https://boards-api.greenhouse.io/v1/boards/..%2Fadmin%3Fx%3D1/jobs"
    )


@pytest.mark.parametrize("board", ["greenhouse", "lever", "ashby", "smartrecruiters"])
@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_non_200_is_failure(board, status):
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        fetch_board(_source(board), FakeHttp(_response(status, body={"jobs": []})))


def test_redirect_status_is_failure():
    # requests follows redirects itself; a 3xx here means it gave up.
    with pytest.raises(FetchError):
        fetch_board(_source("lever"), FakeHttp(_response(301, body=[])))


def test_non_json_is_failure():
    http = FakeHttp(_response(raw=b"<html>maintenance</html>"))
    with pytest.raises(FetchError, match="not JSON"):
        fetch_board(_source("greenhouse"), http)


def test_network_error_is_failure():
    http = FakeHttp(requests.ConnectionError("refused"))
    with pytest.raises(FetchError, match="ConnectionError"):
        fetch_board(_source("ashby"), http)


@pytest.mark.parametrize(
    ("board", "body"),
    [
        ("greenhouse", {}),
        ("greenhouse", {"jobs": [{"title": "No URL"}]}),
        ("greenhouse", {"jobs": [{"title": "", "absolute_url": "https://x/1"}]}),
        ("lever", {"postings": []}),
        ("lever", [{"text": "No URL"}]),
        ("ashby", {"jobs": "nope"}),
        ("ashby", {"jobs": [{"title": "T", "jobUrl": "  "}]}),
        ("smartrecruiters", {"content": []}),
        ("smartrecruiters", {"totalFound": 1, "content": [{"name": "No id"}]}),
        ("greenhouse", None),
    ],
)
def test_malformed_body_is_failure(board, body):
    with pytest.raises(FetchError, match="shape"):
        fetch_board(_source(board), FakeHttp(_response(body=body)))


def test_uses_a_fresh_session_by_default(monkeypatch):
    fake = FakeHttp(_response(body={"jobs": []}))

    class _Session:
        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(boards.requests, "Session", _Session)
    assert fetch_board(_source("greenhouse")) == []
    assert len(fake.calls) == 1
