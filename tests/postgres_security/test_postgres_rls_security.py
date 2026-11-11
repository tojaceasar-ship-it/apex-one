"""Suite D — PostgreSQL Security Tests (require real PostgreSQL Row-Level Security).

IMPORTANT: Never mocked and never marked PASS without real PostgreSQL RLS execution.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.models import ResearchProject
from apex_one.db.platform_models import AuditEventRecord, Organization

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
        async with real_postgres_adapter.session_with_rls(tenant_b) as forged_session:
            forged_session.add(
                ResearchProject(
                    id=uuid.uuid4(),
                    tenant_id=tenant_a,
                    title="Forged Cross-Tenant Insert",
                )
            )
            await forged_session.flush()

    # Unscoped session without app.current_tenant_id must see 0 rows cleanly
    async with real_postgres_adapter.session() as unscoped:
        await unscoped.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
        unscoped_rows = (
            await unscoped.execute(
                select(ResearchProject).where(ResearchProject.id == project_id)
            )
        ).scalars().all()
        assert unscoped_rows == []


async def test_org_rls_and_immutable_audit_trigger(
    real_postgres_adapter: PostgresConnectionAdapter,
) -> None:
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    audit_id = uuid.uuid4()

    async with real_postgres_adapter.session() as sys_session:
        sys_session.add_all(
            [
                Organization(id=org_a, slug=f"org-a-{org_a.hex[:8]}", name="Org A"),
                Organization(id=org_b, slug=f"org-b-{org_b.hex[:8]}", name="Org B"),
            ]
        )

    async with real_postgres_adapter.session_with_rls(org_a) as session_a:
        session_a.add(
            AuditEventRecord(
                id=audit_id,
                organization_id=org_a,
                sequence_no=1,
                actor_id="usr-ci-1",
                actor_type="user",
                action="security.rls.verified",
                resource_type="organization",
                resource_id=str(org_a),
                risk_level="R1",
                prev_hash="0" * 64,
                entry_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
        )

    # Organization B cannot read Organization A's audit event
    async with real_postgres_adapter.session_with_rls(org_b) as session_b:
        leaked = await session_b.get(AuditEventRecord, audit_id)
        assert leaked is None

    # Even Organization A cannot DELETE its own append-only audit event (blocked by trigger)
    with pytest.raises(DBAPIError):
        async with real_postgres_adapter.session_with_rls(org_a) as tamper_session:
            await tamper_session.execute(
                delete(AuditEventRecord).where(AuditEventRecord.id == audit_id)
            )
