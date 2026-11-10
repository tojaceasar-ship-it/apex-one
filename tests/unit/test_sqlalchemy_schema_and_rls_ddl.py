"""Suite A — Unit tests for PostgreSQL DDL, pgvector SQL, RLS policies, and Alembic."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from apex_one.db.base import Base
from apex_one.db.models import DEFAULT_EMBEDDING_DIM, RLS_PROTECTED_TABLES
from apex_one.db.rls import build_enable_rls_statements
from apex_one.db.vector import (
    EmbeddingDimensionError,
    build_cosine_similarity_query,
    validate_vector,
)

pytestmark = pytest.mark.unit


def test_all_sqlalchemy_models_compile_to_postgresql_ddl() -> None:
    dialect = postgresql.dialect()
    tables = Base.metadata.tables
    expected = {
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
    assert expected.issubset(set(tables.keys()))

    compiled_chunks = str(CreateTable(tables["knowledge_chunks"]).compile(dialect=dialect))
    assert f"VECTOR({DEFAULT_EMBEDDING_DIM})" in compiled_chunks
    assert "JSONB" in compiled_chunks

    hnsw_indexes = [
        idx
        for idx in tables["knowledge_chunks"].indexes
        if idx.name == "ix_knowledge_chunks_embedding_hnsw"
    ]
    assert len(hnsw_indexes) == 1
    compiled_idx = str(CreateIndex(hnsw_indexes[0]).compile(dialect=dialect))
    assert "USING hnsw" in compiled_idx
    assert "vector_cosine_ops" in compiled_idx


def test_pgvector_cosine_query_and_dimension_validation() -> None:
    with pytest.raises(EmbeddingDimensionError):
        validate_vector([0.1, 0.2, 0.3], expected_dim=DEFAULT_EMBEDDING_DIM)

    with pytest.raises(EmbeddingDimensionError):
        bad = [0.0] * DEFAULT_EMBEDDING_DIM
        bad[0] = float("nan")
        validate_vector(bad, expected_dim=DEFAULT_EMBEDDING_DIM)

    valid_vec = [0.01] * DEFAULT_EMBEDDING_DIM
    stmt = build_cosine_similarity_query(
        valid_vec,
        project_id=uuid.uuid4(),
        limit=5,
        max_cosine_distance=0.4,
    )
    sql_str = str(stmt.compile(dialect=postgresql.dialect()))
    assert "<=>" in sql_str
    assert "LIMIT" in sql_str


def test_rls_ddl_statements_enable_and_force_tenant_isolation() -> None:
    stmts = build_enable_rls_statements(RLS_PROTECTED_TABLES)
    joined = "\n".join(stmts)
    for table in RLS_PROTECTED_TABLES:
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in joined
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in joined
        assert f"CREATE POLICY p_{table}_tenant_isolation" in joined
    assert "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid" in joined


def test_alembic_offline_sql_generation_succeeds_without_live_db() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    alembic_bin = Path(sys.executable).parent / "alembic"
    proc = subprocess.run(
        [str(alembic_bin), "upgrade", "head", "--sql"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    sql = proc.stdout
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql
    assert "CREATE TABLE knowledge_chunks" in sql
    assert "ENABLE ROW LEVEL SECURITY;" in sql
    assert "FORCE ROW LEVEL SECURITY;" in sql
