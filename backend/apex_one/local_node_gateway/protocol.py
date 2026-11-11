"""Cloud Gateway protocol verifier, per-device credential store, and session registry for Hybrid Local Nodes."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

from apex_one.db.base import utcnow

SUPPORTED_PROTOCOL_VERSION = "1.0"
DEFAULT_DEV_NODE_SECRET = "dev-local-node-protocol-test-secret-32b"
CredentialStatus = Literal["ACTIVE", "REVOKED", "ROTATED"]

_NODE_SECRET_PATTERN = re.compile(
    r"(apxnd_[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,}|dev-local-node-protocol-test-secret-32b)",
    re.IGNORECASE,
)


def shannon_entropy_bits(value: str) -> float:
    """Compute total Shannon entropy in bits across characters of `value`."""
    if not value:
        return 0.0
    length = len(value)
    counts = Counter(value)
    per_char = -sum((cnt / length) * math.log2(cnt / length) for cnt in counts.values())
    return per_char * length


def validate_local_node_secret(
    secret: str | None,
    *,
    env: str = "development",
) -> str:
    """Validate Local Node HMAC secret; fail fast in staging/production on missing, default, or low-entropy values."""
    normalized_env = env.strip().lower()
    is_strict = normalized_env in {"staging", "production"}

    if secret is None or not secret.strip():
        if is_strict:
            raise ValueError(
                f"Environment '{normalized_env}' requires APEX_LOCAL_NODE_SHARED_SECRET (missing secret fail-fast)."
            )
        return DEFAULT_DEV_NODE_SECRET

    cleaned = secret.strip()
    if is_strict:
        if cleaned == DEFAULT_DEV_NODE_SECRET:
            raise ValueError(
                f"Environment '{normalized_env}' strictly forbids DEFAULT_DEV_NODE_SECRET."
            )
        if len(cleaned) < 32:
            raise ValueError(
                f"Environment '{normalized_env}' requires APEX_LOCAL_NODE_SHARED_SECRET of at least 32 characters."
            )
        if len(set(cleaned)) < 10 or shannon_entropy_bits(cleaned) < 96.0:
            raise ValueError(
                f"Environment '{normalized_env}' requires high-entropy APEX_LOCAL_NODE_SHARED_SECRET (>=96 bits Shannon entropy)."
            )
    else:
        if len(cleaned) < 16:
            raise ValueError("Local Node secret must be at least 16 characters long.")
    return cleaned


def redact_node_secrets(message: str, *, extra_secrets: tuple[str, ...] = ()) -> str:
    """Scrub device secrets, bearer tokens, and master secrets from log/diagnostic text."""
    redacted = _NODE_SECRET_PATTERN.sub("[REDACTED_NODE_SECRET]", message)
    for sec in extra_secrets:
        if sec and len(sec) >= 8 and sec in redacted:
            redacted = redacted.replace(sec, "[REDACTED_NODE_SECRET]")
    return redacted


_REGISTERED_SENSITIVE_SECRETS: set[str] = set()


class LocalNodeSecretRedactionFilter(logging.Filter):
    """Logging filter ensuring Local Node secrets never reach log handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        scrubbed = redact_node_secrets(
            msg, extra_secrets=tuple(_REGISTERED_SENSITIVE_SECRETS)
        )
        record.msg = scrubbed
        record.args = ()
        return True


logger = logging.getLogger(__name__)
if not any(isinstance(f, LocalNodeSecretRedactionFilter) for f in logger.filters):
    logger.addFilter(LocalNodeSecretRedactionFilter())


class HandshakeRequest(BaseModel):
    protocol_version: str
    node_id: str = Field(min_length=3, max_length=120)
    node_name: str = Field(min_length=1, max_length=255)
    tenant_id: uuid.UUID
    credential_id: str | None = Field(default=None, max_length=80)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    protocol_version: str
    node_id: str = Field(min_length=3, max_length=120)
    tenant_id: uuid.UUID
    credential_id: str | None = Field(default=None, max_length=80)
    status: str = "ONLINE"
    active_tasks: int = Field(default=0, ge=0)


