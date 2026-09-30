"""Job Runner Service: /config, /jobs, /runs and the cron tick loop."""

from job_lighthouse_backend.common.app import create_app

from . import runs_api

app = create_app("Job Runner Service")
app.include_router(runs_api.router)
