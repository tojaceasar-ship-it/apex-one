"""Background worker runtime supporting retry, idempotency, recovery, heartbeat, and cancellation.

Designed to run without Docker or a permanent OS daemon (`run_once` or `run_loop`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apex_one.outbox.models import (
    ClaimedOutboxJob,
    JobCancelledError,
    NonRetryableJobError,
    OutboxStatus,
    RetryPolicy,
)
from apex_one.outbox.queue import OutboxQueueProtocol


class JobExecutionContext:
    """Context passed to a task handler for cooperative cancellation and heartbeats."""

    def __init__(
        self,
        job: ClaimedOutboxJob,
        queue: OutboxQueueProtocol,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self.job = job
        self._queue = queue
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._cancel_observed = bool(job.cancel_requested)

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_observed

    async def heartbeat_and_check_cancelled(self) -> None:
        """Extend worker lease via heartbeat and raise JobCancelledError if cancelled."""
        if self._cancel_observed:
            raise JobCancelledError(f"Job {self.job.id} cancelled prior to step.")
        ack = await self._queue.heartbeat(
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
            active_job_id=self.job.id,
        )
        if ack.cancel_requested:
            self._cancel_observed = True
            raise JobCancelledError(f"Job {self.job.id} cancelled via cooperative heartbeat.")
        if not ack.lease_extended:
            raise JobCancelledError(
                f"Lease lost for job {self.job.id}; aborting side effects."
            )


TaskHandler = Callable[[JobExecutionContext], Awaitable[dict[str, object] | None]]


@dataclass(frozen=True)
class WorkerTickSummary:
    recovered_stale_jobs: int
    claimed_jobs: int
    succeeded_jobs: int
    retried_jobs: int
    dead_letter_jobs: int
    cancelled_jobs: int
    deduplicated_jobs: int


class WorkerRuntime:
    """Stateless, crash-recoverable worker runtime driven by OutboxQueueProtocol."""

    def __init__(
        self,
        *,
        queue: OutboxQueueProtocol,
        worker_id: str | None = None,
        lease_seconds: int = 60,
        heartbeat_interval_seconds: float = 15.0,
        poll_interval_seconds: float = 2.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._queue = queue
        self.worker_id = worker_id or f"apex-worker-{uuid.uuid4().hex[:10]}"
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._handlers: dict[str, TaskHandler] = {}
        self._executed_idempotency_keys: set[str] = set()
        self._stop_event = asyncio.Event()

    def register_handler(self, event_type: str, handler: TaskHandler) -> None:
        self._handlers[event_type] = handler

    def request_stop(self) -> None:
        self._stop_event.set()

    async def _periodic_heartbeat(
        self,
        ctx: JobExecutionContext,
        done_event: asyncio.Event,
    ) -> None:
        while not done_event.is_set():
            try:
                await asyncio.wait_for(
                    done_event.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
                return
            except TimeoutError:
                ack = await self._queue.heartbeat(
                    worker_id=self.worker_id,
                    lease_seconds=self._lease_seconds,
                    active_job_id=ctx.job.id,
                )
                if ack.cancel_requested:
                    ctx._cancel_observed = True

    async def run_once(self, *, batch_size: int = 5) -> WorkerTickSummary:
        """Run one recovery + claim + dispatch cycle (safe for serverless / ephemeral hosts)."""
        recovered = await self._queue.recover_stale_leases()
        await self._queue.heartbeat(
            worker_id=self.worker_id,
            lease_seconds=self._lease_seconds,
            active_job_id=None,
        )
        claimed = await self._queue.claim_batch(
            worker_id=self.worker_id,
            lease_seconds=self._lease_seconds,
            batch_size=batch_size,
        )

        succeeded = 0
        retried = 0
        dead_letter = 0
        cancelled = 0
        deduplicated = 0

        for job in claimed:
            if job.cancel_requested:
                await self._queue.mark_cancelled(
                    job_id=job.id,
                    worker_id=self.worker_id,
                )
                cancelled += 1
                continue

            # Idempotency guard against duplicate execution
            if (
                job.idempotency_key in self._executed_idempotency_keys
                or await self._queue.is_already_succeeded(job.idempotency_key)
            ):
                await self._queue.mark_succeeded(
                    job_id=job.id,
                    worker_id=self.worker_id,
                )
                deduplicated += 1
                continue

            handler = self._handlers.get(job.event_type)
            if handler is None:
                status = await self._queue.mark_failed(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_message=f"No registered handler for event_type='{job.event_type}'.",
                    retry_policy=self._retry_policy,
                    retryable=False,
                )
                if status == OutboxStatus.DEAD_LETTER:
                    dead_letter += 1
                else:
                    retried += 1
                continue

            ctx = JobExecutionContext(
                job=job,
                queue=self._queue,
                worker_id=self.worker_id,
                lease_seconds=self._lease_seconds,
            )
            done_event = asyncio.Event()
            hb_task = asyncio.create_task(self._periodic_heartbeat(ctx, done_event))
            try:
                await handler(ctx)
                if ctx.cancel_requested:
                    raise JobCancelledError(f"Job {job.id} cancelled before completion.")
                self._executed_idempotency_keys.add(job.idempotency_key)
                await self._queue.mark_succeeded(
                    job_id=job.id,
                    worker_id=self.worker_id,
                )
                succeeded += 1
            except JobCancelledError:
                await self._queue.mark_cancelled(
                    job_id=job.id,
                    worker_id=self.worker_id,
                )
                cancelled += 1
            except NonRetryableJobError as exc:
                await self._queue.mark_failed(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_message=str(exc),
                    retry_policy=self._retry_policy,
                    retryable=False,
                )
                dead_letter += 1
            except Exception as exc:
                next_status = await self._queue.mark_failed(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error_message=f"{type(exc).__name__}: {exc}",
                    retry_policy=self._retry_policy,
                    retryable=True,
                )
                if next_status == OutboxStatus.DEAD_LETTER:
                    dead_letter += 1
                else:
                    retried += 1
            finally:
                done_event.set()
                await hb_task

        return WorkerTickSummary(
            recovered_stale_jobs=recovered,
            claimed_jobs=len(claimed),
            succeeded_jobs=succeeded,
            retried_jobs=retried,
            dead_letter_jobs=dead_letter,
            cancelled_jobs=cancelled,
            deduplicated_jobs=deduplicated,
        )

    async def run_loop(self, *, batch_size: int = 5) -> None:
        """Continuous worker loop runnable via `start_process` without Docker."""
        while not self._stop_event.is_set():
            summary = await self.run_once(batch_size=batch_size)
            if summary.claimed_jobs == 0:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
