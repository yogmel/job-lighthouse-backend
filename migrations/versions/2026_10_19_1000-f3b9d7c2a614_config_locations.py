"""config locations list

Revision ID: f3b9d7c2a614
Revises: e8a2c6d41b57
Create Date: 2026-10-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3b9d7c2a614"
down_revision: str | Sequence[str] | None = "e8a2c6d41b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# "Berlin, Remote, Germany" -> {Berlin,Remote,Germany}; blank entries dropped.
_SPLIT = (
    "ARRAY(SELECT btrim(part)"
    " FROM unnest(string_to_array(location, ',')) WITH ORDINALITY AS t(part, n)"
    " WHERE btrim(part) <> '' ORDER BY n)"
)
# The old filter let every remote opening through. Add "Remote" to a non-empty
# list that lacks it, so existing users keep getting the roles they got.
LOCATIONS_FROM_LOCATION = (
    f"CASE WHEN {_SPLIT} = '{{}}'"  # noqa: S608 -- constants only
    f" OR EXISTS (SELECT 1 FROM unnest({_SPLIT}) AS l WHERE lower(l) = 'remote')"
    f" THEN {_SPLIT} ELSE {_SPLIT} || '{{Remote}}'::text[] END"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "config",
        sa.Column(
            "locations",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.execute(
        f"UPDATE config SET locations = {LOCATIONS_FROM_LOCATION}"  # noqa: S608
    )
    op.drop_column("config", "location")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "config",
        sa.Column("location", sa.Text(), server_default="", nullable=False),
    )
    op.execute("UPDATE config SET location = array_to_string(locations, ', ')")
    op.alter_column("config", "location", server_default=None)
    op.drop_column("config", "locations")
