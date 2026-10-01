"""Pipeline step 8: the digest email.

- The digest is every job of the user with ``active = true AND notified_at IS
  NULL``, not "jobs from this run". A missed digest heals itself: the next
  one carries its jobs too.
- No email when that set is empty.
- ``notified_at`` is stamped only after the provider confirms the send, and
  only on the jobs that were in it.
- A failed send is logged and leaves ``notified_at`` null. It doesn't fail
  the run: the jobs are already committed.
- Titles, companies, locations and URLs are scraped, untrusted text. HTML is
  escaped, and only ``http(s)`` URLs become links.
"""

import html
import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.email import Email, Mailer

from .models import Job, User

logger = logging.getLogger(__name__)


async def send_digest(session: AsyncSession, user_id: uuid.UUID, mailer: Mailer) -> int:
    """Email ``user_id`` their unnotified open jobs. Returns how many were sent.

    Flushes but doesn't commit.
    """
    jobs = (
        await session.scalars(
            select(Job)
            .where(
                Job.user_id == user_id,
                Job.active.is_(True),
                Job.notified_at.is_(None),
            )
            .order_by(Job.match_score.desc().nulls_last(), Job.date.desc(), Job.id)
        )
    ).all()
    if not jobs:
        return 0
    to = await session.scalar(select(User.email).where(User.id == user_id))
    if to is None:
        return 0
    # Built outside the try: a bug here fails the run visibly. Only the send
    # itself fails softly.
    email = build_digest(to, jobs)
    try:
        await mailer(email)
    except Exception as exc:
        # Type only: the message may echo the recipient.
        logger.warning(
            "Digest send failed for user %s: %s", user_id, type(exc).__name__
        )
        return 0
    # Only the jobs that were in the email. A job added since stays for the
    # next digest.
    await session.execute(
        update(Job)
        .where(Job.id.in_([job.id for job in jobs]), Job.notified_at.is_(None))
        .values(notified_at=func.now())
    )
    return len(jobs)


def build_digest(to: str, jobs: Sequence[Job]) -> Email:
    count = len(jobs)
    subject = f"{count} new job{'s' if count != 1 else ''} on Job Lighthouse"
    text = "\n\n".join(_text_entry(job) for job in jobs)
    items = "\n".join(f"<li>{_html_entry(job)}</li>" for job in jobs)
    return Email(
        to=to,
        subject=subject,
        text=f"{subject}\n\n{text}\n",
        html=f"<p>{html.escape(subject)}</p>\n<ul>\n{items}\n</ul>\n",
    )


def _score(job: Job) -> str:
    return "unscored" if job.match_score is None else f"{job.match_score:.0f}/100"


def _details(job: Job) -> str:
    parts = [job.company, job.location, f"match {_score(job)}"]
    return " · ".join(p for p in parts if p)


def _is_web_url(url: str) -> bool:
    return url.lower().startswith(("https://", "http://"))


def _text_entry(job: Job) -> str:
    return f"{job.title}\n{_details(job)}\n{job.url}"


def _html_entry(job: Job) -> str:
    title = html.escape(job.title)
    if _is_web_url(job.url):
        title = f'<a href="{html.escape(job.url, quote=True)}">{title}</a>'
    return f"{title}<br>{html.escape(_details(job))}"
