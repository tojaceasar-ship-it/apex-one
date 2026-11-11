"""APEX ONE HARDENING 1.0 — Stage 8 (P2): Optimistic Concurrency, Idempotency & Atomic Checkpoints.

Provides minimal, surgical concurrency & persistence primitives:
1. `StaleWriteConflictError` (`409 STALE_WRITE_CONFLICT`):
   - Enforces optimistic concurrency control (OCC) via monotonically increasing integer
     `version` / `version_num` counters (and HTTP `If-Match` / `X-Expected-Version` headers).
2. `IdempotencyConflictError` (`409 IDEMPOTENCY_KEY_CONFLICT`) & `RequestIdempotencyLedger`:
   - Deduplicates mutating requests per `(organization_id, scope, idempotency_key)` with
     canonical SHA-256 payload binding, rejecting payload mismatches or concurrent duplicate
     in-flight executions without double-running side effects.
3. `atomic_write_json_checkpoint` & `atomic_read_json_checkpoint`:
   - Writes checkpoints to unique per-invocation temp files (`pid + uuid`), verifies
     canonical SHA-256 integrity envelopes, `fsync`s file descriptors, and atomically
     swaps via `os.replace` so crashes or concurrent writers never leave torn JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class StaleWriteConflictError(RuntimeError):
    """Raised when an optimistic concurrency version check (`expected_version`) fails."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        *,
        expected_version: int,
        actual_version: int | None,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Stale write rejected for {resource_type} '{resource_id}': "
            f"expected version {expected_version}, authoritative version is {actual_version}."
        )


class IdempotencyConflictError(RuntimeError):
    """Raised when an `Idempotency-Key` is reused with a different payload or while in-flight."""


def parse_expected_version_header(raw_header: str | None) -> int | None:
    """Parse HTTP `If-Match` (e.g. `W/\"3\"`, `\"3\"`, `3`) or `X-Expected-Version` into an integer."""
    if raw_header is None:
        return None
    cleaned = raw_header.strip()
    if not cleaned or cleaned == "*":
        return None
    if cleaned.startswith("W/"):
        cleaned = cleaned[2:].strip()
    cleaned = cleaned.strip('"').strip("'").strip()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    try:
        val = int(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Invalid expected version header '{raw_header}'; must be a positive integer."
        ) from exc
    if val < 1:
        raise ValueError("Expected version must be >= 1.")
    return val


def check_and_bump_version(
    entity: dict[str, Any],
    *,
    resource_type: str,
    resource_id: str,
    expected_version: int | None = None,
) -> int:
    """Validate `expected_version` against `entity['version']` and atomically bump by +1."""
    current_ver = int(entity.get("version", 1))
    if expected_version is not None and int(expected_version) != current_ver:
        raise StaleWriteConflictError(
            resource_type,
            resource_id,
            expected_version=int(expected_version),
            actual_version=current_ver,
        )
    next_ver = current_ver + 1
    entity["version"] = next_ver
    return next_ver


def compute_request_payload_hash(
    *, method: str, path: str, body_bytes: bytes
) -> str:
    """Compute canonical SHA-256 fingerprint of `(HTTP method, path, body_bytes)`."""
    h = hashlib.sha256()
    h.update(method.upper().encode("utf-8"))
    h.update(b"\x00")
    h.update(path.encode("utf-8"))
    h.update(b"\x00")
    h.update(body_bytes)
    return h.hexdigest()


