"""Job Runner Service: /config, /jobs, /runs and the cron tick loop."""

from functools import partial

from fastapi import FastAPI

from job_lighthouse_backend.common.app import create_app

from . import config, jobs, runs_api
from .runs_api import run_scheduled
from .scheduler import tick_loop


async def _scheduler(app: FastAPI) -> None:
    if app.state.settings.scheduler_enabled:
        # Same pipeline and lock as POST /runs.
        await tick_loop(app.state.engine, partial(run_scheduled, app.state))


app = create_app("Job Runner Service", background=[_scheduler])
app.include_router(config.router)
app.include_router(jobs.router)
app.include_router(runs_api.router)
