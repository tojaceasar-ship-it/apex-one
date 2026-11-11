"""Additive OCC version_num columns and RLS-protected idempotency_records table.

Revision ID: 0005_occ_and_idempotency
Revises: 0004_local_node_credentials
Create Date: 2026-11-11
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

revision: str = "0005_occ_and_idempotency"
down_revision: str | None = "0004_local_node_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_identity_directory",
        sa.Column(
            "version_num",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "organization_workspace_states",
        sa.Column(
            "version_num",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column(
            "response_json",
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_idempotency_records_org_key",
        ),
    )
    op.create_index(
        "ix_idempotency_records_organization_id",
        "idempotency_records",
        ["organization_id"],
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
    )

    for stmt in build_enable_org_rls_statements(("idempotency_records",)):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in build_disable_org_rls_statements(("idempotency_records",)):
        op.execute(stmt)
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_index(
        "ix_idempotency_records_organization_id", table_name="idempotency_records"
    )
    op.drop_table("idempotency_records")
    op.drop_column("organization_workspace_states", "version_num")
    op.drop_column("platform_identity_directory", "version_num")
