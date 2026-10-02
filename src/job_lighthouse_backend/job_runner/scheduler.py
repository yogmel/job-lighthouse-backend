"""The cron tick loop: once a minute, find the users whose run is due.

Nothing is registered at boot. Every tick reads ``Config.cron`` fresh, so a
``PUT /config`` takes effect on the next tick with no restart.

A user is due when the first cron fire time **after** their last run's
``started_at`` (any trigger, any status) is at or before now. So:

- after downtime, a missed schedule runs once, not once per missed fire
- a manual run after a fire time counts as that fire's run
- a user with no runs yet waits for the first fire after signup

Cron expressions are standard 5-field ones, evaluated in UTC.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from croniter import croniter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from job_lighthouse_backend.common.db import create_sessionmaker

from .models import Config, Run, User

logger = logging.getLogger(__name__)

TICK_SECONDS = 60

# Called for each due user, one at a time, in the tick's own task.
OnDue = Callable[[uuid.UUID], Awaitable[None]]


class InvalidCron(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    user_id: uuid.UUID
    cron: str
    # Last run's ``started_at``, or signup time if the user never ran.
    last_started_at: datetime


def next_fire(cron: str, after: datetime) -> datetime:
    """First fire time of ``cron`` strictly after ``after``, in UTC."""
    # croniter also takes a 6th (seconds) field; the tick only has minutes.
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise InvalidCron(cron)
    return croniter(cron, after.astimezone(UTC)).get_next(datetime)


def is_due(cron: str, last_started_at: datetime, now: datetime) -> bool:
    return next_fire(cron, last_started_at) <= now


async def load_schedules(
    session: AsyncSession, user_ids: Sequence[uuid.UUID] | None = None
) -> list[Schedule]:
    """Every user with a ``Config`` row, or only ``user_ids``."""
    last_run = (
        select(func.max(Run.started_at))
        .where(Run.user_id == Config.user_id)
        .scalar_subquery()
    )
    stmt = select(
        Config.user_id,
        Config.cron,
        func.coalesce(last_run, User.created_at),
    ).join(User, User.id == Config.user_id)
    if user_ids is not None:
        stmt = stmt.where(Config.user_id.in_(user_ids))
    rows = (await session.execute(stmt.order_by(Config.user_id))).all()
    return [Schedule(user_id, cron, last) for user_id, cron, last in rows]


def _due(schedule: Schedule, now: datetime) -> bool:
    try:
        return is_due(schedule.cron, schedule.last_started_at, now)
    except InvalidCron:
        # PUT /config accepts any non-empty string. Skip, don't stop the tick.
        logger.warning("Invalid cron for user %s; skipped", schedule.user_id)
        return False


async def tick(engine: AsyncEngine, now: datetime, on_due: OnDue) -> None:
    """Call ``on_due`` for every user due at ``now``.

    Users are handled one at a time. Each is re-checked right before
    ``on_due``: an earlier user's run can take minutes, and a manual run
    started meanwhile makes the scheduled one unnecessary.
    One user's failure is logged and doesn't stop the others.
    """
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as session:
        due = [s.user_id for s in await load_schedules(session) if _due(s, now)]
    for user_id in due:
        try:
            async with sessionmaker() as session:
                fresh = await load_schedules(session, [user_id])
            if fresh and _due(fresh[0], now):
                await on_due(user_id)
        except Exception:
            logger.exception("Scheduled run for user %s failed", user_id)


def _seconds_to_next_tick(now: datetime) -> float:
    start = now.replace(second=0, microsecond=0) + timedelta(seconds=TICK_SECONDS)
    return (start - now).total_seconds()


async def tick_loop(engine: AsyncEngine, on_due: OnDue) -> None:
    """Tick at the start of every minute, until cancelled.

    A tick that takes longer than a minute delays the next one; skipped
    minutes are caught up by the due rule.
    """
    while True:
        await asyncio.sleep(_seconds_to_next_tick(datetime.now(UTC)))
        try:
            await tick(engine, datetime.now(UTC), on_due)
        except Exception:
            # E.g. the DB is briefly down. Try again next minute.
            logger.exception("Tick failed")
