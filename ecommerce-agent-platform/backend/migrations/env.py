from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from shop.config import get_settings
from shop.models import Base  # noqa: F401

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().resolved_database_url


def run() -> None:
    eng = create_engine(_url(), poolclass=pool.NullPool)
    with eng.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run()
