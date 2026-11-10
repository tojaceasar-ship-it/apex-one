"""Durable workflow state machine for APEX ONE Research Edition."""

from __future__ import annotations

from enum import StrEnum


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ALLOWED_WORKFLOW_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.PENDING: frozenset(
        {
            WorkflowStatus.RUNNING,
            WorkflowStatus.CANCEL_REQUESTED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.FAILED,
        }
    ),
    WorkflowStatus.RUNNING: frozenset(
        {
            WorkflowStatus.SUCCEEDED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCEL_REQUESTED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.PENDING,  # when recovered from a crashed worker lease
        }
    ),
    WorkflowStatus.CANCEL_REQUESTED: frozenset(
        {
            WorkflowStatus.CANCELLED,
            WorkflowStatus.SUCCEEDED,
            WorkflowStatus.FAILED,
        }
    ),
    WorkflowStatus.SUCCEEDED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}


class InvalidWorkflowTransitionError(ValueError):
    """Raised when attempting an illegal durable workflow state transition."""


def validate_workflow_transition(
    current: WorkflowStatus | str,
    target: WorkflowStatus | str,
) -> WorkflowStatus:
    cur = WorkflowStatus(current)
    tgt = WorkflowStatus(target)
    if cur == tgt:
        return tgt
    if tgt not in ALLOWED_WORKFLOW_TRANSITIONS[cur]:
        raise InvalidWorkflowTransitionError(
            f"Illegal workflow transition from {cur.value} to {tgt.value}."
        )
    return tgt
