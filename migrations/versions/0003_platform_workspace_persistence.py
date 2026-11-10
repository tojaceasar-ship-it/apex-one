"""Durable PostgreSQL workspace & identity synchronization tables with RLS.

Revision ID: 0003_platform_workspace_persistence
Revises: 0002_apex_one_complete_platform
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from apex_one.db.rls import (
    build_disable_org_rls_statements,
    build_enable_org_rls_statements,
)

revision: str = "0003_platform_workspace_persistence"
down_revision: str | None = "0002_apex_one_complete_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_identity_directory",
        sa.Column("singleton_key", sa.String(length=48), primary_key=True, nullable=False),
        sa.Column(
            "identity_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "organization_workspace_states",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    for stmt in build_enable_org_rls_statements(("organization_workspace_states",)):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in build_disable_org_rls_statements(("organization_workspace_states",)):
        op.execute(stmt)
    op.drop_table("organization_workspace_states")
    op.drop_table("platform_identity_directory")
