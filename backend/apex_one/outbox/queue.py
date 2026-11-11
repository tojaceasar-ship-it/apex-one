"""PostgreSQL Outbox queue implementation using `FOR UPDATE SKIP LOCKED`."""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import DateTime, Integer, String, bindparam, select, text, update

from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.base import utcnow
from apex_one.db.models import OutboxEvent, WorkerHeartbeat
from apex_one.db.rls import OUTBOX_DISPATCHER_GUC_NAME
from apex_one.outbox.models import (
    ClaimedOutboxJob,
    HeartbeatAck,
    OutboxEnqueueRequest,
    OutboxStatus,
    RetryPolicy,
)


class OutboxQueueProtocol(Protocol):
    """Persistence-agnostic Outbox interface implemented by PostgresOutboxQueue."""

    async def enqueue(self, req: OutboxEnqueueRequest) -> tuple[uuid.UUID, bool]:
        """Idempotently enqueue an outbox message; returns (job_id, created_new)."""
        ...

    async def claim_batch(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        batch_size: int = 1,
        now: datetime | None = None,
    ) -> list[ClaimedOutboxJob]: ...

    async def mark_succeeded(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool: ...

    async def mark_failed(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        error_message: str,
        retry_policy: RetryPolicy,
        retryable: bool = True,
        now: datetime | None = None,
    ) -> OutboxStatus: ...

    async def mark_cancelled(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> bool: ...

    async def request_cancel(
        self,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        now: datetime | None = None,
    ) -> bool: ...

    async def heartbeat(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        active_job_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> HeartbeatAck: ...

    async def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> int: ...

    async def is_already_succeeded(self, idempotency_key: str) -> bool: ...


CLAIM_BATCH_SQL = text(
    """
    WITH candidate AS (
        SELECT id
        FROM outbox_events
        WHERE (
            (status = 'PENDING' AND available_at <= CAST(:now AS timestamptz) AND cancel_requested = false)
            OR (status = 'PENDING' AND cancel_requested = true)
        )
        ORDER BY available_at ASC, created_at ASC
        LIMIT CAST(:batch_size AS integer)
        FOR UPDATE SKIP LOCKED
    )
    UPDATE outbox_events AS o
    SET status = CASE WHEN o.cancel_requested THEN 'CANCELLED' ELSE 'PROCESSING' END,
        locked_by = CASE WHEN o.cancel_requested THEN CAST(NULL AS varchar) ELSE CAST(:worker_id AS varchar) END,
        lease_expires_at = CASE WHEN o.cancel_requested THEN CAST(NULL AS timestamptz) ELSE CAST(:lease_expires_at AS timestamptz) END,
        processed_at = CASE WHEN o.cancel_requested THEN CAST(:now AS timestamptz) ELSE o.processed_at END,
        attempts = CASE WHEN o.cancel_requested THEN o.attempts ELSE o.attempts + 1 END
    FROM candidate
    WHERE o.id = candidate.id
    RETURNING
        o.id,
        o.tenant_id,
        o.aggregate_type,
        o.aggregate_id,
        o.event_type,
        o.idempotency_key,
        o.payload,
        o.status,
        o.attempts,
        o.max_attempts,
        o.locked_by,
        o.lease_expires_at,
        o.recovery_count,
        o.cancel_requested
    """
).bindparams(
    bindparam("now", type_=DateTime(timezone=True)),
    bindparam("batch_size", type_=Integer()),
    bindparam("worker_id", type_=String()),
    bindparam("lease_expires_at", type_=DateTime(timezone=True)),
)


class PostgresOutboxQueue(OutboxQueueProtocol):
    """Production PostgreSQL Outbox queue backed by `FOR UPDATE SKIP LOCKED`."""

    def __init__(self, adapter: PostgresConnectionAdapter) -> None:
        self._adapter = adapter

    async def _set_dispatcher_context(self, session: Any) -> None:
        await session.execute(
            text("SELECT set_config(:guc, '1', true)"),
            {"guc": OUTBOX_DISPATCHER_GUC_NAME},
        )

    async def enqueue(self, req: OutboxEnqueueRequest) -> tuple[uuid.UUID, bool]:
        async with self._adapter.session_with_rls(req.tenant_id) as session:
            existing = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key == req.idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing.id, False

            now = utcnow()
            event = OutboxEvent(
                id=uuid.uuid4(),
                tenant_id=req.tenant_id,
                aggregate_type=req.aggregate_type,
                aggregate_id=req.aggregate_id,
                event_type=req.event_type,
                idempotency_key=req.idempotency_key,
                payload=dict(req.payload),
                status=OutboxStatus.PENDING.value,
                attempts=0,
                max_attempts=req.max_attempts,
                available_at=now,
                created_at=now,
            )
            session.add(event)
            await session.flush()
            return event.id, True

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

        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            rows = (
                await session.execute(
                    CLAIM_BATCH_SQL,
                    {
                        "now": ts,
                        "batch_size": batch_size,
                        "worker_id": worker_id,
                        "lease_expires_at": lease_until,
                    },
                )
            ).mappings().all()

            claimed: list[ClaimedOutboxJob] = []
            for row in rows:
                if row["status"] == OutboxStatus.CANCELLED.value:
                    continue
                claimed.append(
                    ClaimedOutboxJob(
                        id=row["id"],
                        tenant_id=row["tenant_id"],
                        aggregate_type=row["aggregate_type"],
                        aggregate_id=row["aggregate_id"],
                        event_type=row["event_type"],
                        idempotency_key=row["idempotency_key"],
                        payload=dict(row["payload"] or {}),
                        status=OutboxStatus(row["status"]),
                        attempts=int(row["attempts"]),
                        max_attempts=int(row["max_attempts"]),
                        locked_by=str(row["locked_by"]),
                        lease_expires_at=row["lease_expires_at"],
                        recovery_count=int(row["recovery_count"]),
                        cancel_requested=bool(row["cancel_requested"]),
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
        ts = now or utcnow()
        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            res = await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == job_id,
                    OutboxEvent.locked_by == worker_id,
                    OutboxEvent.status == OutboxStatus.PROCESSING.value,
                )
                .values(
                    status=OutboxStatus.SUCCEEDED.value,
                    locked_by=None,
                    lease_expires_at=None,
                    processed_at=ts,
                )
            )
            return int(res.rowcount or 0) > 0

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
        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            row = await session.get(OutboxEvent, job_id)
            if row is None:
                return OutboxStatus.DEAD_LETTER

            exhausted = (not retryable) or (row.attempts >= row.max_attempts)
            if exhausted:
                next_status = OutboxStatus.DEAD_LETTER
                row.status = next_status.value
                row.processed_at = ts
            else:
                next_status = OutboxStatus.PENDING
                row.status = next_status.value
                row.available_at = retry_policy.next_available_at(ts, row.attempts)

            row.locked_by = None
            row.lease_expires_at = None
            row.last_error = error_message[:4000]
            await session.flush()
            return next_status

    async def mark_cancelled(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        ts = now or utcnow()
        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            stmt = update(OutboxEvent).where(OutboxEvent.id == job_id)
            if worker_id is not None:
                stmt = stmt.where(OutboxEvent.locked_by == worker_id)
            res = await session.execute(
                stmt.values(
                    status=OutboxStatus.CANCELLED.value,
                    cancel_requested=True,
                    locked_by=None,
                    lease_expires_at=None,
                    processed_at=ts,
                )
            )
            return int(res.rowcount or 0) > 0

    async def request_cancel(
        self,
        *,
        job_id: uuid.UUID,
        tenant_id: uuid.UUID,
        now: datetime | None = None,
    ) -> bool:
        ts = now or utcnow()
        async with self._adapter.session_with_rls(tenant_id) as session:
            row = await session.get(OutboxEvent, job_id)
            if row is None:
                return False
            if row.status in {
                OutboxStatus.SUCCEEDED.value,
                OutboxStatus.DEAD_LETTER.value,
                OutboxStatus.CANCELLED.value,
            }:
                return False
            row.cancel_requested = True
            if row.status == OutboxStatus.PENDING.value:
                row.status = OutboxStatus.CANCELLED.value
                row.processed_at = ts
            await session.flush()
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
        new_expiry = ts + timedelta(seconds=lease_seconds)

        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            hb = await session.get(WorkerHeartbeat, worker_id)
            if hb is None:
                hb = WorkerHeartbeat(
                    worker_id=worker_id,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    active_job_id=active_job_id,
                    status="BUSY" if active_job_id else "IDLE",
                    last_heartbeat_at=ts,
                    metadata_json={},
                )
                session.add(hb)
            else:
                hb.active_job_id = active_job_id
                hb.status = "BUSY" if active_job_id else "IDLE"
                hb.last_heartbeat_at = ts

            if active_job_id is None:
                return HeartbeatAck(
                    lease_extended=True,
                    cancel_requested=False,
                    new_lease_expires_at=None,
                )

            job = await session.get(OutboxEvent, active_job_id)
            if (
                job is None
                or job.status != OutboxStatus.PROCESSING.value
                or job.locked_by != worker_id
            ):
                return HeartbeatAck(
                    lease_extended=False,
                    cancel_requested=bool(job.cancel_requested) if job else False,
                    new_lease_expires_at=None,
                )

            job.lease_expires_at = new_expiry
            await session.flush()
            return HeartbeatAck(
                lease_extended=True,
                cancel_requested=bool(job.cancel_requested),
                new_lease_expires_at=new_expiry,
            )

    async def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        ts = now or utcnow()
        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            stale_rows = (
                await session.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_expires_at.is_not(None),
                        OutboxEvent.lease_expires_at <= ts,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            recovered = 0
            for row in stale_rows:
                row.recovery_count += 1
                row.locked_by = None
                row.lease_expires_at = None
                if row.cancel_requested:
                    row.status = OutboxStatus.CANCELLED.value
                    row.processed_at = ts
                elif row.attempts >= row.max_attempts:
                    row.status = OutboxStatus.DEAD_LETTER.value
                    row.last_error = "Stale lease recovered after max_attempts exhausted."
                    row.processed_at = ts
                else:
                    row.status = OutboxStatus.PENDING.value
                    row.available_at = ts
                recovered += 1

            await session.flush()
            return recovered

    async def is_already_succeeded(self, idempotency_key: str) -> bool:
        async with self._adapter.session() as session:
            await self._set_dispatcher_context(session)
            row = (
                await session.execute(
                    select(OutboxEvent.status).where(
                        OutboxEvent.idempotency_key == idempotency_key
                    )
                )
            ).first()
            return bool(row and row[0] == OutboxStatus.SUCCEEDED.value)
