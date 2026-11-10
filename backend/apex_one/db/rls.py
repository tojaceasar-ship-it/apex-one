"""PostgreSQL Row-Level Security (RLS) policy builders and session scoping."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apex_one.db.models import RLS_PROTECTED_TABLES

TENANT_GUC_NAME = "app.current_tenant_id"
ORG_GUC_NAME = "app.current_organization_id"
ACTOR_GUC_NAME = "app.current_actor_id"
OUTBOX_DISPATCHER_GUC_NAME = "app.outbox_dispatcher"


def build_enable_org_rls_statements(
    table_names: tuple[str, ...],
    *,
    schema: str | None = None,
) -> list[str]:
    """Generate PostgreSQL DDL to enable and force strict organization_id RLS."""
    statements: list[str] = []
    org_expr = (
        f"organization_id = COALESCE("
        f"NULLIF(current_setting('{ORG_GUC_NAME}', true), ''), "
        f"NULLIF(current_setting('{TENANT_GUC_NAME}', true), '')"
        f")::uuid"
    )
    for table in table_names:
        qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
        policy_name = f"p_{table}_org_isolation"
        statements.extend(
            [
                f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS {policy_name} ON {qualified}",
                (
                    f"CREATE POLICY {policy_name} ON {qualified} "
                    f"AS PERMISSIVE FOR ALL "
                    f"USING ({org_expr}) "
                    f"WITH CHECK ({org_expr})"
                ),
            ]
        )
    return statements


def build_disable_org_rls_statements(
    table_names: tuple[str, ...],
    *,
    schema: str | None = None,
) -> list[str]:
    statements: list[str] = []
    for table in table_names:
        qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
        policy_name = f"p_{table}_org_isolation"
        statements.extend(
            [
                f"DROP POLICY IF EXISTS {policy_name} ON {qualified}",
                f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY",
                f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY",
            ]
        )
    return statements


def build_audit_immutability_trigger_sql(*, schema: str | None = None) -> list[str]:
    """Protect `audit_events` from ordinary UPDATE and DELETE modifications."""
    qualified = f'"{schema}"."audit_events"' if schema else '"audit_events"'
    fn_name = (
        f'"{schema}".prevent_audit_events_mutation'
        if schema
        else "prevent_audit_events_mutation"
    )
    return [
        (
            f"CREATE OR REPLACE FUNCTION {fn_name}() RETURNS trigger AS $$ "
            "BEGIN "
            "RAISE EXCEPTION 'audit_events records are append-only and cannot be modified or deleted'; "
            "END; $$ LANGUAGE plpgsql"
        ),
        f"DROP TRIGGER IF EXISTS trg_audit_events_immutable ON {qualified}",
        (
            f"CREATE TRIGGER trg_audit_events_immutable "
            f"BEFORE UPDATE OR DELETE ON {qualified} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn_name}()"
        ),
    ]


def build_enable_rls_statements(
    table_names: tuple[str, ...] = RLS_PROTECTED_TABLES,
    *,
    schema: str | None = None,
) -> list[str]:
    """Generate PostgreSQL DDL to enable and force strict tenant RLS on tables."""
    statements: list[str] = []
    for table in table_names:
        qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
        policy_name = f"p_{table}_tenant_isolation"
        tenant_expr = (
            f"tenant_id = NULLIF(current_setting('{TENANT_GUC_NAME}', true), '')::uuid"
        )
        if table == "outbox_events":
            # Outbox claim dispatcher may claim across tenants inside a scoped
            # dispatcher transaction, then executes handlers under strict tenant RLS.
            policy_expr = (
                f"({tenant_expr} OR "
                f"NULLIF(current_setting('{OUTBOX_DISPATCHER_GUC_NAME}', true), '') = '1')"
            )
        else:
            policy_expr = tenant_expr
        statements.extend(
            [
                f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS {policy_name} ON {qualified}",
                (
                    f"CREATE POLICY {policy_name} ON {qualified} "
                    f"AS PERMISSIVE FOR ALL "
                    f"USING ({policy_expr}) "
                    f"WITH CHECK ({policy_expr})"
                ),
            ]
        )
    return statements


def build_disable_rls_statements(
    table_names: tuple[str, ...] = RLS_PROTECTED_TABLES,
    *,
    schema: str | None = None,
) -> list[str]:
    """Generate PostgreSQL DDL to drop RLS policies during migration downgrade."""
    statements: list[str] = []
    for table in table_names:
        qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
        policy_name = f"p_{table}_tenant_isolation"
        statements.extend(
            [
                f"DROP POLICY IF EXISTS {policy_name} ON {qualified}",
                f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY",
                f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY",
            ]
        )
    return statements


async def set_rls_tenant_context(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    actor_id: str | None = None,
) -> None:
    """Bind tenant_id to the current PostgreSQL transaction via `set_config(..., true)`."""
    if not isinstance(tenant_id, uuid.UUID):
        tenant_id = uuid.UUID(str(tenant_id))

    await session.execute(
        text("SELECT set_config(:guc, :val, true)"),
        {"guc": TENANT_GUC_NAME, "val": str(tenant_id)},
    )
    await session.execute(
        text("SELECT set_config(:guc, :val, true)"),
        {"guc": ORG_GUC_NAME, "val": str(tenant_id)},
    )
    if actor_id is not None:
        await session.execute(
            text("SELECT set_config(:guc, :val, true)"),
            {"guc": ACTOR_GUC_NAME, "val": str(actor_id)},
        )
