"""config timezone

Revision ID: e8a2c6d41b57
Revises: d5f1a3b79e24
Create Date: 2026-10-13 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a2c6d41b57"
down_revision: str | Sequence[str] | None = "d5f1a3b79e24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "config",
        sa.Column("timezone", sa.Text(), server_default="UTC", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("config", "timezone")
