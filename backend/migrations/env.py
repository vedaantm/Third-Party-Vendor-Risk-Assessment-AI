# backend/migrations/env.py

"""
Alembic environment for VendorIQ (async, same engine options as the app).

Database URL, in order:
  1. `alembic -x url=<url> ...`      — tests and one-off checks
  2. DATABASE_URL via app.settings   — environment, then backend/.env

Run from backend/:  uv run alembic upgrade head
"""

import asyncio
from logging.config import fileConfig

from alembic import context

import app.db.models  # noqa: F401 — registers every table on Base.metadata
from app.db.database import Base, build_engine

config = context.config

# Tests set skip_logging so Alembic doesn't reconfigure pytest's logging.
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url")
    if url:
        return url
    from app.settings import settings
    return settings.database_url.get_secret_value()


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:
    # SQLite can't ALTER most things in place; batch mode copies the table.
    _configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    # `-x ssl=disable` for a local Postgres; otherwise the DATABASE_SSL setting
    engine = build_engine(_url(), pooled=False, ssl=context.get_x_argument(as_dictionary=True).get("ssl"))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
