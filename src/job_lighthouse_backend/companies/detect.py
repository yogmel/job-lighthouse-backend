"""POST /companies/detect: turn a pasted careers URL into a draft ``Source``.

1. Known board URL (BE-033): verify with a live fetch of the board. No LLM.
2. Otherwise, LLM selector discovery (BE-034).
3. Neither works: ``needs_custom`` (a 200, not an error).

The response carries a scored sample of the openings found, scored the same
way a run scores new jobs. Nothing is stored: the user confirms by sending
the returned ``source`` unchanged to ``POST /companies``.

A URL that can't be fetched at all (or a matched board that won't answer)
is a **422**: the user likely pasted a wrong URL, and a custom handler
wouldn't fix that.
"""

import asyncio
import logging
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.db import get_session
from job_lighthouse_backend.job_runner.company_run import Fetcher, fetch_openings
from job_lighthouse_backend.job_runner.openings import FetchError, Opening
from job_lighthouse_backend.job_runner.runs_api import scorer_for
from job_lighthouse_backend.job_runner.scoring import (
    Match,
    Posting,
    Scorer,
    load_profile,
    score_postings,
)

from .ats import match_board, normalize_url
from .selector_discovery import (
    PageLoader,
    Proposer,
    create_openai_proposer,
    discover_selectors,
    load_page,
)
from .sources import BoardSource, ManualSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])

Session = Annotated[AsyncSession, Depends(get_session)]

# Openings scored for the confirm screen. Bounds LLM cost and latency.
SAMPLE_SIZE = 5


class DetectIn(BaseModel):
    url: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)
    ]


class SampleJob(BaseModel):
    title: str
    url: str
    location: str
    # Null when there is no profile, no LLM key, or scoring failed.
    match_score: int | None
    match_description: str | None


class DetectOut(BaseModel):
    """``detected``: ``source`` is set. ``needs_custom``: it is null."""

    status: Literal["detected", "needs_custom"]
    # How the source was found.
    method: Literal["board", "selectors"] | None
    source: ManualSource | None
    jobs_found: int
    sample: list[SampleJob]
    # Why it needs custom handling, else null.
    reason: str | None = None


def get_fetcher() -> Fetcher:
    """Dependency so tests can swap the network out."""
    return fetch_openings


def get_page_loader() -> PageLoader:
    """Dependency so tests can swap the network out."""
    return load_page


def get_proposer(request: Request) -> Proposer | None:
    """The selector proposer, or ``None`` when ``OPENAI_API_KEY`` isn't set.

    Built once per app.
    """
    state = request.app.state
    if not state.settings.openai_api_key:
        return None
    if getattr(state, "proposer", None) is None:
        state.proposer = create_openai_proposer(
            state.settings.openai_api_key, state.settings.openai_model
        )
    return state.proposer


def get_scorer(request: Request) -> Scorer | None:
    """Dependency so tests can swap the network out."""
    return scorer_for(request.app.state)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


async def _fetch_board(source: BoardSource, fetch: Fetcher) -> list[Opening]:
    try:
        return await asyncio.to_thread(fetch, source)
    except FetchError as exc:
        raise _unprocessable(f"{source.board} board failed: {exc}") from None
    except Exception as exc:
        logger.exception("Unexpected error verifying a %s board", source.board)
        raise _unprocessable(
            f"{source.board} board failed: unexpected error: {type(exc).__name__}"
        ) from None


@router.post(
    "/detect",
    response_model=DetectOut,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The URL or its board can't be fetched"
        }
    },
)
async def detect_company(
    body: DetectIn,
    user_id: CurrentUserId,
    session: Session,
    fetch: Annotated[Fetcher, Depends(get_fetcher)],
    load: Annotated[PageLoader, Depends(get_page_loader)],
    proposer: Annotated[Proposer | None, Depends(get_proposer)],
    scorer: Annotated[Scorer | None, Depends(get_scorer)],
) -> DetectOut:
    """Detect a ``Source`` for a careers URL. Stores nothing."""
    profile = await load_profile(session, user_id) if scorer else None
    # Don't hold a DB connection through fetches and LLM calls.
    await session.close()

    url = normalize_url(body.url)
    source: ManualSource
    method: Literal["board", "selectors"]
    board = match_board(url)
    if board is not None:
        source, method = board, "board"
        company = board.board_id
        openings = await _fetch_board(board, fetch)
    else:
        if proposer is None:
            return _needs_custom("selector discovery is not configured")
        try:
            found = await discover_selectors(url, proposer, load)
        except FetchError as exc:
            raise _unprocessable(f"page could not be loaded: {exc}") from None
        if found.source is None:
            return _needs_custom("no job list could be found on the page")
        source, method = found.source, "selectors"
        company = urlsplit(url).hostname or ""
        openings = found.openings

    # Same URL = same job; keep the first.
    unique: dict[str, Opening] = {}
    for o in openings:
        unique.setdefault(o.url, o)
    distinct = list(unique.values())
    sample = distinct[:SAMPLE_SIZE]
    matches: list[Match | None] = [None] * len(sample)
    if scorer is not None and profile is not None:
        postings = [
            Posting(o.title, company, o.location, o.description) for o in sample
        ]
        matches = await score_postings(postings, profile.text, scorer)

    return DetectOut(
        status="detected",
        method=method,
        source=source,
        jobs_found=len(distinct),
        sample=[
            SampleJob(
                title=o.title,
                url=o.url,
                location=o.location,
                match_score=m.score if m else None,
                match_description=m.description if m else None,
            )
            for o, m in zip(sample, matches, strict=True)
        ],
    )


def _needs_custom(reason: str) -> DetectOut:
    return DetectOut(
        status="needs_custom",
        method=None,
        source=None,
        jobs_found=0,
        sample=[],
        reason=reason,
    )
