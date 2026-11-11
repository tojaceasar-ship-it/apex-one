"""Strict local permission guard for APEX HYBRID Local Node (Part III.14).

Enforces separate explicit capability grants for `read`, `write`, and `execute`,
confines filesystem operations to administrator-selected directories, and forbids
unrestricted remote shell/terminal execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

LocalOperation = Literal["read", "write", "execute"]

ALLOWED_STRUCTURED_TASK_TYPES = frozenset(
    {
        "dataset.summarize_csv_or_json",
        "ollama.local_infer",
        "experiment.evaluate_metrics",
    }
)


class LocalPermissionDeniedError(PermissionError):
    """Raised when a Local Node task exceeds granted read/write/execute capabilities."""


@dataclass(frozen=True)
class LocalCapabilityGrants:
    can_read: bool = True
    can_write: bool = False
    can_execute: bool = False
    allowed_directories: list[Path] = field(default_factory=list)
    allowed_task_types: frozenset[str] = ALLOWED_STRUCTURED_TASK_TYPES

    def check_operation(self, op: LocalOperation) -> None:
        if op == "read" and not self.can_read:
            raise LocalPermissionDeniedError("Local READ capability is not granted.")
        if op == "write" and not self.can_write:
            raise LocalPermissionDeniedError("Local WRITE capability is not granted.")
        if op == "execute" and not self.can_execute:
            raise LocalPermissionDeniedError("Local EXECUTE capability is not granted.")

    def resolve_permitted_path(self, target_path: str | Path, op: LocalOperation) -> Path:
        self.check_operation(op)
        raw = str(target_path)
        if not raw or "\x00" in raw:
            raise LocalPermissionDeniedError("Invalid path.")
        if not self.allowed_directories:
            raise LocalPermissionDeniedError("No allowed local directories configured.")

        resolved = Path(raw).resolve()
        for allowed_dir in self.allowed_directories:
            root = allowed_dir.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise LocalPermissionDeniedError(
            f"Path '{target_path}' is outside all selected Local Node directories."
        )

    def validate_task_execution(self, task_type: str) -> None:
        """Forbid unrestricted remote terminal/shell commands; allow only typed tasks."""
        self.check_operation("execute")
        lowered = task_type.strip().lower()
        if lowered in {"shell", "bash", "sh", "cmd", "powershell", "raw_terminal"}:
            raise LocalPermissionDeniedError(
                "Unrestricted remote terminal execution is permanently forbidden."
            )
        if task_type not in self.allowed_task_types:
            raise LocalPermissionDeniedError(
                f"Task type '{task_type}' is not in permitted Local Node task catalog."
            )
