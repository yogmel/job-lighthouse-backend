"""The user's ``Config`` filters, applied to new openings only (BE-063).

A filtered-out opening is not inserted and not scored. Close/reopen and
``jobs_found`` still use the full fetch, so filtering never closes a job.
Changing filters doesn't touch stored jobs.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Config
from .openings import Opening


@dataclass(frozen=True)
class JobFilter:
    """Empty fields keep everything."""

    # Keep if the title contains any of these (substring, case-insensitive).
    include: tuple[str, ...] = ()
    # Drop if the title has any of these as a whole word, case-insensitive.
    exclude: tuple[str, ...] = ()
    # Keep if the opening's location contains this, case-insensitive. An
    # opening with no location, or a remote one, always passes.
    location: str = ""

    def keeps(self, opening: Opening) -> bool:
        title = opening.title.casefold()
        include = [k.strip().casefold() for k in self.include if k.strip()]
        if include and not any(k in title for k in include):
            return False
        if any(_whole_word(k).search(opening.title) for k in self.exclude if k.strip()):
            return False
        return self._location_ok(opening.location)

    def _location_ok(self, location: str) -> bool:
        wanted = self.location.strip().casefold()
        have = location.strip().casefold()
        return not wanted or not have or "remote" in have or wanted in have


# Keeps every opening.
NO_FILTER = JobFilter()


def _whole_word(keyword: str) -> re.Pattern[str]:
    # Lookarounds instead of ``\b`` so keywords ending in a symbol ("c++")
    # still match.
    return re.compile(rf"(?<!\w){re.escape(keyword.strip())}(?!\w)", re.IGNORECASE)


async def load_filter(session: AsyncSession, user_id: uuid.UUID) -> JobFilter:
    """The user's filters, or an empty one when they have no ``Config``."""
    config = await session.scalar(select(Config).where(Config.user_id == user_id))
    if config is None:
        return NO_FILTER
    return JobFilter(
        tuple(config.keywords_include), tuple(config.keywords_exclude), config.location
    )
