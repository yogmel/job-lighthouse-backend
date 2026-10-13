"""run company results new jobs

Revision ID: d5f1a3b79e24
Revises: c4d8e2a65f13
Create Date: 2026-10-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f1a3b79e24"
down_revision: str | Sequence[str] | None = "c4d8e2a65f13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "run_company_results",
        sa.Column("new_jobs", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("run_company_results", "new_jobs")
