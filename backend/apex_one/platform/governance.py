"""Governance engine: R0-R3 risk gates, parameter-bound approvals, and immutable Audit Log."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from apex_one.db.base import utcnow
from apex_one.platform.security import sha256_hex

RiskLevel = Literal["R0", "R1", "R2", "R3"]

RISK_DESCRIPTIONS: dict[RiskLevel, str] = {
    "R0": "Read-only inspection and retrieval.",
    "R1": "Internal preparation, analysis, and reversible draft creation.",
    "R2": "Controlled modification of organizational state or resource-consuming runs.",
    "R3": "External, high-impact, or potentially irreversible operations requiring exact confirmation.",
}


class ImmutableAuditError(PermissionError):
    """Raised when attempting to modify or delete a protected audit record."""


class GovernanceBlockedError(PermissionError):
    """Raised when Emergency Stop, budget limit, or R2/R3 approval blocks execution."""


def compute_approval_parameter_hash(
    *,
    organization_id: str,
    operation_type: str,
    execution_version: int,
    parameters: dict[str, Any],
) -> str:
    """Bind approval cryptographically to operation, org, execution_version, and exact parameters."""
    canonical = json.dumps(
        {
            "organization_id": organization_id,
            "operation_type": operation_type,
            "execution_version": execution_version,
            "parameters": parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_hex(canonical)


@dataclass(frozen=True)
class AuditEntry:
    id: str
    organization_id: str
    sequence_no: int
    actor_id: str
    actor_type: str
    action: str
    resource_type: str
    resource_id: str
    risk_level: RiskLevel
    details: dict[str, Any]
    prev_hash: str
    entry_hash: str
    created_at: str


class TamperEvidentAuditLedger:
    """Append-only, SHA-256 hash-chained audit log per organization."""

    def __init__(self) -> None:
        self._entries_by_org: dict[str, list[AuditEntry]] = {}

    def append(
        self,
        *,
        organization_id: str,
        actor_id: str,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str,
        risk_level: RiskLevel = "R0",
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        chain = self._entries_by_org.setdefault(organization_id, [])
        seq = len(chain) + 1
        prev_hash = chain[-1].entry_hash if chain else "0" * 64
        created_at = utcnow().isoformat()
        eid = str(uuid.uuid4())
        safe_details = dict(details or {})

        material = json.dumps(
            {
                "id": eid,
                "organization_id": organization_id,
                "sequence_no": seq,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "risk_level": risk_level,
                "details": safe_details,
                "prev_hash": prev_hash,
                "created_at": created_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        entry_hash = sha256_hex(material)
        entry = AuditEntry(
            id=eid,
            organization_id=organization_id,
            sequence_no=seq,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            risk_level=risk_level,
            details=safe_details,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            created_at=created_at,
        )
        chain.append(entry)
        return entry

    def list_entries(self, organization_id: str, *, limit: int = 200) -> list[AuditEntry]:
        chain = self._entries_by_org.get(organization_id, [])
        return list(reversed(chain[-limit:]))

    def get_chronological_entries(self, organization_id: str) -> list[AuditEntry]:
        return list(self._entries_by_org.get(organization_id, []))

    def hydrate_org_entries(
        self,
        organization_id: str,
        raw_entries: list[dict[str, Any]],
    ) -> None:
        ordered = sorted(raw_entries, key=lambda r: int(r["sequence_no"]))
        self._entries_by_org[organization_id] = [
            AuditEntry(
                id=str(r["id"]),
                organization_id=str(r["organization_id"]),
                sequence_no=int(r["sequence_no"]),
                actor_id=str(r["actor_id"]),
                actor_type=str(r["actor_type"]),
                action=str(r["action"]),
                resource_type=str(r["resource_type"]),
                resource_id=str(r["resource_id"]),
                risk_level=r["risk_level"],
                details=dict(r.get("details") or {}),
                prev_hash=str(r["prev_hash"]),
                entry_hash=str(r["entry_hash"]),
                created_at=str(r["created_at"]),
            )
            for r in ordered
        ]

    def modify_entry(self, *_args: object, **_kwargs: object) -> None:
        raise ImmutableAuditError(
            "Audit records are append-only and protected from modification."
        )

    def delete_entry(self, *_args: object, **_kwargs: object) -> None:
        raise ImmutableAuditError(
            "Audit records are append-only and protected from deletion."
        )

    def verify_chain(self, organization_id: str) -> dict[str, object]:
        chain = self._entries_by_org.get(organization_id, [])
        expected_prev = "0" * 64
        for idx, item in enumerate(chain, start=1):
            if item.sequence_no != idx or item.prev_hash != expected_prev:
                return {"valid": False, "entries_checked": idx, "broken_at": idx}
            material = json.dumps(
                {
                    "id": item.id,
                    "organization_id": item.organization_id,
                    "sequence_no": item.sequence_no,
                    "actor_id": item.actor_id,
                    "actor_type": item.actor_type,
                    "action": item.action,
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "risk_level": item.risk_level,
                    "details": item.details,
                    "prev_hash": item.prev_hash,
                    "created_at": item.created_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            if sha256_hex(material) != item.entry_hash:
                return {"valid": False, "entries_checked": idx, "broken_at": idx}
            expected_prev = item.entry_hash
        return {
            "valid": True,
            "entries_checked": len(chain),
            "head_hash": expected_prev,
        }


def default_approval_expiry(hours: int = 24) -> datetime:
    return utcnow() + timedelta(hours=hours)
