"""POST /companies/detect: turn a pasted careers URL into a draft ``Source``.

1. Known board URL (BE-033): verify with a live fetch of the board. No LLM.
2. A known board embedded on or linked from the page (BE-050): same, but a
   board that won't answer falls through to step 3, since the page itself
   loaded fine.
3. Otherwise, LLM selector discovery (BE-034).
4. None works: ``needs_custom`` (a 200, not an error).

The response carries a scored sample of the openings found, scored the same
way a run scores new jobs. Nothing is stored: the user confirms by sending
the returned ``source`` unchanged to ``POST /companies``.

A URL that can't be fetched at all (or a matched board that won't answer)
is a **422**: the user likely pasted a wrong URL, and a custom handler
wouldn't fix that.

Everything runs inside the request, so it has a time budget (BE-048) that
ends before Nginx's 60s ``proxy_read_timeout``. Out of time while finding
the source: ``needs_custom``. Out of time while scoring: the sample is
returned unscored.
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
from .embeds import match_embedded_board
from .selector_discovery import (
    PageLoader,
    Proposer,
    Strategy,
    create_openai_proposer,
    discover_selectors,
    load_page,
)
from .sources import BoardSource, ManualSource

Found = tuple[ManualSource, list[Opening]]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])

Session = Annotated[AsyncSession, Depends(get_session)]

# Openings scored for the confirm screen. Bounds LLM cost and latency.
SAMPLE_SIZE = 5

# Seconds one detect may take, under Nginx's 60s default with margin. The
# worst case without it: static GET (25s), LLM (60s), render (30s load +
# 10s settle), LLM (60s), scoring (60s).
DETECT_BUDGET_SECONDS = 50.0
TIMED_OUT = "detection took too long"


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
    # Running out of time cancels the awaits, but not the work already in a
    # thread: a fetch or browser render runs on to its own timeout.
    deadline = asyncio.get_running_loop().time() + DETECT_BUDGET_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            found = await _find(url, fetch, _static_once(load), proposer)
    except TimeoutError:
        logger.warning("Detect ran out of time finding a source")
        return _needs_custom(TIMED_OUT)
    if isinstance(found, str):
        return _needs_custom(found)
    source, openings = found
    method: Literal["board", "selectors"]
    if isinstance(source, BoardSource):
        method, company = "board", source.board_id
    else:
        method, company = "selectors", urlsplit(url).hostname or ""

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
        try:
            async with asyncio.timeout_at(deadline):
                matches = await score_postings(postings, profile.text, scorer)
        except TimeoutError:
            logger.warning("Detect ran out of time scoring the sample")

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


async def _find(
    url: str, fetch: Fetcher, load: PageLoader, proposer: Proposer | None
) -> Found | str:
    """A source for ``url`` and its openings, or why it needs custom handling."""
    board = match_board(url)
    if board is not None:
        return board, await _fetch_board(board, fetch)
    embedded = await _embedded_board(url, fetch, load)
    if embedded is not None:
        return embedded
    if proposer is None:
        return "selector discovery is not configured"
    try:
        found = await discover_selectors(url, proposer, load)
    except FetchError as exc:
        raise _unprocessable(f"page could not be loaded: {exc}") from None
    if found.source is None:
        return "no job list could be found on the page"
    return found.source, found.openings


async def _embedded_board(url: str, fetch: Fetcher, load: PageLoader) -> Found | None:
    """The board the page embeds or links to, if it answers. No LLM."""
    try:
        final_url, html = await asyncio.to_thread(load, url, "static")
    except FetchError:
        # Discovery reports it, and may still load the page in a browser.
        return None
    board = match_embedded_board(html, final_url)
    if board is None:
        return None
    try:
        return board, await asyncio.to_thread(fetch, board)
    except Exception as exc:
        # E.g. a stale link to an old board. The page loaded, so try it.
        logger.info("Embedded %s board failed: %s", board.board, type(exc).__name__)
        return None


def _static_once(load: PageLoader) -> PageLoader:
    """``load``, but the static page is fetched at most once per URL.

    The embed check and selector discovery both read it.
    """
    pages: dict[str, tuple[str, str] | FetchError] = {}

    def cached(url: str, strategy: Strategy) -> tuple[str, str]:
        if strategy != "static":
            return load(url, strategy)
        if url not in pages:
            try:
                pages[url] = load(url, strategy)
            except FetchError as exc:
                pages[url] = exc
        page = pages[url]
        if isinstance(page, FetchError):
            raise page
        return page

    return cached


def _needs_custom(reason: str) -> DetectOut:
    return DetectOut(
        status="needs_custom",
        method=None,
        source=None,
        jobs_found=0,
        sample=[],
        reason=reason,
    )
