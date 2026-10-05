"""runs scope and company_id

Revision ID: b7e2f4a18c90
Revises: a3c1d9e47b52
Create Date: 2026-10-05 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2f4a18c90"
down_revision: str | Sequence[str] | None = "a3c1d9e47b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "runs",
        sa.Column("scope", sa.Text(), server_default="all", nullable=False),
    )
    op.add_column("runs", sa.Column("company_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "runs_company_id_fkey",
        "runs",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    """Downgrade schema.

    Single-company runs have no place in the old schema, so they are dropped.
    """
    op.execute("DELETE FROM runs WHERE scope = 'company'")
    op.drop_constraint("runs_company_id_fkey", "runs", type_="foreignkey")
    op.drop_column("runs", "company_id")
    op.drop_column("runs", "scope")
