"""baseline

Revision ID: c90dba0b15bc
Revises:
Create Date: 2026-09-28 11:56:56.029067

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c90dba0b15bc"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
