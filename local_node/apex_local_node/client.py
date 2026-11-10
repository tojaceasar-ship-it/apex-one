"""Protocol v1.0 client for the standalone APEX ONE Hybrid Local Node."""

from __future__ import annotations

from typing import Any

import httpx

from apex_local_node import PROTOCOL_VERSION
from apex_local_node.capabilities import discover_node_capabilities
from apex_local_node.config import LocalNodeSettings
from apex_local_node.signer import build_signed_headers


class LocalNodeProtocolError(RuntimeError):
    """Raised when handshake or protocol exchange with Cloud Gateway fails."""


class LocalNodeClient:
    """Async protocol client communicating with APEX ONE Cloud Gateway."""

    def __init__(
        self,
        settings: LocalNodeSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._session_token: str | None = None

    @property
    def session_token(self) -> str | None:
        return self._session_token

    def build_handshake_payload(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "node_id": self._settings.node_id,
            "node_name": self._settings.node_name,
            "tenant_id": str(self._settings.tenant_id),
            "capabilities": discover_node_capabilities(),
        }

    def build_heartbeat_payload(self, *, active_tasks: int = 0) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "node_id": self._settings.node_id,
            "tenant_id": str(self._settings.tenant_id),
            "status": "BUSY" if active_tasks > 0 else "ONLINE",
            "active_tasks": active_tasks,
        }

    async def handshake(self) -> dict[str, Any]:
        payload = self.build_handshake_payload()
        headers = build_signed_headers(
            self._settings.shared_secret.get_secret_value(),
            payload,
        )
        base = self._settings.cloud_url.rstrip("/")
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(
                f"{base}/api/v1/local-node/handshake",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                raise LocalNodeProtocolError(
                    f"Handshake rejected ({resp.status_code}): {resp.text}"
                )
            data = dict(resp.json())
            if data.get("protocol_version") != PROTOCOL_VERSION:
                raise LocalNodeProtocolError("Incompatible Cloud protocol_version.")
            self._session_token = str(data["session_token"])
            return data
        finally:
            if owns_client:
                await client.aclose()

    async def heartbeat(self, *, active_tasks: int = 0) -> dict[str, Any]:
        if not self._session_token:
            raise LocalNodeProtocolError("Cannot send heartbeat prior to handshake.")
        payload = self.build_heartbeat_payload(active_tasks=active_tasks)
        headers = build_signed_headers(
            self._settings.shared_secret.get_secret_value(),
            payload,
        )
        headers["Authorization"] = f"Bearer {self._session_token}"
        base = self._settings.cloud_url.rstrip("/")
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(
                f"{base}/api/v1/local-node/heartbeat",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                raise LocalNodeProtocolError(
                    f"Heartbeat rejected ({resp.status_code}): {resp.text}"
                )
            return dict(resp.json())
        finally:
            if owns_client:
                await client.aclose()
