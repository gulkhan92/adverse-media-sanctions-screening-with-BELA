"""Alembic environment. Migrations are written as SQL, so no model metadata is used."""

from __future__ import annotations

from alembic import context
from atlas.config import DATABASE_URL
from sqlalchemy import create_engine

url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
