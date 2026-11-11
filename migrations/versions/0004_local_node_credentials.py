"""Additive Local Node per-device credentials table with Row-Level Security.

Revision ID: 0004_local_node_credentials
Revises: 0003_workspace_persistence
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from apex_one.db.rls import (
    build_disable_rls_statements,
    build_enable_rls_statements,
)

revision: str = "0004_local_node_credentials"
down_revision: str | None = "0003_workspace_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_node_credentials",
        sa.Column("credential_id", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_local_node_credentials_tenant_id",
        "local_node_credentials",
        ["tenant_id"],
    )
    op.create_index(
        "ix_local_node_credentials_node_id",
        "local_node_credentials",
        ["node_id"],
    )
    op.create_index(
        "ix_local_node_credentials_status",
        "local_node_credentials",
        ["status"],
    )

    for stmt in build_enable_rls_statements(("local_node_credentials",)):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in build_disable_rls_statements(("local_node_credentials",)):
        op.execute(stmt)
    op.drop_index("ix_local_node_credentials_status", table_name="local_node_credentials")
    op.drop_index("ix_local_node_credentials_node_id", table_name="local_node_credentials")
    op.drop_index("ix_local_node_credentials_tenant_id", table_name="local_node_credentials")
    op.drop_table("local_node_credentials")
