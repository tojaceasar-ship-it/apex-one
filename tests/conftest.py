"""Pytest configuration and shared fixtures for Suites A, B, C, and D."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from apex_one.config import Settings
from apex_one.db.adapter import PostgresConnectionAdapter


@pytest.fixture
async def real_postgres_adapter() -> AsyncIterator[PostgresConnectionAdapter]:
    """Fixture for Suites C & D requiring a real PostgreSQL connection.

    Never substitutes SQLite or mocks: skips cleanly when unconfigured so C & D
    are never falsely marked PASS.
    """
    settings = Settings()
    if not settings.is_database_configured:
        pytest.skip(
            "Real PostgreSQL required (APEX_DATABASE_URL / DATABASE_URL not configured). "
            "Suites C and D are never mocked or marked PASS without real execution."
        )
    adapter = PostgresConnectionAdapter(settings)
    health = await adapter.health_check()
    if not health.connected:
        await adapter.dispose()
        pytest.fail(f"PostgreSQL configured but unreachable: {health.error}")
    try:
        yield adapter
    finally:
        await adapter.dispose()
