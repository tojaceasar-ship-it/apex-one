"""Alembic migration environment for APEX ONE Research Edition."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from apex_one.config import get_settings, normalize_psycopg_dsn
from apex_one.db import models, platform_models  # noqa: F401 — ensure all models register metadata
from apex_one.db.adapter import DatabaseNotConfiguredError
from apex_one.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit PostgreSQL DDL in '--sql' mode without requiring live credentials."""
    settings = get_settings()
    if settings.effective_database_secret is not None:
        url = normalize_psycopg_dsn(
            settings.effective_database_secret.get_secret_value(),
            force_ssl=settings.db_ssl_required,
        )
    else:
        url = "postgresql+psycopg://offline-compile-only/apex_one"

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Execute migrations against a real PostgreSQL instance."""
    settings = get_settings()
    if settings.effective_database_secret is None:
        raise DatabaseNotConfiguredError(
            "Cannot run online Alembic migrations: set APEX_DATABASE_URL or DATABASE_URL."
        )

    dsn = normalize_psycopg_dsn(
        settings.effective_database_secret.get_secret_value(),
        force_ssl=settings.db_ssl_required,
    )
    target_schema = config.attributes.get("target_schema")

    connectable = create_engine(dsn, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if target_schema:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{target_schema}"'))
            connection.execute(text(f'SET search_path TO "{target_schema}", public'))
            connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=target_schema,
            include_schemas=bool(target_schema),
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
