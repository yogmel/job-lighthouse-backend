"""jobs

Revision ID: f30591071ad7
Revises: b2539b5be6c9
Create Date: 2026-09-30 11:57:56.756714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f30591071ad7'
down_revision: Union[str, Sequence[str], None] = 'b2539b5be6c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # Default NO ACTION (not RESTRICT) so an account delete can cascade
        # to companies and jobs in the same statement.
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id"),
            nullable=False,
            index=True,
        ),
        # Denormalized display name at scrape time; may drift after a rename.
        sa.Column("company", sa.Text(), nullable=False),
        # Scoring fields stay null until the job is scored (v0.5).
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_description", sa.Text(), nullable=True),
        # Config.profile_version at scoring time.
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column(
            "date",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Null = not yet included in a sent digest.
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        # Posting is still open. Not a user-dismiss flag.
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        # Same URL = same job.
        sa.UniqueConstraint("user_id", "url", name="uq_jobs_user_id_url"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("jobs")