@dataclass
class IdempotencyEntry:
    organization_id: str
    idempotency_key: str
    request_hash: str
    status: str  # "IN_PROGRESS" | "COMPLETED"
    status_code: int | None = None
    response_json: Any = None
    created_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "status": self.status,
            "status_code": self.status_code,
            "response_json": self.response_json,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class RequestIdempotencyLedger:
    """Thread-safe tenant-scoped idempotency coordinator for mutating operations."""

    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], IdempotencyEntry] = {}

    def export_org_entries(self, organization_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                e.to_dict()
                for (oid, _), e in self._entries.items()
                if oid == organization_id and e.status == "COMPLETED"
            ]

    def hydrate_org_entries(
        self, organization_id: str, rows: list[dict[str, Any]]
    ) -> None:
        with self._lock:
            for r in rows:
                key = str(r.get("idempotency_key") or "")
                if not key:
                    continue
                self._entries[(organization_id, key)] = IdempotencyEntry(
                    organization_id=organization_id,
                    idempotency_key=key,
                    request_hash=str(r.get("request_hash") or ""),
                    status=str(r.get("status") or "COMPLETED"),
                    status_code=int(r["status_code"])
                    if r.get("status_code") is not None
                    else 200,
                    response_json=r.get("response_json"),
                    created_at=str(r.get("created_at") or ""),
                    expires_at=str(r.get("expires_at") or ""),
                )

    def begin_or_replay(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[str, IdempotencyEntry]:
        """Return `('EXECUTE', entry)` for new keys or `('REPLAY', entry)` for completed matches.

        Raises `IdempotencyConflictError` if the key is reused with a different payload hash
        or if an identical request is currently `IN_PROGRESS` concurrently.
        """
        norm_key = idempotency_key.strip()
        if not norm_key or len(norm_key) > 200:
            raise ValueError("Idempotency-Key must be between 1 and 200 characters.")

        now = datetime.now(UTC)
        composite = (organization_id, norm_key)
        with self._lock:
            existing = self._entries.get(composite)
            if existing is not None and existing.expires_at:
                try:
                    exp_dt = datetime.fromisoformat(existing.expires_at)
                    if exp_dt <= now:
                        del self._entries[composite]
                        existing = None
                except ValueError:
                    pass

            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(
                        f"Idempotency-Key '{norm_key}' was already used with a different request payload."
                    )
                if existing.status == "IN_PROGRESS":
                    raise IdempotencyConflictError(
                        f"Request with Idempotency-Key '{norm_key}' is already in progress."
                    )
                return "REPLAY", existing

            entry = IdempotencyEntry(
                organization_id=organization_id,
                idempotency_key=norm_key,
                request_hash=request_hash,
                status="IN_PROGRESS",
                created_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self._ttl_seconds)).isoformat(),
            )
            self._entries[composite] = entry
            return "EXECUTE", entry

    def complete(
        self,
        *,
        organization_id: str,
        idempotency_key: str,
        status_code: int,
        response_json: Any,
    ) -> None:
        composite = (organization_id, idempotency_key.strip())
        with self._lock:
            entry = self._entries.get(composite)
            if entry is not None:
                entry.status = "COMPLETED"
                entry.status_code = int(status_code)
                entry.response_json = response_json

    def abort(self, *, organization_id: str, idempotency_key: str) -> None:
        """Release an in-flight reservation when execution fails before committing."""
        composite = (organization_id, idempotency_key.strip())
        with self._lock:
            entry = self._entries.get(composite)
            if entry is not None and entry.status == "IN_PROGRESS":
                del self._entries[composite]


def atomic_write_json_checkpoint(file_path: Path, data: dict[str, Any]) -> str:
    """Atomically write JSON checkpoint with unique temp file, `fsync`, and SHA-256 integrity envelope."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_data = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    digest = hashlib.sha256(serialized_data.encode("utf-8")).hexdigest()
    envelope = {
        "_checkpoint_schema_version": 2,
        "_checkpoint_sha256": digest,
        "_written_at": datetime.now(UTC).isoformat(),
        "data": data,
    }
    encoded_bytes = json.dumps(envelope, ensure_ascii=False, default=str).encode(
        "utf-8"
    )
    tmp_name = f".{file_path.name}.{os.getpid()}.{uuid.uuid4().hex[:10]}.tmp"
    tmp_path = file_path.parent / tmp_name
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, encoded_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(file_path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return digest


def atomic_read_json_checkpoint(file_path: Path) -> dict[str, Any] | None:
    """Read and verify an atomic JSON checkpoint (supports v2 integrity envelope + legacy v1 JSON)."""
    if not file_path.exists():
        return None
    try:
        raw_text = file_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(parsed, dict):
        return None

    if parsed.get("_checkpoint_schema_version") == 2:
        inner = parsed.get("data")
        expected_sha = parsed.get("_checkpoint_sha256")
        if not isinstance(inner, dict) or not isinstance(expected_sha, str):
            return None
        canonical = json.dumps(
            inner,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        actual_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual_sha != expected_sha:
            return None
        return inner

    return parsed
