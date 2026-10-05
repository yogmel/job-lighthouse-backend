"""run company results keep the company name

Revision ID: a3c1d9e47b52
Revises: 7f318dc494ff
Create Date: 2026-10-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c1d9e47b52"
down_revision: str | Sequence[str] | None = "7f318dc494ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "run_company_results_company_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("run_company_results", sa.Column("company_name", sa.Text()))
    op.execute(
        "UPDATE run_company_results r SET company_name = c.name"
        " FROM companies c WHERE c.id = r.company_id"
    )
    op.alter_column("run_company_results", "company_name", nullable=False)

    op.drop_constraint(FK, "run_company_results", type_="foreignkey")
    op.alter_column("run_company_results", "company_id", nullable=True)
    op.create_foreign_key(
        FK,
        "run_company_results",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    """Downgrade schema.

    Rows whose Company is gone can't satisfy NOT NULL, so they are dropped.
    """
    op.execute("DELETE FROM run_company_results WHERE company_id IS NULL")
    op.drop_constraint(FK, "run_company_results", type_="foreignkey")
    op.alter_column("run_company_results", "company_id", nullable=False)
    op.create_foreign_key(
        FK,
        "run_company_results",
        "companies",
        ["company_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_column("run_company_results", "company_name")
