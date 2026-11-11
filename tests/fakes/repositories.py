"""In-memory test doubles isolated strictly to `tests/fakes/` (never imported by prod)."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from apex_one.db.base import utcnow
from apex_one.db.vector import VectorSimilarityHit
from apex_one.outbox.models import (
    ClaimedOutboxJob,
    HeartbeatAck,
    OutboxEnqueueRequest,
    OutboxStatus,
    RetryPolicy,
)


class FakeInMemoryOutboxQueue:
    """Test-only implementation of OutboxQueueProtocol for Suite A worker unit tests."""

    def __init__(self) -> None:
        self.events: dict[uuid.UUID, dict[str, Any]] = {}
        self.by_idem: dict[str, uuid.UUID] = {}
        self.heartbeats: dict[str, datetime] = {}

    async def enqueue(self, req: OutboxEnqueueRequest) -> tuple[uuid.UUID, bool]:
        if req.idempotency_key in self.by_idem:
            return self.by_idem[req.idempotency_key], False
        now = utcnow()
        jid = uuid.uuid4()
        self.events[jid] = {
            "id": jid,
            "tenant_id": req.tenant_id,
            "aggregate_type": req.aggregate_type,
            "aggregate_id": req.aggregate_id,
            "event_type": req.event_type,
            "idempotency_key": req.idempotency_key,
            "payload": dict(req.payload),
            "status": OutboxStatus.PENDING,
            "attempts": 0,
            "max_attempts": req.max_attempts,
            "available_at": now,
            "locked_by": None,
            "lease_expires_at": None,
            "recovery_count": 0,
            "cancel_requested": False,
            "last_error": None,
        }
        self.by_idem[req.idempotency_key] = jid
        return jid, True

    async def claim_batch(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        batch_size: int = 1,
        now: datetime | None = None,
    ) -> list[ClaimedOutboxJob]:
        ts = now or utcnow()
        lease_until = ts + timedelta(seconds=lease_seconds)
        claimed: list[ClaimedOutboxJob] = []
        for item in self.events.values():
            if len(claimed) >= batch_size:
                break
            if item["status"] != OutboxStatus.PENDING:
                continue
            if item["cancel_requested"]:
                item["status"] = OutboxStatus.CANCELLED
                continue
            if item["available_at"] > ts:
                continue
            item["status"] = OutboxStatus.PROCESSING
            item["locked_by"] = worker_id
            item["lease_expires_at"] = lease_until
            item["attempts"] += 1
            claimed.append(
                ClaimedOutboxJob(
                    id=item["id"],
                    tenant_id=item["tenant_id"],
                    aggregate_type=item["aggregate_type"],
                    aggregate_id=item["aggregate_id"],
                    event_type=item["event_type"],
                    idempotency_key=item["idempotency_key"],
                    payload=dict(item["payload"]),
                    status=OutboxStatus.PROCESSING,
                    attempts=item["attempts"],
                    max_attempts=item["max_attempts"],
                    locked_by=worker_id,
                    lease_expires_at=lease_until,
                    recovery_count=item["recovery_count"],
                    cancel_requested=item["cancel_requested"],
                )
            )
        return claimed

    async def mark_succeeded(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        item = self.events.get(job_id)
        if not item or item["locked_by"] != worker_id:
            return False
        item["status"] = OutboxStatus.SUCCEEDED
        item["locked_by"] = None
        item["lease_expires_at"] = None
        return True

    async def mark_failed(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        error_message: str,
        retry_policy: RetryPolicy,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> OutboxStatus:
        ts = now or utcnow()
        item = self.events[job_id]
        item["last_error"] = error_message
        item["locked_by"] = None
        item["lease_expires_at"] = None
        if (not retryable) or item["attempts"] >= item["max_attempts"]:
            item["status"] = OutboxStatus.DEAD_LETTER
        else:
            item["status"] = OutboxStatus.PENDING
            item["available_at"] = retry_policy.next_available_at(ts, item["attempts"])
        return item["status"]

    async def mark_cancelled(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        item = self.events.get(job_id)
        if not item:
            return False
        item["status"] = OutboxStatus.CANCELLED
        item["cancel_requested"] = True
        item["locked_by"] = None
        item["lease_expires_at"] = None
        return True

    async def request_cancel(
        self,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        now: datetime | None = None,
    ) -> bool:
        item = self.events.get(job_id)
        if not item or item["tenant_id"] != tenant_id:
            return False
        item["cancel_requested"] = True
        if item["status"] == OutboxStatus.PENDING:
            item["status"] = OutboxStatus.CANCELLED
        return True

    async def heartbeat(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        active_job_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> HeartbeatAck:
        ts = now or utcnow()
        self.heartbeats[worker_id] = ts
        if active_job_id is None:
            return HeartbeatAck(lease_extended=True, cancel_requested=False)
        item = self.events.get(active_job_id)
        if not item or item["locked_by"] != worker_id:
            return HeartbeatAck(
                lease_extended=False,
                cancel_requested=bool(item["cancel_requested"]) if item else False,
            )
        new_exp = ts + timedelta(seconds=lease_seconds)
        item["lease_expires_at"] = new_exp
        return HeartbeatAck(
            lease_extended=True,
            cancel_requested=bool(item["cancel_requested"]),
            new_lease_expires_at=new_exp,
        )

    async def recover_stale_leases(self, *, now: datetime | None = None) -> int:
        ts = now or utcnow()
        recovered = 0
        for item in self.events.values():
            if (
                item["status"] == OutboxStatus.PROCESSING
                and item["lease_expires_at"] is not None
                and item["lease_expires_at"] <= ts
            ):
                item["recovery_count"] += 1
                item["locked_by"] = None
                item["lease_expires_at"] = None
                if item["attempts"] >= item["max_attempts"]:
                    item["status"] = OutboxStatus.DEAD_LETTER
                else:
                    item["status"] = OutboxStatus.PENDING
                    item["available_at"] = ts
                recovered += 1
        return recovered

    async def is_already_succeeded(self, idempotency_key: str) -> bool:
        jid = self.by_idem.get(idempotency_key)
        if jid is None:
            return False
        return self.events[jid]["status"] == OutboxStatus.SUCCEEDED


class FakeWorkflowStore:
    """Test-only WorkflowStoreProtocol double strictly for Suite B API tests."""

    def __init__(self) -> None:
        self._by_id: dict[uuid.UUID, dict[str, Any]] = {}
        self._by_idem: dict[tuple[uuid.UUID, str], uuid.UUID] = {}

    async def create_or_get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_type: str,
        idempotency_key: str,
        input_payload: dict[str, Any],
        max_attempts: int = 5,
    ) -> tuple[dict[str, Any], bool]:
        key = (tenant_id, idempotency_key)
        if key in self._by_idem:
            return dict(self._by_id[self._by_idem[key]]), False
        wid = uuid.uuid4()
        now = utcnow().isoformat()
        rec = {
            "id": str(wid),
            "tenant_id": str(tenant_id),
            "workflow_type": workflow_type,
            "idempotency_key": idempotency_key,
            "status": "PENDING",
            "input_payload": dict(input_payload),
            "checkpoint_state": {},
            "result_payload": None,
            "error_message": None,
            "cancel_requested_at": None,
            "cancelled_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._by_id[wid] = rec
        self._by_idem[key] = wid
        return dict(rec), True

    async def get_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        rec = self._by_id.get(workflow_id)
        if not rec or rec["tenant_id"] != str(tenant_id):
            return None
        return dict(rec)

    async def request_cancel_workflow(
        self,
        *,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        rec = self._by_id.get(workflow_id)
        if not rec or rec["tenant_id"] != str(tenant_id):
            return None
        now = utcnow().isoformat()
        rec["cancel_requested_at"] = now
        rec["status"] = "CANCELLED"
        rec["cancelled_at"] = now
        return dict(rec)


class FakeVectorStore:
    """Test-only KnowledgeVectorStoreProtocol double strictly for Suite B API tests."""

    def __init__(self) -> None:
        self.items: list[tuple[uuid.UUID, list[float], VectorSimilarityHit]] = []

    async def similarity_search(
        self,
        *,
        tenant_id: uuid.UUID,
        query_vector: Sequence[float],
        project_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[VectorSimilarityHit]:
        matches: list[VectorSimilarityHit] = []
        q_norm = math.sqrt(sum(x * x for x in query_vector)) or 1.0
        for item_tid, emb, hit in self.items:
            if item_tid != tenant_id:
                continue
            if project_id is not None and hit.project_id != project_id:
                continue
            e_norm = math.sqrt(sum(x * x for x in emb)) or 1.0
            dot = sum(a * b for a, b in zip(query_vector, emb, strict=False))
            cos_sim = max(-1.0, min(1.0, dot / (q_norm * e_norm)))
            dist = 1.0 - cos_sim
            matches.append(
                replace(hit, cosine_distance=dist, similarity_score=max(0.0, cos_sim))
            )
        matches.sort(key=lambda h: h.cosine_distance)
        return matches[:limit]
