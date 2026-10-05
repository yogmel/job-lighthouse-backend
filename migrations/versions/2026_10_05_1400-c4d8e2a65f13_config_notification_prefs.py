"""config notification preferences

Revision ID: c4d8e2a65f13
Revises: b7e2f4a18c90
Create Date: 2026-10-05 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e2a65f13"
down_revision: str | Sequence[str] | None = "b7e2f4a18c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "config",
        sa.Column("notify_email", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "config",
        sa.Column(
            "notify_empty_company",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "config",
        sa.Column(
            "notify_min_score", sa.Integer(), server_default="40", nullable=False
        ),
    )
    op.create_check_constraint(
        "config_notify_min_score_range",
        "config",
        "notify_min_score BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("config_notify_min_score_range", "config", type_="check")
    op.drop_column("config", "notify_min_score")
    op.drop_column("config", "notify_empty_company")
    op.drop_column("config", "notify_email")
