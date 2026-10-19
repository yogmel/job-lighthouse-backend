"""BE-068: the migration turns the old ``location`` string into a list."""

import importlib.util
from pathlib import Path

import psycopg
import pytest

from .conftest import needs_db

pytestmark = needs_db

_PATH = next(
    (Path(__file__).parents[1] / "migrations" / "versions").glob(
        "*_config_locations.py"
    )
)


def _split_sql() -> str:
    spec = importlib.util.spec_from_file_location("config_locations", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LOCATIONS_FROM_LOCATION


@pytest.mark.parametrize(
    ("location", "locations"),
    [
        ("Berlin, Remote, Germany", ["Berlin", "Remote", "Germany"]),
        ("Berlin, REMOTE", ["Berlin", "REMOTE"]),
        # Remote used to pass on its own; keep that for existing users.
        ("Berlin", ["Berlin", "Remote"]),
        (" , Berlin ,, ", ["Berlin", "Remote"]),
        ("", []),
        (" , ", []),
    ],
)
def test_split_location(db: psycopg.Connection, location, locations):
    row = db.execute(
        f"SELECT {_split_sql()} FROM (VALUES (%s)) AS t(location)",  # noqa: S608
        (location,),
    ).fetchone()
    assert row == (locations,)
