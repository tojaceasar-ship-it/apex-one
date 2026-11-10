"""Repository contracts and PostgreSQL SQLAlchemy implementations."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.base import utcnow
from apex_one.db.models import OutboxEvent, WorkflowRun
from apex_one.db.vector import VectorSimilarityHit, search_knowledge_chunks


class WorkflowStoreProtocol(Protocol):
    """Persistence contract for durable workflows + atomic transactional outbox."""

    async def create_or_get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_type: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> tuple[dict[str, Any], bool]: ...

    async def get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None: ...

    async def request_cancel_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None: ...


class KnowledgeVectorStoreProtocol(Protocol):
    """Persistence contract for pgvector knowledge similarity search."""

    async def similarity_search(
        self,
        *,
        tenant_id: uuid.UUID,
        query_vector: Sequence[float],
        project_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[VectorSimilarityHit]: ...


def _workflow_to_dict(row: WorkflowRun) -> dict[str, Any]:
    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "workflow_type": row.workflow_type,
        "idempotency_key": row.idempotency_key,
        "status": row.status,
        "input_payload": dict(row.input_payload or {}),
        "checkpoint_state": dict(row.checkpoint_state or {}),
        "result_payload": dict(row.result_payload) if row.result_payload else None,
        "error_message": row.error_message,
        "cancel_requested_at": _iso(row.cancel_requested_at),
        "cancelled_at": _iso(row.cancelled_at),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class SqlAlchemyWorkflowStore(WorkflowStoreProtocol):
    """Production PostgreSQL workflow + transactional outbox repository."""

    def __init__(self, adapter: PostgresConnectionAdapter) -> None:
        self._adapter = adapter

    async def create_or_get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_type: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> tuple[dict[str, Any], bool]:
        async with self._adapter.session_with_rls(tenant_id) as session:
            existing_stmt = select(WorkflowRun).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                return _workflow_to_dict(existing), False

            now = utcnow()
            run_id = uuid.uuid4()
            workflow = WorkflowRun(
                id=run_id,
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                idempotency_key=idempotency_key,
                status="PENDING",
                input_payload=input_payload,
                checkpoint_state={},
                created_at=now,
                updated_at=now,
            )
            outbox = OutboxEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                aggregate_type="workflow_run",
                aggregate_id=run_id,
                event_type=f"workflow.{workflow_type}.execute",
                idempotency_key=f"wf:{tenant_id}:{idempotency_key}",
                payload={
                    "workflow_id": str(run_id),
                    "tenant_id": str(tenant_id),
                    "workflow_type": workflow_type,
                    "input": input_payload,
                },
                status="PENDING",
                attempts=0,
                max_attempts=max_attempts,
                available_at=now,
                created_at=now,
            )
            session.add(workflow)
            session.add(outbox)
            await session.flush()
            return _workflow_to_dict(workflow), True

    async def get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        async with self._adapter.session_with_rls(tenant_id) as session:
            row = await session.get(WorkflowRun, workflow_id)
            return _workflow_to_dict(row) if row is not None else None

    async def request_cancel_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        async with self._adapter.session_with_rls(tenant_id) as session:
            row = await session.get(WorkflowRun, workflow_id)
            if row is None:
                return None
            now = utcnow()
            if row.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return _workflow_to_dict(row)

            row.cancel_requested_at = now
            if row.status == "PENDING":
                row.status = "CANCELLED"
                row.cancelled_at = now
            else:
                row.status = "CANCEL_REQUESTED"
            row.updated_at = now

            await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == workflow_id,
                    OutboxEvent.status.in_(["PENDING", "PROCESSING"]),
                )
                .values(cancel_requested=True)
            )
            await session.flush()
            return _workflow_to_dict(row)


class SqlAlchemyKnowledgeVectorStore(KnowledgeVectorStoreProtocol):
    """Production pgvector similarity repository scoped by PostgreSQL RLS."""

    def __init__(self, adapter: PostgresConnectionAdapter) -> None:
        self._adapter = adapter

    async def similarity_search(
        self,
        *,
        tenant_id: uuid.UUID,
        query_vector: Sequence[float],
        project_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[VectorSimilarityHit]:
        async with self._adapter.session_with_rls(tenant_id) as session:
            return await search_knowledge_chunks(
                session,
                query_vector,
                project_id=project_id,
                limit=limit,
            )
