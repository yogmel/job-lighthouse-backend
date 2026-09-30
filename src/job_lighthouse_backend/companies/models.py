"""ORM models for tables owned by the Companies Service."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from job_lighthouse_backend.common.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(Text)
    # Null when the account is Google-only.
    password_hash: Mapped[str | None] = mapped_column(Text)
    google_id: Mapped[str | None] = mapped_column(Text, unique=True)
    # Tracked, not enforced.
    email_verified: Mapped[bool] = mapped_column(server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
