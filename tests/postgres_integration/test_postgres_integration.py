"""Suite C — PostgreSQL Integration Tests (require real PostgreSQL + pgvector).

IMPORTANT: These tests are NEVER mocked and NEVER marked PASS without executing
against a real PostgreSQL instance configured via APEX_DATABASE_URL or DATABASE_URL.
"""

from __future__ import annotations

import uuid

import pytest

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.models import DEFAULT_EMBEDDING_DIM, KnowledgeChunk, ResearchProject, Tenant
from apex_one.db.repositories import SqlAlchemyWorkflowStore
from apex_one.db.vector import search_knowledge_chunks
from apex_one.outbox.models import OutboxEnqueueRequest
from apex_one.outbox.queue import PostgresOutboxQueue

pytestmark = pytest.mark.pg_integration


async def test_postgres_health_and_pgvector_extension(
    real_postgres_adapter: PostgresConnectionAdapter,
) -> None:
    report = await real_postgres_adapter.health_check()
    assert report.connected is True
    assert report.server_version is not None
    assert report.pgvector_available is True


async def test_pgvector_insert_and_cosine_similarity_search(
    real_postgres_adapter: PostgresConnectionAdapter,
) -> None:
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()

    async with real_postgres_adapter.session() as session:
        session.add(Tenant(id=tenant_id, slug=f"t-{tenant_id.hex[:8]}", name="Test"))

    vec_target = [1.0] + [0.0] * (DEFAULT_EMBEDDING_DIM - 1)
    vec_ortho = [0.0, 1.0] + [0.0] * (DEFAULT_EMBEDDING_DIM - 2)

    async with real_postgres_adapter.session_with_rls(tenant_id) as session:
        session.add(
            ResearchProject(
                id=project_id,
                tenant_id=tenant_id,
                title="pgvector test",
                hypothesis="Cosine ordering works",
            )
        )
        await session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_uri="doc://1",
                    content="Exact match chunk",
                    embedding=vec_target,
                ),
                KnowledgeChunk(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_uri="doc://2",
                    content="Orthogonal chunk",
                    embedding=vec_ortho,
                ),
            ]
        )
        await session.flush()
        hits = await search_knowledge_chunks(
            session, vec_target, project_id=project_id, limit=2
        )
        assert len(hits) == 2
        assert hits[0].source_uri == "doc://1"
        assert hits[0].cosine_distance < hits[1].cosine_distance


async def test_transactional_workflow_and_skip_locked_outbox(
    real_postgres_adapter: PostgresConnectionAdapter,
) -> None:
    tenant_id = uuid.uuid4()
    store = SqlAlchemyWorkflowStore(real_postgres_adapter)
    wf, created = await store.create_or_get_workflow(
        tenant_id=tenant_id,
        workflow_type="literature_review",
        idempotency_key=f"pg-int-{uuid.uuid4().hex}",
        input_payload={"query": "pgvector"},
    )
    assert created is True

    queue = PostgresOutboxQueue(real_postgres_adapter)
    job_id, _ = await queue.enqueue(
        OutboxEnqueueRequest(
            tenant_id=tenant_id,
            aggregate_type="custom",
            aggregate_id=uuid.UUID(wf["id"]),
            event_type="custom.pg.test",
            idempotency_key=f"outbox-int-{uuid.uuid4().hex}",
        )
    )
    claimed = await queue.claim_batch(worker_id="pg-worker-1", lease_seconds=60, batch_size=10)
    claimed_ids = {c.id for c in claimed}
    assert job_id in claimed_ids
    for c in claimed:
        await queue.mark_succeeded(job_id=c.id, worker_id="pg-worker-1")
