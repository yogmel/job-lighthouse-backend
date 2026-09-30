"""Run async DB code from sync tests, on a fresh engine and event loop."""

import asyncio
import os
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.db import create_engine, create_sessionmaker


def in_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Call ``fn`` with a session, commit, and return its result."""

    async def main() -> T:
        engine = create_engine(os.environ["DATABASE_URL"])
        try:
            async with create_sessionmaker(engine)() as session:
                result = await fn(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(main())
