"""Typed contracts for isolated experiment execution in APEX ONE."""

from __future__ import annotations

import hashlib
import posixpath
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FORBIDDEN_ENV_PREFIXES = (
    "APEX_",
    "DATABASE_",
    "POSTGRES",
    "REDIS_",
    "AWS_",
    "GCP_",
    "GOOGLE_",
    "AZURE_",
    "GH_",
    "GITHUB_",
    "OPENAI_",
    "ANTHROPIC_",
    "LD_",
    "PYTHONPATH",
)


def validate_safe_relative_path(rel_path: str) -> str:
    if not rel_path or "\x00" in rel_path or "\\" in rel_path:
        raise ValueError("Invalid relative path characters.")
    if rel_path.startswith("/") or rel_path.startswith("~"):
        raise ValueError("Absolute or home-relative paths are forbidden.")
    norm = posixpath.normpath(rel_path)
    if norm in {".", ".."} or norm.startswith("../") or "/../" in f"/{norm}/":
        raise ValueError(f"Path traversal detected in '{rel_path}'.")
    return norm


class SandboxJobState(StrEnum):
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    BLOCKED_UNVERIFIED_ISOLATION = "BLOCKED_UNVERIFIED_ISOLATION"


class ResourceLimits(BaseModel):
    max_cpu_cores: float = Field(default=1.0, ge=0.1, le=8.0)
    max_memory_mb: int = Field(default=512, ge=64, le=16384)
    max_disk_mb: int = Field(default=256, ge=16, le=10240)
    max_pids: int = Field(default=32, ge=4, le=512)
    max_output_bytes: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    allow_network: bool = Field(default=False)


class TimeoutPolicy(BaseModel):
    wall_time_seconds: int = Field(default=60, ge=1, le=1800)
    cpu_time_seconds: int = Field(default=60, ge=1, le=1800)
    grace_kill_seconds: int = Field(default=5, ge=1, le=30)


class ArtifactRule(BaseModel):
    max_files: int = Field(default=32, ge=1, le=256)
    max_single_file_bytes: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    max_total_bytes: int = Field(default=52_428_800, ge=1024, le=524_288_000)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [
            ".json",
            ".csv",
            ".txt",
            ".md",
            ".png",
            ".svg",
            ".pdf",
            ".parquet",
        ]
    )

    @field_validator("allowed_extensions")
    @classmethod
    def _normalize_exts(cls, exts: list[str]) -> list[str]:
        normalized: list[str] = []
        for ext in exts:
            cleaned = ext.strip().lower()
            if not cleaned.startswith(".") or len(cleaned) < 2:
                raise ValueError(f"Invalid file extension '{ext}'.")
            normalized.append(cleaned)
        return normalized


class InputFileSpec(BaseModel):
    relative_path: str
    content_utf8: str = Field(max_length=5_000_000)

    @field_validator("relative_path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return validate_safe_relative_path(value)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content_utf8.encode("utf-8")).hexdigest()


class ValidatedArtifact(BaseModel):
    relative_path: str
    size_bytes: int
    sha256: str
    extension: str


class SandboxJob(BaseModel):
    job_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    runtime: Literal["python3.11", "node22", "r4"] = "python3.11"
    entrypoint: list[str] = Field(min_length=1, max_length=16)
    inputs: list[InputFileSpec] = Field(default_factory=list, max_length=64)
    env_vars: dict[str, str] = Field(default_factory=dict)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    artifact_rule: ArtifactRule = Field(default_factory=ArtifactRule)

    @field_validator("entrypoint")
    @classmethod
    def _check_entrypoint(cls, value: list[str]) -> list[str]:
        cleaned = [part.strip() for part in value if part.strip()]
        if not cleaned:
            raise ValueError("SandboxJob entrypoint cannot be empty.")
        return cleaned

    @field_validator("env_vars")
    @classmethod
    def _check_env_vars(cls, env_map: dict[str, str]) -> dict[str, str]:
        for key in env_map:
            upper = key.upper().strip()
            if any(upper.startswith(prefix) for prefix in FORBIDDEN_ENV_PREFIXES):
                raise ValueError(
                    f"Environment variable '{key}' is forbidden in SandboxJob."
                )
        return env_map

    @model_validator(mode="after")
    def _check_unique_inputs(self) -> SandboxJob:
        seen: set[str] = set()
        for item in self.inputs:
            if item.relative_path in seen:
                raise ValueError(f"Duplicate input path '{item.relative_path}'.")
            seen.add(item.relative_path)
        return self


class SandboxExecutionResult(BaseModel):
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    isolation_verified: bool
    state: SandboxJobState
    exit_code: int | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    artifacts: list[ValidatedArtifact] = Field(default_factory=list)
    error_reason: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class SandboxReadinessStatus(BaseModel):
    provider: str
    isolation_verified: bool
    arbitrary_code_execution_ready: bool
    reason: str
