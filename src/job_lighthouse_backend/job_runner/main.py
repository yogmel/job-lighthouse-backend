"""Job Runner Service: /config, /jobs, /runs and the cron tick loop."""

from job_lighthouse_backend.common.app import create_app

app = create_app("Job Runner Service")
