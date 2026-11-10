"""Configuration for the standalone APEX ONE Hybrid Local Node."""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalNodeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    cloud_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="APEX_NODE_CLOUD_URL",
    )
    node_id: str = Field(
        default_factory=lambda: f"node-{uuid.uuid4().hex[:12]}",
        validation_alias="APEX_NODE_ID",
    )
    node_name: str = Field(
        default_factory=socket.gethostname,
        validation_alias="APEX_NODE_NAME",
    )
    tenant_id: uuid.UUID = Field(
        default=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        validation_alias="APEX_NODE_TENANT_ID",
    )
    shared_secret: SecretStr = Field(
        default=SecretStr("dev-local-node-protocol-test-secret-32b"),
        validation_alias="APEX_NODE_SHARED_SECRET",
    )
    workspace_root: Path = Field(
        default_factory=lambda: Path.cwd(),
        validation_alias="APEX_NODE_WORKSPACE_ROOT",
    )
    heartbeat_interval_seconds: float = Field(
        default=15.0,
        gt=1.0,
        validation_alias="APEX_NODE_HEARTBEAT_SECONDS",
    )

    def resolve_within_workspace(self, relative_path: str) -> Path:
        """Strictly confine Local Node file access to configured workspace_root."""
        if not relative_path or "\x00" in relative_path:
            raise ValueError("Invalid workspace relative path.")
        root = self.workspace_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError(
                f"Path '{relative_path}' escapes Local Node workspace boundary."
            ) from exc
        return candidate