@dataclass
class DeviceCredentialRecord:
    """Persisted per-device credential state (`secret_hash` only; never plaintext)."""

    tenant_id: uuid.UUID
    node_id: str
    credential_id: str
    secret_hash: str
    key_version: int
    status: CredentialStatus
    created_at: datetime
    revoked_at: datetime | None = None
    rotated_at: datetime | None = None

    def public_metadata(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "node_id": self.node_id,
            "credential_id": self.credential_id,
            "key_version": self.key_version,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
        }


@dataclass(frozen=True)
class IssuedDeviceCredential:
    """One-time provisioning envelope returned exclusively at issuance/rotation."""

    tenant_id: uuid.UUID
    node_id: str
    credential_id: str
    device_secret: SecretStr
    secret_hash: str
    key_version: int
    status: CredentialStatus
    created_at: datetime


@dataclass
class RegisteredNodeState:
    node_id: str
    node_name: str
    tenant_id: uuid.UUID
    protocol_version: str
    capabilities: dict[str, Any]
    session_token: str
    status: str
    registered_at: datetime
    last_heartbeat_at: datetime
    credential_id: str | None = None


class LocalNodeGatewayRegistry:
    """Verifies HMAC signatures, enforces per-device credentials & tenant binding, prevents replay, and tracks node sessions."""

    def __init__(
        self,
        *,
        shared_secret: str | None,
        env: str = "development",
        arena_isolated_mode: bool = True,
        max_skew_seconds: int = 300,
    ) -> None:
        self._env = env.strip().lower()
        self._secret = validate_local_node_secret(shared_secret, env=self._env)
        self._arena_isolated_mode = arena_isolated_mode
        self._max_skew_seconds = max_skew_seconds
        self._seen_nonces: dict[str, int] = {}
        self._nodes: dict[str, RegisteredNodeState] = {}
        self._node_tenant_binding: dict[str, uuid.UUID] = {}
        self._credentials: dict[str, DeviceCredentialRecord] = {}
        self._credentials_by_node: dict[str, list[str]] = {}
        self._revoked_nodes: set[str] = set()

        if self._secret:
            _REGISTERED_SENSITIVE_SECRETS.add(self._secret)

    def __repr__(self) -> str:
        return (
            f"LocalNodeGatewayRegistry(env={self._env!r}, "
            f"active_nodes={len(self._nodes)}, secret='***REDACTED***')"
        )

    @property
    def arena_isolated_mode(self) -> bool:
        return self._arena_isolated_mode

    def _derive_device_signing_secret(
        self,
        *,
        tenant_id: uuid.UUID,
        node_id: str,
        credential_id: str,
        key_version: int,
    ) -> str:
        context = f"apex-node-cred-v1:{tenant_id}:{node_id}:{credential_id}:{key_version}".encode()
        digest = hmac.new(self._secret.encode("utf-8"), context, hashlib.sha256).hexdigest()
        return f"apxnd_{digest}"

    @staticmethod
    def _hash_secret(raw_secret: str) -> str:
        return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()

    def provision_device_credential(
        self,
        *,
        tenant_id: uuid.UUID,
        node_id: str,
    ) -> IssuedDeviceCredential:
        """Issue a tenant-bound per-device credential; stores only `secret_hash`."""
        existing_tenant = self._node_tenant_binding.get(node_id)
        if existing_tenant is not None and existing_tenant != tenant_id:
            raise PermissionError(
                f"Node '{node_id}' is already bound to a different organization."
            )
        self._node_tenant_binding[node_id] = tenant_id
        self._revoked_nodes.discard(node_id)

        existing_ids = self._credentials_by_node.get(node_id, [])
        next_version = (
            max((self._credentials[cid].key_version for cid in existing_ids), default=0) + 1
        )
        credential_id = f"lnc_{secrets.token_hex(12)}"
        raw_secret = self._derive_device_signing_secret(
            tenant_id=tenant_id,
            node_id=node_id,
            credential_id=credential_id,
            key_version=next_version,
        )
        sec_hash = self._hash_secret(raw_secret)
        now = utcnow()
        rec = DeviceCredentialRecord(
            tenant_id=tenant_id,
            node_id=node_id,
            credential_id=credential_id,
            secret_hash=sec_hash,
            key_version=next_version,
            status="ACTIVE",
            created_at=now,
        )
        self._credentials[credential_id] = rec
        self._credentials_by_node.setdefault(node_id, []).append(credential_id)
        logger.info(
            "Provisioned Local Node credential %s for tenant=%s node=%s (version=%d)",
            credential_id,
            tenant_id,
            node_id,
            next_version,
        )
        return IssuedDeviceCredential(
            tenant_id=tenant_id,
            node_id=node_id,
            credential_id=credential_id,
            device_secret=SecretStr(raw_secret),
            secret_hash=sec_hash,
            key_version=next_version,
            status="ACTIVE",
            created_at=now,
        )

    def rotate_device_credential(
        self,
        *,
        tenant_id: uuid.UUID,
        node_id: str,
    ) -> IssuedDeviceCredential:
        """Rotate device credential atomically: mark prior ACTIVE keys ROTATED and invalidate live sessions."""
        bound_tenant = self._node_tenant_binding.get(node_id)
        if bound_tenant is not None and bound_tenant != tenant_id:
            raise PermissionError("Cross-tenant credential rotation is forbidden.")
        if node_id in self._revoked_nodes:
            raise PermissionError("Cannot rotate credentials for a revoked device.")

        now = utcnow()
        for cid in self._credentials_by_node.get(node_id, []):
            rec = self._credentials[cid]
            if rec.tenant_id == tenant_id and rec.status == "ACTIVE":
                rec.status = "ROTATED"
                rec.rotated_at = now

        # Force re-handshake with the newly rotated credential
        self._nodes.pop(node_id, None)
        return self.provision_device_credential(tenant_id=tenant_id, node_id=node_id)

    def revoke_device(
        self,
        *,
        tenant_id: uuid.UUID,
        node_id: str,
        credential_id: str | None = None,
    ) -> dict[str, object]:
        """Revoke a specific device credential or entire node for `tenant_id` and terminate sessions."""
        bound_tenant = self._node_tenant_binding.get(node_id)
        if bound_tenant is None:
            state = self._nodes.get(node_id)
            if state is not None:
                bound_tenant = state.tenant_id
        if bound_tenant is not None and bound_tenant != tenant_id:
            raise PermissionError("Cross-tenant device revocation is forbidden.")

        now = utcnow()
        revoked_ids: list[str] = []
        for cid in self._credentials_by_node.get(node_id, []):
            rec = self._credentials[cid]
            if rec.tenant_id == tenant_id and (credential_id is None or cid == credential_id):
                rec.status = "REVOKED"
                rec.revoked_at = now
                revoked_ids.append(cid)

        has_remaining_active = any(
            self._credentials[cid].status == "ACTIVE"
            for cid in self._credentials_by_node.get(node_id, [])
        )
        if credential_id is None or not has_remaining_active:
            self._revoked_nodes.add(node_id)
            self._nodes.pop(node_id, None)
        elif node_id in self._nodes and self._nodes[node_id].credential_id in revoked_ids:
            self._nodes.pop(node_id, None)

        self._node_tenant_binding.setdefault(node_id, tenant_id)
        logger.info(
            "Revoked Local Node device node=%s tenant=%s credentials=%s",
            node_id,
            tenant_id,
            revoked_ids,
        )
        return {
            "node_id": node_id,
            "tenant_id": str(tenant_id),
            "revoked_credential_ids": revoked_ids,
            "device_revoked": node_id in self._revoked_nodes,
        }

    def list_device_credentials(self, *, tenant_id: uuid.UUID) -> list[dict[str, object]]:
        """Return safe credential metadata for `tenant_id` (never secrets or hashes)."""
        return [
            rec.public_metadata()
            for rec in self._credentials.values()
            if rec.tenant_id == tenant_id
        ]

    def _resolve_verification_key(
        self,
        *,
        payload: dict[str, Any],
        credential_id_header: str | None,
    ) -> tuple[str, str | None]:
        node_id = str(payload.get("node_id") or "").strip()
        raw_tenant = payload.get("tenant_id")
        req_tenant: uuid.UUID | None = None
        if raw_tenant is not None:
            try:
                req_tenant = uuid.UUID(str(raw_tenant))
            except ValueError as exc:
                raise PermissionError("Invalid tenant_id UUID in Local Node payload.") from exc

        if node_id and node_id in self._revoked_nodes:
            raise PermissionError("Local Node device has been revoked.")

        bound_tenant = self._node_tenant_binding.get(node_id)
        if bound_tenant is not None and req_tenant is not None and bound_tenant != req_tenant:
            raise PermissionError("Cross-tenant Local Node access rejected.")

        explicit_cid = (credential_id_header or payload.get("credential_id") or "").strip() or None
        node_cids = self._credentials_by_node.get(node_id, [])

        if explicit_cid is not None or node_cids:
            rec: DeviceCredentialRecord | None = None
            if explicit_cid is not None:
                rec = self._credentials.get(explicit_cid)
                if rec is None:
                    raise PermissionError("Unknown Local Node credential_id.")
            else:
                # Find active credential for this node
                active = [
                    self._credentials[cid]
                    for cid in node_cids
                    if self._credentials[cid].status == "ACTIVE"
                ]
                if not active:
                    raise PermissionError(
                        "No ACTIVE Local Node credential remains (device revoked or rotated)."
                    )
                rec = active[-1]

            if node_id and rec.node_id != node_id:
                raise PermissionError("Credential node_id mismatch.")
            if req_tenant is not None and rec.tenant_id != req_tenant:
                raise PermissionError("Cross-tenant Local Node credential rejected.")
            if rec.status == "REVOKED":
                raise PermissionError("Local Node credential has been revoked.")
            if rec.status != "ACTIVE":
                raise PermissionError(
                    f"Local Node credential is '{rec.status}' and no longer valid."
                )

            derived = self._derive_device_signing_secret(
                tenant_id=rec.tenant_id,
                node_id=rec.node_id,
                credential_id=rec.credential_id,
                key_version=rec.key_version,
            )
            if not hmac.compare_digest(self._hash_secret(derived), rec.secret_hash):
                raise PermissionError("Device credential hash verification failed.")
            return derived, rec.credential_id

        return self._secret, None

    def verify_signed_request(
        self,
        *,
        timestamp_header: str | None,
        nonce_header: str | None,
        signature_header: str | None,
        payload: dict[str, Any],
        credential_id_header: str | None = None,
        now_epoch: int | None = None,
    ) -> str | None:
        if not timestamp_header or not nonce_header or not signature_header:
            raise PermissionError("Missing Local Node HMAC headers.")
        if len(nonce_header.strip()) < 8:
            raise PermissionError("Local Node nonce too short.")
        try:
            ts = int(timestamp_header)
        except ValueError as exc:
            raise PermissionError("Invalid X-Apex-Node-Timestamp.") from exc

        now = int(time.time()) if now_epoch is None else now_epoch
        if abs(now - ts) > self._max_skew_seconds:
            raise PermissionError("Local Node timestamp outside allowed replay window.")

        # Prune expired nonces
        cutoff = now - self._max_skew_seconds
        expired = [k for k, v in self._seen_nonces.items() if v < cutoff]
        for k in expired:
            self._seen_nonces.pop(k, None)

        if nonce_header in self._seen_nonces:
            raise PermissionError("Replay detected: nonce already used.")

        signing_secret, matched_cred_id = self._resolve_verification_key(
            payload=payload,
            credential_id_header=credential_id_header,
        )

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signing_input = f"{ts}.{nonce_header}.{canonical}".encode()
        expected = hmac.new(
            signing_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature_header):
            raise PermissionError("Invalid Local Node HMAC signature.")

        self._seen_nonces[nonce_header] = ts
        return matched_cred_id

    def register_handshake(
        self,
        req: HandshakeRequest,
        *,
        verified_credential_id: str | None = None,
    ) -> RegisteredNodeState:
        if req.protocol_version != SUPPORTED_PROTOCOL_VERSION:
            raise ValueError(
                f"Unsupported protocol_version '{req.protocol_version}'; "
                f"expected '{SUPPORTED_PROTOCOL_VERSION}'."
            )
        if req.node_id in self._revoked_nodes:
            raise PermissionError("Local Node device has been revoked.")

        bound_tenant = self._node_tenant_binding.get(req.node_id)
        if bound_tenant is not None and bound_tenant != req.tenant_id:
            raise PermissionError("Cross-tenant Local Node registration rejected.")
        self._node_tenant_binding[req.node_id] = req.tenant_id

        now = utcnow()
        token = secrets.token_urlsafe(32)
        _REGISTERED_SENSITIVE_SECRETS.add(token)
        state = RegisteredNodeState(
            node_id=req.node_id,
            node_name=req.node_name,
            tenant_id=req.tenant_id,
            protocol_version=req.protocol_version,
            capabilities=dict(req.capabilities),
            session_token=token,
            status="ONLINE",
            registered_at=now,
            last_heartbeat_at=now,
            credential_id=verified_credential_id or req.credential_id,
        )
        self._nodes[req.node_id] = state
        return state

    def record_heartbeat(
        self,
        req: HeartbeatRequest,
        *,
        bearer_token: str | None,
    ) -> RegisteredNodeState:
        if req.node_id in self._revoked_nodes:
            raise PermissionError("Local Node device has been revoked.")
        state = self._nodes.get(req.node_id)
        if state is None:
            raise PermissionError("Unknown Local Node session.")
        if state.tenant_id != req.tenant_id:
            raise PermissionError("Cross-tenant Local Node session rejected.")
        if state.credential_id is not None:
            cred = self._credentials.get(state.credential_id)
            if cred is None or cred.status != "ACTIVE":
                self._nodes.pop(req.node_id, None)
                raise PermissionError("Local Node session credential is no longer ACTIVE.")
        if not bearer_token or not hmac.compare_digest(state.session_token, bearer_token):
            raise PermissionError("Invalid Local Node session token.")
        state.status = req.status
        state.last_heartbeat_at = utcnow()
        return state

    def verify_session_token(
        self,
        node_id: str,
        token: str,
        *,
        expected_tenant_id: uuid.UUID | None = None,
    ) -> RegisteredNodeState:
        if node_id in self._revoked_nodes:
            raise PermissionError("Local Node device has been revoked.")
        state = self._nodes.get(node_id)
        if state is None or not token or not hmac.compare_digest(state.session_token, token):
            raise PermissionError("Invalid WebSocket session token.")
        if expected_tenant_id is not None and state.tenant_id != expected_tenant_id:
            raise PermissionError("Cross-tenant WebSocket session rejected.")
        if state.credential_id is not None:
            cred = self._credentials.get(state.credential_id)
            if cred is None or cred.status != "ACTIVE":
                self._nodes.pop(node_id, None)
                raise PermissionError("Local Node session credential is no longer ACTIVE.")
        return state

    def is_node_authorized_for_tenant(self, *, tenant_id: uuid.UUID, node_id: str) -> bool:
        if node_id in self._revoked_nodes:
            return False
        state = self._nodes.get(node_id)
        if state is not None and state.tenant_id == tenant_id:
            return True
        bound = self._node_tenant_binding.get(node_id)
        if bound == tenant_id:
            cids = self._credentials_by_node.get(node_id, [])
            return not cids or any(self._credentials[c].status == "ACTIVE" for c in cids)
        return False

    def status_snapshot(self) -> dict[str, object]:
        """Safe telemetry snapshot — never exposes secrets, hashes, or session tokens."""
        return {
            "protocol_version": SUPPORTED_PROTOCOL_VERSION,
            "arena_isolated_mode": self._arena_isolated_mode,
            "user_machine_connected": False
            if self._arena_isolated_mode
            else len(self._nodes) > 0,
            "loopback_protocol_sessions": len(self._nodes),
            "provisioned_device_credentials_count": sum(
                1 for r in self._credentials.values() if r.status == "ACTIVE"
            ),
            "revoked_devices_count": len(self._revoked_nodes),
            "note": (
                "Arena runs in an isolated cloud sandbox; Local Node is NOT connected "
                "to the user's physical computer. Standalone installation is supported "
                "without Cloud architecture changes."
                if self._arena_isolated_mode
                else "Cloud gateway active for external Hybrid Local Nodes."
            ),
        }
