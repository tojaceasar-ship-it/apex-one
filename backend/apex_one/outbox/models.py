"""Domain value objects and retry math for the PostgreSQL Outbox queue."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class NonRetryableJobError(Exception):
    """Raised by handlers when retrying the job would be futile."""


class JobCancelledError(Exception):
    """Raised when cooperative cancellation is observed during job execution."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0
    backoff_multiplier: float = 2.0

    def compute_next_delay_seconds(self, attempt: int) -> float:
        """Compute bounded exponential backoff delay for 1-indexed attempt."""
        if attempt <= 0:
            return self.base_delay_seconds
        raw = self.base_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        return float(min(raw, self.max_delay_seconds))

    def next_available_at(self, now: datetime, attempt: int) -> datetime:
        return now + timedelta(seconds=self.compute_next_delay_seconds(attempt))


@dataclass(frozen=True)
class OutboxEnqueueRequest:
    tenant_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    max_attempts: int = 5


@dataclass(frozen=True)
class ClaimedOutboxJob:
    id: uuid.UUID
    tenant_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    idempotency_key: str
    payload: dict[str, Any]
    status: OutboxStatus
    attempts: int
    max_attempts: int
    locked_by: str
    lease_expires_at: datetime
    recovery_count: int
    cancel_requested: bool


@dataclass(frozen=True)
class HeartbeatAck:
    lease_extended: bool
    cancel_requested: bool
    new_lease_expires_at: datetime | None = None
