"""Suite A — Unit tests for Outbox retry, idempotency, recovery, heartbeat, and cancellation."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from apex_one.db.base import utcnow
from apex_one.outbox.models import (
    NonRetryableJobError,
    OutboxEnqueueRequest,
    OutboxStatus,
    RetryPolicy,
)
from apex_one.worker.runtime import JobExecutionContext, WorkerRuntime
from apex_one.workflows.engine import (
    InvalidWorkflowTransitionError,
    WorkflowStatus,
    validate_workflow_transition,
)
from tests.fakes.repositories import FakeInMemoryOutboxQueue

pytestmark = pytest.mark.unit


def test_workflow_state_machine_transitions() -> None:
    assert (
        validate_workflow_transition(WorkflowStatus.PENDING, WorkflowStatus.RUNNING)
        == WorkflowStatus.RUNNING
    )
    assert (
        validate_workflow_transition(WorkflowStatus.RUNNING, WorkflowStatus.SUCCEEDED)
        == WorkflowStatus.SUCCEEDED
    )
    with pytest.raises(InvalidWorkflowTransitionError):
        validate_workflow_transition(WorkflowStatus.SUCCEEDED, WorkflowStatus.RUNNING)


def test_exponential_backoff_retry_policy() -> None:
    policy = RetryPolicy(
        max_attempts=5, base_delay_seconds=2.0, max_delay_seconds=10.0
    )
    assert policy.compute_next_delay_seconds(1) == 2.0
    assert policy.compute_next_delay_seconds(2) == 4.0
    assert policy.compute_next_delay_seconds(3) == 8.0
    assert policy.compute_next_delay_seconds(4) == 10.0  # capped


async def test_worker_idempotency_and_success() -> None:
    queue = FakeInMemoryOutboxQueue()
    tenant_id = uuid.uuid4()
    req = OutboxEnqueueRequest(
        tenant_id=tenant_id,
        aggregate_type="workflow_run",
        aggregate_id=uuid.uuid4(),
        event_type="workflow.test.run",
        idempotency_key="idem-key-001",
        payload={"step": 1},
    )
    jid1, created1 = await queue.enqueue(req)
    jid2, created2 = await queue.enqueue(req)
    assert created1 is True
    assert created2 is False
    assert jid1 == jid2

    call_count = 0

    async def handler(ctx: JobExecutionContext) -> dict[str, object]:
        nonlocal call_count
        await ctx.heartbeat_and_check_cancelled()
        call_count += 1
        return {"ok": True}

    worker = WorkerRuntime(queue=queue, worker_id="w-1", lease_seconds=30)
    worker.register_handler("workflow.test.run", handler)

    summary = await worker.run_once()
    assert summary.claimed_jobs == 1
    assert summary.succeeded_jobs == 1
    assert call_count == 1
    assert queue.events[jid1]["status"] == OutboxStatus.SUCCEEDED


async def test_worker_retries_transient_errors_and_dead_letters_exhausted() -> None:
    queue = FakeInMemoryOutboxQueue()
    jid, _ = await queue.enqueue(
        OutboxEnqueueRequest(
            tenant_id=uuid.uuid4(),
            aggregate_type="workflow_run",
            aggregate_id=uuid.uuid4(),
            event_type="workflow.flaky",
            idempotency_key="idem-flaky",
            max_attempts=2,
        )
    )

    async def failing_handler(_ctx: JobExecutionContext) -> dict[str, object]:
        raise RuntimeError("transient network hiccup")

    worker = WorkerRuntime(
        queue=queue,
        worker_id="w-retry",
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.01),
    )
    worker.register_handler("workflow.flaky", failing_handler)

    # 1st attempt -> retried (PENDING)
    s1 = await worker.run_once()
    assert s1.retried_jobs == 1
    assert queue.events[jid]["status"] == OutboxStatus.PENDING

    # Force available_at to now for immediate 2nd attempt
    queue.events[jid]["available_at"] = utcnow() - timedelta(seconds=1)
    s2 = await worker.run_once()
    assert s2.dead_letter_jobs == 1
    assert queue.events[jid]["status"] == OutboxStatus.DEAD_LETTER


async def test_non_retryable_error_immediately_dead_letters() -> None:
    queue = FakeInMemoryOutboxQueue()
    jid, _ = await queue.enqueue(
        OutboxEnqueueRequest(
            tenant_id=uuid.uuid4(),
            aggregate_type="workflow_run",
            aggregate_id=uuid.uuid4(),
            event_type="workflow.invalid",
            idempotency_key="idem-nonretry",
            max_attempts=5,
        )
    )

    async def bad_payload_handler(_ctx: JobExecutionContext) -> dict[str, object]:
        raise NonRetryableJobError("schema invalid")

    worker = WorkerRuntime(queue=queue, worker_id="w-nr")
    worker.register_handler("workflow.invalid", bad_payload_handler)

    s = await worker.run_once()
    assert s.dead_letter_jobs == 1
    assert queue.events[jid]["status"] == OutboxStatus.DEAD_LETTER


async def test_stale_lease_recovery_after_worker_crash() -> None:
    queue = FakeInMemoryOutboxQueue()
    jid, _ = await queue.enqueue(
        OutboxEnqueueRequest(
            tenant_id=uuid.uuid4(),
            aggregate_type="workflow_run",
            aggregate_id=uuid.uuid4(),
            event_type="workflow.recover",
            idempotency_key="idem-recover",
            max_attempts=3,
        )
    )
    # Simulate crashed worker holding an expired lease
    past = utcnow() - timedelta(seconds=120)
    queue.events[jid]["available_at"] = past
    await queue.claim_batch(worker_id="crashed-worker", lease_seconds=30, now=past)
    assert queue.events[jid]["status"] == OutboxStatus.PROCESSING

    worker2 = WorkerRuntime(queue=queue, worker_id="healthy-worker")

    async def ok_handler(_ctx: JobExecutionContext) -> dict[str, object]:
        return {"recovered": True}

    worker2.register_handler("workflow.recover", ok_handler)
    summary = await worker2.run_once()
    assert summary.recovered_stale_jobs == 1
    assert summary.succeeded_jobs == 1
    assert queue.events[jid]["recovery_count"] == 1
    assert queue.events[jid]["status"] == OutboxStatus.SUCCEEDED


async def test_cooperative_cancellation_via_heartbeat() -> None:
    queue = FakeInMemoryOutboxQueue()
    tenant_id = uuid.uuid4()
    jid, _ = await queue.enqueue(
        OutboxEnqueueRequest(
            tenant_id=tenant_id,
            aggregate_type="workflow_run",
            aggregate_id=uuid.uuid4(),
            event_type="workflow.cancellable",
            idempotency_key="idem-cancel",
        )
    )

    async def cancelling_handler(ctx: JobExecutionContext) -> dict[str, object]:
        # Simulate user requesting cancel mid-execution
        await queue.request_cancel(job_id=jid, tenant_id=tenant_id)
        await ctx.heartbeat_and_check_cancelled()
        return {"should_not": "reach"}

    worker = WorkerRuntime(queue=queue, worker_id="w-cancel")
    worker.register_handler("workflow.cancellable", cancelling_handler)
    summary = await worker.run_once()
    assert summary.cancelled_jobs == 1
    assert queue.events[jid]["status"] == OutboxStatus.CANCELLED
