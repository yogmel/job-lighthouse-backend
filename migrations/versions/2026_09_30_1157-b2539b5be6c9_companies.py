"""companies

Revision ID: b2539b5be6c9
Revises: 5892bf8c8608
Create Date: 2026-09-30 11:57:28.105247

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2539b5be6c9'
down_revision: Union[str, Sequence[str], None] = '5892bf8c8608'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "companies",
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
            index=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # Hand-set from the dashboard; no derivation.
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Marketing site; careers_url lives inside source.selectors.
        sa.Column("website_url", sa.Text(), nullable=False),
        # False = stop tracking; row and jobs are kept.
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        # Discriminated union on `kind` (board | scraper | custom),
        # validated in the app layer.
        sa.Column("source", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("companies")
