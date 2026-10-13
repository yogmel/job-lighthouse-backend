"""FastAPI app factory shared by both services.

Startup reads settings and checks the database. Any failure propagates, so
uvicorn logs it and exits non-zero instead of serving a broken app.

Work that outlives a request goes in ``app.state.background_tasks``, and so
does each ``background`` coroutine started at boot (e.g. the tick loop). Shutdown
cancels those tasks and waits for them before closing the engine, so each one
gets to clean up (e.g. close its ``Runs`` row) while the DB is still there.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import check_connection, create_engine, create_sessionmaker
from .settings import Settings, cors_allowed_origins_from_env

logger = logging.getLogger(__name__)


# Started once the DB is up; gets the app, so it can read ``app.state``.
Background = Callable[[FastAPI], Coroutine[Any, Any, None]]


def create_app(title: str, background: Sequence[Background] = ()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings.from_env()
        engine = create_engine(settings.database_url)
        try:
            await check_connection(engine)
        except Exception:
            # Don't log the URL: it carries the DB password.
            logger.critical("%s: cannot connect to Postgres", title)
            await engine.dispose()
            raise
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        tasks: set[asyncio.Task[object]] = set()
        app.state.background_tasks = tasks
        for start in background:
            started: asyncio.Task[object] = asyncio.create_task(start(app))
            tasks.add(started)
            started.add_done_callback(tasks.discard)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    app = FastAPI(title=title, lifespan=lifespan)
    # Read here, not in lifespan: middleware can't be added once the app starts.
    # CORS is set only here, never in Nginx: a duplicate
    # Access-Control-Allow-Origin header makes the browser reject the response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins_from_env(),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        # GET /jobs paging; the browser hides other custom headers cross-origin.
        expose_headers=["X-Next-Cursor", "X-Total-Count", "X-Tier-Counts"],
        # The frontend sends a Bearer token, not cookies.
        allow_credentials=False,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
