"""Cloud Gateway protocol verifier and session registry for Hybrid Local Nodes."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from apex_one.db.base import utcnow

SUPPORTED_PROTOCOL_VERSION = "1.0"
DEFAULT_DEV_NODE_SECRET = "dev-local-node-protocol-test-secret-32b"


class HandshakeRequest(BaseModel):
    protocol_version: str
    node_id: str = Field(min_length=3, max_length=120)
    node_name: str = Field(min_length=1, max_length=255)
    tenant_id: uuid.UUID
    capabilities: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    protocol_version: str
    node_id: str = Field(min_length=3, max_length=120)
    tenant_id: uuid.UUID
    status: str = "ONLINE"
    active_tasks: int = Field(default=0, ge=0)


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


class LocalNodeGatewayRegistry:
    """Verifies HMAC signatures, prevents replay attacks, and tracks node sessions."""

    def __init__(
        self,
        *,
        shared_secret: str,
        arena_isolated_mode: bool = True,
        max_skew_seconds: int = 300,
    ) -> None:
        self._secret = shared_secret
        self._arena_isolated_mode = arena_isolated_mode
        self._max_skew_seconds = max_skew_seconds
        self._seen_nonces: dict[str, int] = {}
        self._nodes: dict[str, RegisteredNodeState] = {}

    @property
    def arena_isolated_mode(self) -> bool:
        return self._arena_isolated_mode

    def verify_signed_request(
        self,
        *,
        timestamp_header: str | None,
        nonce_header: str | None,
        signature_header: str | None,
        payload: dict[str, Any],
        now_epoch: int | None = None,
    ) -> None:
        if not timestamp_header or not nonce_header or not signature_header:
            raise PermissionError("Missing Local Node HMAC headers.")
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

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        signing_input = f"{ts}.{nonce_header}.{canonical}".encode()
        expected = hmac.new(
            self._secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature_header):
            raise PermissionError("Invalid Local Node HMAC signature.")

        self._seen_nonces[nonce_header] = ts

    def register_handshake(self, req: HandshakeRequest) -> RegisteredNodeState:
        if req.protocol_version != SUPPORTED_PROTOCOL_VERSION:
            raise ValueError(
                f"Unsupported protocol_version '{req.protocol_version}'; "
                f"expected '{SUPPORTED_PROTOCOL_VERSION}'."
            )
        now = utcnow()
        token = secrets.token_urlsafe(32)
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
        )
        self._nodes[req.node_id] = state
        return state

    def record_heartbeat(
        self,
        req: HeartbeatRequest,
        *,
        bearer_token: str | None,
    ) -> RegisteredNodeState:
        state = self._nodes.get(req.node_id)
        if state is None or state.tenant_id != req.tenant_id:
            raise PermissionError("Unknown Local Node session.")
        if not bearer_token or not hmac.compare_digest(state.session_token, bearer_token):
            raise PermissionError("Invalid Local Node session token.")
        state.status = req.status
        state.last_heartbeat_at = utcnow()
        return state

    def verify_session_token(self, node_id: str, token: str) -> RegisteredNodeState:
        state = self._nodes.get(node_id)
        if state is None or not hmac.compare_digest(state.session_token, token):
            raise PermissionError("Invalid WebSocket session token.")
        return state

    def status_snapshot(self) -> dict[str, object]:
        return {
            "protocol_version": SUPPORTED_PROTOCOL_VERSION,
            "arena_isolated_mode": self._arena_isolated_mode,
            # Explicitly false in Arena isolated sandbox — never claim user host link
            "user_machine_connected": False
            if self._arena_isolated_mode
            else len(self._nodes) > 0,
            "loopback_protocol_sessions": len(self._nodes),
            "note": (
                "Arena runs in an isolated cloud sandbox; Local Node is NOT connected "
                "to the user's physical computer. Standalone installation is supported "
                "without Cloud architecture changes."
                if self._arena_isolated_mode
                else "Cloud gateway active for external Hybrid Local Nodes."
            ),
        }
