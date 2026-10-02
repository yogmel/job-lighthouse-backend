"""Job Runner Service: /config, /jobs, /runs and the cron tick loop."""

import logging
import uuid

from fastapi import FastAPI

from job_lighthouse_backend.common.app import create_app

from . import config, jobs, runs_api
from .scheduler import tick_loop

logger = logging.getLogger(__name__)


async def _run_due(user_id: uuid.UUID) -> None:
    # BE-032 starts the run here, under the same lock as POST /runs.
    logger.info("Scheduled run due for user %s", user_id)


async def _scheduler(app: FastAPI) -> None:
    if app.state.settings.scheduler_enabled:
        await tick_loop(app.state.engine, _run_due)


app = create_app("Job Runner Service", background=[_scheduler])
app.include_router(config.router)
app.include_router(jobs.router)
app.include_router(runs_api.router)
