"""runs and run company results

Revision ID: 7f318dc494ff
Revises: f30591071ad7
Create Date: 2026-09-30 11:58:30.434422

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f318dc494ff"
down_revision: str | Sequence[str] | None = "f30591071ad7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "runs",
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
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "jobs_found",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('cron', 'manual')",
            name="ck_runs_trigger",
        ),
    )
    # The scheduler reads the latest run per user.
    op.create_index(
        "ix_runs_user_id_started_at",
        "runs",
        ["user_id", sa.text("started_at DESC")],
    )

    op.create_table(
        "run_company_results",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Deferred so an account delete can cascade to companies and results
        # (checked at commit), same as jobs.company_id.
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "jobs_found",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('ok', 'failed', 'skipped')",
            name="ck_run_company_results_status",
        ),
        # Exactly one result per company per run.
        sa.UniqueConstraint(
            "run_id",
            "company_id",
            name="uq_run_company_results_run_id_company_id",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("run_company_results")
    op.drop_index("ix_runs_user_id_started_at", table_name="runs")
    op.drop_table("runs")
