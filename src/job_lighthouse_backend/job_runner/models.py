"""ORM models for tables owned by the Job Runner Service.

Tables come from the shared Alembic migrations. ``Companies`` is owned by the
Companies Service; the runner only reads it.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from job_lighthouse_backend.common.db import Base

# Registers ``users`` / ``companies`` in the shared metadata so the foreign
# keys below resolve. The runner reads ``Company`` but never writes it.
from job_lighthouse_backend.companies.models import Company, User

__all__ = ["Company", "Config", "Job", "Run", "RunCompanyResult", "User"]


class Config(Base):
    __tablename__ = "config"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    # One config per user.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    keywords_include: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}"
    )
    # Word-boundary matched against titles of new openings (see filters).
    keywords_exclude: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default="{}"
    )
    location: Mapped[str] = mapped_column(Text)
    # Cron expression, read by the runner's tick loop.
    cron: Mapped[str] = mapped_column(Text)
    # Markdown.
    profile: Mapped[str] = mapped_column(Text, server_default="")
    # Bumped on every profile edit. No history of past texts is kept.
    profile_version: Mapped[int] = mapped_column(server_default="1")
    # Digest on/off.
    notify_email: Mapped[bool] = mapped_column(server_default="true")
    # Warn when a company returns nothing. Stored only; nothing sends it yet.
    notify_empty_company: Mapped[bool] = mapped_column(server_default="true")
    # The digest carries only jobs scoring above this (0-100).
    notify_min_score: Mapped[int] = mapped_column(server_default="40")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    # Same URL = same job (unique per user).
    url: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    # Display name at scrape time; may drift after a rename.
    company: Mapped[str] = mapped_column(Text)
    # Null until scored (v0.5).
    match_score: Mapped[float | None]
    match_description: Mapped[str | None] = mapped_column(Text)
    profile_version: Mapped[int | None]
    date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Posting is still open. Not a user-dismiss flag.
    active: Mapped[bool] = mapped_column(server_default="true")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "running" | "success" | "failed"
    status: Mapped[str] = mapped_column(Text)
    # "cron" | "manual"
    trigger: Mapped[str] = mapped_column(Text)
    # "all" | "company": a Full run, or a Single-company run (BE-058)
    scope: Mapped[str] = mapped_column(Text, server_default="all")
    # The Company of a Single-company run; null for a Full run, or once the
    # Company is deleted.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    jobs_found: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class RunCompanyResult(Base):
    __tablename__ = "run_company_results"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    # Null once the Company is deleted; the row stays as run history.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    # Company name when the row was written, so the breakdown outlives the Company.
    company_name: Mapped[str] = mapped_column(Text)
    # "ok" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(Text)
    jobs_found: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
