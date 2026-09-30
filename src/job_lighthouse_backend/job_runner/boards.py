"""Fetch openings from a board's public job API.

Each board has its own response shape; all are normalized to ``Opening``.
A non-200, non-JSON or wrongly shaped response is a ``FetchError``. Only a
well-formed response with no postings means "zero openings".
"""

from collections.abc import Callable
from urllib.parse import quote

import requests
from pydantic import BaseModel, ConfigDict, ValidationError

from job_lighthouse_backend.companies.sources import Board, BoardSource, NonEmptyStr

from .openings import FetchError, HttpClient, Opening, get_json

# SmartRecruiters pages its list. Stops a bad `totalFound` from looping.
SMARTRECRUITERS_PAGE_SIZE = 100
SMARTRECRUITERS_MAX_PAGES = 50


class _Shape(BaseModel):
    # Boards add fields over time; only the ones read here are checked.
    # A posting without a title or URL fails the whole fetch: URL is identity.
    model_config = ConfigDict(extra="ignore")


# --- Greenhouse: GET boards-api.greenhouse.io/v1/boards/{token}/jobs


class _GreenhouseLocation(_Shape):
    name: str | None = None


class _GreenhouseJob(_Shape):
    title: NonEmptyStr
    absolute_url: NonEmptyStr
    location: _GreenhouseLocation | None = None


class _GreenhouseBoard(_Shape):
    jobs: list[_GreenhouseJob]


def _greenhouse(http: HttpClient, board_id: str) -> list[Opening]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board_id, safe='')}/jobs"
    board = _GreenhouseBoard.model_validate(get_json(http, url))
    return [
        Opening(
            title=j.title,
            url=j.absolute_url,
            location=(j.location.name if j.location else None) or "",
        )
        for j in board.jobs
    ]


# --- Lever: GET api.lever.co/v0/postings/{site}?mode=json


class _LeverCategories(_Shape):
    location: str | None = None


class _LeverPosting(_Shape):
    text: NonEmptyStr
    hostedUrl: NonEmptyStr
    categories: _LeverCategories | None = None
    descriptionPlain: str | None = None


def _lever(http: HttpClient, board_id: str) -> list[Opening]:
    url = f"https://api.lever.co/v0/postings/{quote(board_id, safe='')}"
    raw = get_json(http, url, params={"mode": "json"})
    if not isinstance(raw, list):
        raise FetchError("unexpected response shape")
    postings = [_LeverPosting.model_validate(p) for p in raw]
    return [
        Opening(
            title=p.text,
            url=p.hostedUrl,
            location=(p.categories.location if p.categories else None) or "",
            description=p.descriptionPlain or "",
        )
        for p in postings
    ]


# --- Ashby: GET api.ashbyhq.com/posting-api/job-board/{name}


class _AshbyJob(_Shape):
    title: NonEmptyStr
    jobUrl: NonEmptyStr
    location: str | None = None
    descriptionPlain: str | None = None
    isListed: bool = True


class _AshbyBoard(_Shape):
    jobs: list[_AshbyJob]


def _ashby(http: HttpClient, board_id: str) -> list[Opening]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(board_id, safe='')}"
    board = _AshbyBoard.model_validate(get_json(http, url))
    return [
        Opening(
            title=j.title,
            url=j.jobUrl,
            location=j.location or "",
            description=j.descriptionPlain or "",
        )
        for j in board.jobs
        if j.isListed
    ]


# --- SmartRecruiters: GET api.smartrecruiters.com/v1/companies/{id}/postings


class _SmartRecruitersLocation(_Shape):
    fullLocation: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None


class _SmartRecruitersPosting(_Shape):
    id: NonEmptyStr
    name: NonEmptyStr
    location: _SmartRecruitersLocation | None = None


class _SmartRecruitersPage(_Shape):
    totalFound: int
    content: list[_SmartRecruitersPosting]


def _smartrecruiters_location(loc: _SmartRecruitersLocation | None) -> str:
    if loc is None:
        return ""
    if loc.fullLocation:
        return loc.fullLocation
    return ", ".join(p for p in (loc.city, loc.region, loc.country) if p)


def _smartrecruiters(http: HttpClient, board_id: str) -> list[Opening]:
    company = quote(board_id, safe="")
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    postings: list[_SmartRecruitersPosting] = []
    for page in range(SMARTRECRUITERS_MAX_PAGES):
        params = {
            "limit": SMARTRECRUITERS_PAGE_SIZE,
            "offset": page * SMARTRECRUITERS_PAGE_SIZE,
        }
        batch = _SmartRecruitersPage.model_validate(get_json(http, url, params))
        postings.extend(batch.content)
        if not batch.content or len(postings) >= batch.totalFound:
            break
    else:
        raise FetchError("too many pages")
    return [
        Opening(
            title=p.name,
            url=f"https://jobs.smartrecruiters.com/{company}/{quote(p.id, safe='')}",
            location=_smartrecruiters_location(p.location),
        )
        for p in postings
    ]


_FETCHERS: dict[Board, Callable[[HttpClient, str], list[Opening]]] = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
}


def fetch_board(source: BoardSource, http: HttpClient | None = None) -> list[Opening]:
    """Current openings on ``source.board``. Raises ``FetchError`` on failure.

    An empty list is a real answer: the board says there are no openings.
    """
    fetch = _FETCHERS[source.board]
    try:
        if http is not None:
            return fetch(http, source.board_id)
        with requests.Session() as session:
            return fetch(session, source.board_id)
    except ValidationError as exc:
        raise FetchError("unexpected response shape") from exc
