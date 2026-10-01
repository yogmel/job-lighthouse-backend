"""/runs: trigger a run by hand.

The run happens inline, inside the request, under the same per-user lock and
pipeline a scheduled run will use.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.auth import CurrentUserId

from .company_run import Fetcher, fetch_openings
from .models import Run
from .pipeline import run_pipeline
from .runs import execute_run
from .scoring import Scorer, create_openai_scorer

router = APIRouter(prefix="/runs", tags=["runs"])


def get_fetcher() -> Fetcher:
    """Dependency so tests can swap the network out."""
    return fetch_openings


def get_scorer(request: Request) -> Scorer | None:
    """The match scorer, or ``None`` when ``OPENAI_API_KEY`` isn't set.

    Built once per app. A dependency so tests can swap the network out.
    """
    state = request.app.state
    if not state.settings.openai_api_key:
        return None
    if getattr(state, "scorer", None) is None:
        state.scorer = create_openai_scorer(
            state.settings.openai_api_key, state.settings.openai_model
        )
    return state.scorer


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
    status_code=status.HTTP_201_CREATED,
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
) -> RunOut:
    """Run the pipeline now and return the finished ``Runs`` row.

    - ``jobs_found`` is the number of **new** jobs. They are scored against
      the profile when a scorer is configured.
    - A pipeline error is still a 201, with ``status: "failed"``.
    - 409 if a run for this user holds the lock; nothing is written.
    """

    async def pipeline(session: AsyncSession, run: Run) -> int:
        return await run_pipeline(session, run, fetch, scorer)

    try:
        run = await execute_run(request.app.state.engine, user_id, "manual", pipeline)
    except IntegrityError:
        # Only the runs.user_id FK can fail: the token's user was deleted.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Account not found"
        ) from None
    if run is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="A run is already in progress"
        )
    return RunOut.model_validate(run)
