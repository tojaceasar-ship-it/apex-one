"""Suite D — PostgreSQL Security Tests (require real PostgreSQL Row-Level Security).

IMPORTANT: Never mocked and never marked PASS without real PostgreSQL RLS execution.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.models import ResearchProject

pytestmark = pytest.mark.pg_security


async def test_rls_strict_cross_tenant_isolation(
    real_postgres_adapter: PostgresConnectionAdapter,
) -> None:
    health = await real_postgres_adapter.health_check()
    if health.is_superuser_or_bypassrls:
        pytest.fail(
            f"Connected PostgreSQL role '{health.current_role}' has SUPERUSER or BYPASSRLS; "
            "production and Suite D require a dedicated NOBYPASSRLS application role."
        )

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    project_id = uuid.uuid4()

    async with real_postgres_adapter.session_with_rls(tenant_a) as session_a:
        session_a.add(
            ResearchProject(
                id=project_id,
                tenant_id=tenant_a,
                title="Confidential Tenant A Research",
            )
        )

    # Tenant B must see 0 rows belonging to Tenant A
    async with real_postgres_adapter.session_with_rls(tenant_b) as session_b:
        leaked = await session_b.get(ResearchProject, project_id)
        assert leaked is None
        rows = (
            await session_b.execute(
                select(ResearchProject).where(ResearchProject.id == project_id)
            )
        ).scalars().all()
        assert rows == []

        # Tenant B attempting to forge tenant_a row must fail WITH CHECK policy
        with pytest.raises(DBAPIError):
            session_b.add(
                ResearchProject(
                    id=uuid.uuid4(),
                    tenant_id=tenant_a,
                    title="Forged Cross-Tenant Insert",
                )
            )
            await session_b.flush()

    # Unscoped session without app.current_tenant_id must see 0 rows cleanly
    async with real_postgres_adapter.session() as unscoped:
        await unscoped.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
        unscoped_rows = (
            await unscoped.execute(
                select(ResearchProject).where(ResearchProject.id == project_id)
            )
        ).scalars().all()
        assert unscoped_rows == []
