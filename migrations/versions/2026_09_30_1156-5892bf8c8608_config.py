"""config

Revision ID: 5892bf8c8608
Revises: b584fdfc1cbb
Create Date: 2026-09-30 11:56:37.077415

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5892bf8c8608"
down_revision: str | Sequence[str] | None = "b584fdfc1cbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "config",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # One config per user.
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "keywords_include",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        # Word-boundary matched, applied at scrape time.
        sa.Column(
            "keywords_exclude",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("location", sa.Text(), nullable=False),
        # Cron expression, read by the runner's tick loop.
        sa.Column("cron", sa.Text(), nullable=False),
        # Markdown.
        sa.Column("profile", sa.Text(), nullable=False, server_default=""),
        # Bumped on every profile edit.
        sa.Column(
            "profile_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("config")
