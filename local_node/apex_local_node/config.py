"""Configuration for the standalone APEX ONE Hybrid Local Node."""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_NODE_SECRET = "dev-local-node-protocol-test-secret-32b"


class LocalNodeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="development", validation_alias="APEX_ENV")
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
    credential_id: str | None = Field(
        default=None,
        validation_alias="APEX_NODE_CREDENTIAL_ID",
    )
    shared_secret: SecretStr | None = Field(
        default=None,
        validation_alias="APEX_NODE_SHARED_SECRET",
    )

    @property
    def effective_secret(self) -> str:
        if self.shared_secret is not None and self.shared_secret.get_secret_value().strip():
            return self.shared_secret.get_secret_value().strip()
        if self.env.strip().lower() in {"staging", "production"}:
            raise ValueError(
                f"Environment '{self.env}' requires explicit APEX_NODE_SHARED_SECRET."
            )
        return _DEV_NODE_SECRET

    @model_validator(mode="after")
    def _validate_node_secret(self) -> LocalNodeSettings:
        norm_env = self.env.strip().lower()
        if norm_env in {"staging", "production"}:
            if self.shared_secret is None or not self.shared_secret.get_secret_value().strip():
                raise ValueError(
                    f"Environment '{norm_env}' requires explicit APEX_NODE_SHARED_SECRET."
                )
            raw = self.shared_secret.get_secret_value().strip()
            if raw == _DEV_NODE_SECRET or len(raw) < 32 or len(set(raw)) < 10:
                raise ValueError(
                    f"Environment '{norm_env}' rejects default or low-entropy APEX_NODE_SHARED_SECRET."
                )
        elif self.shared_secret is None:
            self.shared_secret = SecretStr(_DEV_NODE_SECRET)
        return self
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
