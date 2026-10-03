"""``custom`` sources: per-company fetch functions shipped in code (BE-040).

For companies that never fit a board API or CSS selectors. Each handler is
registered in ``HANDLERS`` under the name its ``CustomSource.handler`` stores.

A handler reports its own outcome:

- Returns the openings (empty included) = success. Missing jobs get closed.
- Raises ``FetchError`` = failure. Nothing is closed.

A name with no handler yet raises ``NoHandlerError``: the company is
``skipped`` until a developer ships one.
"""

from collections.abc import Callable, Mapping

from job_lighthouse_backend.companies.sources import CustomSource

from .openings import Opening

# Synchronous, like the other fetchers; runs in a worker thread.
Handler = Callable[[], list[Opening]]

# Handler name -> function. Add one entry per company handled in code.
HANDLERS: dict[str, Handler] = {}


class NoHandlerError(Exception):
    """No handler is registered under the source's name yet."""


def fetch_custom(
    source: CustomSource, handlers: Mapping[str, Handler] = HANDLERS
) -> list[Opening]:
    """Run the handler named by ``source.handler``."""
    handler = handlers.get(source.handler)
    if handler is None:
        raise NoHandlerError(f"no handler named {source.handler!r} yet")
    return handler()
