"""Alembic environment shared by the Job Runner and Companies services.

The database URL comes only from the ``DATABASE_URL`` env var. It is passed
straight to the engine (not via ``config.set_main_option``) so that ``%`` in a
password doesn't trip configparser interpolation.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from job_lighthouse_backend.common.db import normalize_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models yet; migrations are written by hand. Revisit when the
# services add models (BE-007 / BE-008).
target_metadata = None


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. See README → Local database & migrations."
        )
    # Migrations always run on the sync psycopg (v3) driver.
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (``alembic upgrade --sql``)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
