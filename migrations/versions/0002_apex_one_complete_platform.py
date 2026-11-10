"""Complete APEX ONE 1.0 relational schema, RLS policies, and immutable audit trigger.

Revision ID: 0002_apex_one_complete_platform
Revises: 0001_apex_one_initial
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from apex_one.db import models, platform_models  # noqa: F401
from apex_one.db.base import Base
from apex_one.db.platform_models import ORG_RLS_PROTECTED_TABLES
from apex_one.db.rls import (
    build_audit_immutability_trigger_sql,
    build_disable_org_rls_statements,
    build_enable_org_rls_statements,
)

revision: str = "0002_apex_one_complete_platform"
down_revision: str | None = "0001_apex_one_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATION_0001_TABLES = frozenset(
    {
        "tenants",
        "research_projects",
        "knowledge_chunks",
        "workflow_runs",
        "workflow_steps",
        "outbox_events",
        "worker_heartbeats",
        "sandbox_job_records",
        "local_node_sessions",
    }
)

MIGRATION_0003_TABLES = frozenset(
    {
        "platform_identity_directory",
        "organization_workspace_states",
    }
)


def upgrade() -> None:
    # Add organization_id, workspace_scope, state, version columns to research_projects
    op.add_column(
        "research_projects",
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "research_projects",
        sa.Column(
            "workspace_scope",
            sa.String(length=16),
            nullable=False,
            server_default="research",
        ),
    )
    op.add_column(
        "research_projects",
        sa.Column(
            "state",
            sa.String(length=40),
            nullable=False,
            server_default="CREATED",
        ),
    )
    op.add_column(
        "research_projects",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    # Create all 0002 platform tables in topological FK order using SQLAlchemy DDL compiler
    new_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name not in MIGRATION_0001_TABLES
        and table.name not in MIGRATION_0003_TABLES
    ]
    for table in new_tables:
        op.execute(sa.schema.CreateTable(table))
        for idx in table.indexes:
            op.execute(sa.schema.CreateIndex(idx))

    # Enforce PostgreSQL Row-Level Security on all organization_id-scoped tables
    for stmt in build_enable_org_rls_statements(ORG_RLS_PROTECTED_TABLES):
        op.execute(stmt)

    # Enforce append-only immutability on audit_events
    for stmt in build_audit_immutability_trigger_sql():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_events_mutation()")

    for stmt in build_disable_org_rls_statements(ORG_RLS_PROTECTED_TABLES):
        op.execute(stmt)

    new_tables = [
        table
        for table in reversed(Base.metadata.sorted_tables)
        if table.name not in MIGRATION_0001_TABLES
        and table.name not in MIGRATION_0003_TABLES
    ]
    for table in new_tables:
        op.drop_table(table.name)

    op.drop_column("research_projects", "version")
    op.drop_column("research_projects", "state")
    op.drop_column("research_projects", "workspace_scope")
    op.drop_column("research_projects", "organization_id")
