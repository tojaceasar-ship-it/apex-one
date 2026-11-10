"""PostgreSQL connection adapter for APEX ONE Research Edition.

Manages SQLAlchemy AsyncEngine (asyncpg), connection pooling / serverless NullPool,
RLS transaction context binding, health checks, and pgvector capability probes.
Never falls back to SQLite or JSON storage.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from apex_one.config import (
    Settings,
    normalize_asyncpg_dsn,
    redact_dsn,
)
from apex_one.db.rls import set_rls_tenant_context


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when PostgreSQL credentials are absent (no silent SQLite fallback)."""


@dataclass(frozen=True)
class DatabaseHealthReport:
    configured: bool
    connected: bool
    dsn_redacted: str
    server_version: str | None = None
    pgvector_available: bool = False
    pgvector_installed: bool = False
    pgvector_version: str | None = None
    current_role: str | None = None
    is_superuser_or_bypassrls: bool | None = None
    error: str | None = None


class PostgresConnectionAdapter:
    """Lifecycle manager for PostgreSQL async engines and RLS-scoped sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def is_configured(self) -> bool:
        return self._settings.is_database_configured

    @property
    def redacted_dsn(self) -> str:
        secret = self._settings.effective_database_secret
        return redact_dsn(secret.get_secret_value() if secret else None)

    def get_engine(self) -> AsyncEngine:
        if not self.is_configured or self._settings.effective_database_secret is None:
            raise DatabaseNotConfiguredError(
                "PostgreSQL is not configured. Set APEX_DATABASE_URL or DATABASE_URL "
                "(e.g., Neon PostgreSQL 16+ with pgvector). SQLite/JSON substitutes are disabled."
            )
        if self._engine is None:
            raw_dsn = self._settings.effective_database_secret.get_secret_value()
            async_dsn, connect_args = normalize_asyncpg_dsn(
                raw_dsn,
                force_ssl=self._settings.db_ssl_required,
            )
            engine_kwargs: dict[str, object] = {
                "echo": self._settings.db_echo_sql,
                "pool_pre_ping": True,
                "connect_args": connect_args,
            }
            if self._settings.db_pool_mode == "nullpool":
                engine_kwargs["poolclass"] = NullPool
            else:
                engine_kwargs["pool_size"] = self._settings.db_pool_size
                engine_kwargs["max_overflow"] = self._settings.db_max_overflow

            self._engine = create_async_engine(async_dsn, **engine_kwargs)
            self._session_factory = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._engine

    def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        self.get_engine()
        assert self._session_factory is not None
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Open a standard transactional session (for non-tenant tables or system tasks)."""
        factory = self.get_session_factory()
        async with factory() as session:
            async with session.begin():
                yield session

    @asynccontextmanager
    async def session_with_rls(
        self,
        tenant_id: uuid.UUID,
        *,
        actor_id: str | None = None,
    ) -> AsyncIterator[AsyncSession]:
        """Open a transaction with PostgreSQL RLS tenant context enforced (`SET LOCAL`)."""
        factory = self.get_session_factory()
        async with factory() as session:
            async with session.begin():
                await set_rls_tenant_context(session, tenant_id, actor_id=actor_id)
                yield session

    async def health_check(self) -> DatabaseHealthReport:
        """Probe PostgreSQL connectivity, server version, role flags, and pgvector status."""
        if not self.is_configured:
            return DatabaseHealthReport(
                configured=False,
                connected=False,
                dsn_redacted=self.redacted_dsn,
                error="PostgreSQL credentials not configured (APEX_DATABASE_URL / DATABASE_URL unset).",
            )

        try:
            engine = self.get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                ver_row = await conn.execute(text("SHOW server_version"))
                server_version = str(ver_row.scalar_one())

                role_row = await conn.execute(
                    text(
                        "SELECT current_user, "
                        "(rolsuper OR rolbypassrls) AS bypasses_rls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
                role_data = role_row.first()
                current_role = str(role_data[0]) if role_data else None
                bypasses_rls = bool(role_data[1]) if role_data else None

                avail_row = await conn.execute(
                    text(
                        "SELECT default_version FROM pg_available_extensions "
                        "WHERE name = 'vector'"
                    )
                )
                pgvector_available = avail_row.first() is not None

                inst_row = await conn.execute(
                    text(
                        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                    )
                )
                inst_first = inst_row.first()
                pgvector_installed = inst_first is not None
                pgvector_version = str(inst_first[0]) if inst_first else None

                return DatabaseHealthReport(
                    configured=True,
                    connected=True,
                    dsn_redacted=self.redacted_dsn,
                    server_version=server_version,
                    pgvector_available=pgvector_available,
                    pgvector_installed=pgvector_installed,
                    pgvector_version=pgvector_version,
                    current_role=current_role,
                    is_superuser_or_bypassrls=bypasses_rls,
                )
        except Exception as exc:
            # Never leak raw connection strings in error messages
            msg = str(exc)
            secret = self._settings.effective_database_secret
            if secret:
                raw_val = secret.get_secret_value()
                if raw_val and raw_val in msg:
                    msg = msg.replace(raw_val, "<REDACTED_DSN>")
            return DatabaseHealthReport(
                configured=True,
                connected=False,
                dsn_redacted=self.redacted_dsn,
                error=f"{type(exc).__name__}: {msg}",
            )

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
