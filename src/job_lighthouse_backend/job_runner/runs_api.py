"""/runs: trigger a run by hand, and the scheduled run the tick loop starts.

Both use the same per-user lock and the same pipeline, so they can't overlap.
A manual run goes on in a background task, not inside the request: a run can
take longer than Nginx's ``proxy_read_timeout``. The request waits only until
the lock is taken and the ``Runs`` row is open.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import State

from job_lighthouse_backend.common.auth import CurrentUserId
from job_lighthouse_backend.common.email import Mailer, resend_mailer

from .company_run import Fetcher, fetch_openings
from .models import Run
from .pipeline import run_pipeline
from .runs import Pipeline, execute_run
from .scoring import Scorer, create_openai_scorer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def get_fetcher() -> Fetcher:
    """Dependency so tests can swap the network out."""
    return fetch_openings


def get_scorer(request: Request) -> Scorer | None:
    """A dependency so tests can swap the network out."""
    return scorer_for(request.app.state)


def scorer_for(state: State) -> Scorer | None:
    """The match scorer, or ``None`` when ``OPENAI_API_KEY`` isn't set.

    Built once per app.
    """
    if not state.settings.openai_api_key:
        return None
    if getattr(state, "scorer", None) is None:
        state.scorer = create_openai_scorer(
            state.settings.openai_api_key, state.settings.openai_model
        )
    return state.scorer


def get_mailer(request: Request) -> Mailer | None:
    """A dependency so tests can swap the network out."""
    return mailer_for(request.app.state)


def mailer_for(state: State) -> Mailer | None:
    """The digest mailer, or ``None`` when ``RESEND_API_KEY`` isn't set.

    Built once per app.
    """
    settings = state.settings
    if not settings.resend_api_key or not settings.email_from:
        return None
    if getattr(state, "mailer", None) is None:
        state.mailer = resend_mailer(settings.resend_api_key, settings.email_from)
    return state.mailer


def _pipeline(fetch: Fetcher, scorer: Scorer | None, mailer: Mailer | None) -> Pipeline:
    async def pipeline(session: AsyncSession, run: Run) -> int:
        return await run_pipeline(session, run, fetch, scorer, mailer)

    return pipeline


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["running", "success", "failed"]
    trigger: Literal["cron", "manual"]
    started_at: datetime
    finished_at: datetime | None
    jobs_found: int
    error: str | None


@router.post(
    "",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Account not found"},
        status.HTTP_409_CONFLICT: {"description": "A run is already in progress"},
    },
)
async def create_run(
    request: Request,
    user_id: CurrentUserId,
    fetch: Annotated[Fetcher, Depends(get_fetcher)],
    scorer: Annotated[Scorer | None, Depends(get_scorer)],
    mailer: Annotated[Mailer | None, Depends(get_mailer)],
) -> RunOut:
    """Start a run and return its ``Runs`` row with ``status: "running"``.

    The pipeline goes on in the background and closes the row as
    ``success`` or ``failed``:

    - ``jobs_found`` becomes the number of **new** jobs. They are scored
      against the profile when a scorer is configured.
    - When email is configured, the digest of all open, not-yet-notified
      jobs is sent at the end. A failed send doesn't fail the run.
    - 409 if a run for this user holds the lock; nothing is written.
    """

    pipeline = _pipeline(fetch, scorer, mailer)
    opened: asyncio.Future[Run | None] = asyncio.get_running_loop().create_future()
    # The task owns the lock connection and the row for the whole run, so
    # both are released on every path, even if this request goes away.
    task = asyncio.create_task(
        execute_run(
            request.app.state.engine, user_id, "manual", pipeline, opened.set_result
        )
    )
    tasks: set[asyncio.Task[object]] = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(_finished)
    task.add_done_callback(tasks.discard)

    await asyncio.wait({opened, task}, return_when=asyncio.FIRST_COMPLETED)
    if opened.done():
        return RunOut.model_validate(opened.result())
    try:
        run = task.result()
    except IntegrityError:
        # Only the runs.user_id FK can fail: the token's user was deleted.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Account not found"
        ) from None
    if run is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="A run is already in progress"
        )
    # execute_run only returns a row after opening it.
    raise AssertionError("run closed without opening")  # pragma: no cover


def _finished(task: asyncio.Task[Run | None]) -> None:
    """Log what ``execute_run`` let through, e.g. the DB down at close time.

    Pipeline errors are already on the row; this is the rest.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and not isinstance(exc, IntegrityError):
        logger.error("Background run crashed", exc_info=exc)


async def run_scheduled(
    state: State, user_id: uuid.UUID, fetch: Fetcher = fetch_openings
) -> Run | None:
    """The tick loop's run for a due user, with ``trigger: "cron"``.

    Runs inline, in the tick's task. Returns ``None``, writing nothing, when
    another run (e.g. a manual one) holds the user's lock; the next tick
    re-checks, and that run's ``started_at`` usually makes this one not due.
    """
    pipeline = _pipeline(fetch, scorer_for(state), mailer_for(state))
    run = await execute_run(state.engine, user_id, "cron", pipeline)
    if run is None:
        logger.info("Scheduled run for user %s skipped: a run holds the lock", user_id)
    return run
