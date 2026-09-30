"""FastAPI app factory shared by both services.

Startup reads settings and checks the database. Any failure propagates, so
uvicorn logs it and exits non-zero instead of serving a broken app.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import check_connection, create_engine, create_sessionmaker
from .settings import Settings

logger = logging.getLogger(__name__)


def create_app(title: str) -> FastAPI:
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
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title=title, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
