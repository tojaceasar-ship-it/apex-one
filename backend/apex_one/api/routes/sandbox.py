"""Experiment Sandbox API endpoints with mandatory isolation gating."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apex_one.api.dependencies import get_sandbox_adapter, require_tenant_id
from apex_one.sandbox.adapter import (
    ExperimentSandboxAdapter,
    UntrustedExecutionBlockedError,
)
from apex_one.sandbox.contracts import (
    ArtifactRule,
    InputFileSpec,
    ResourceLimits,
    SandboxExecutionResult,
    SandboxJob,
    SandboxReadinessStatus,
    TimeoutPolicy,
)

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])


class SubmitSandboxJobBody(BaseModel):
    runtime: Literal["python3.11", "node22", "r4"] = "python3.11"
    entrypoint: list[str] = Field(min_length=1, max_length=16)
    inputs: list[InputFileSpec] = Field(default_factory=list)
    env_vars: dict[str, str] = Field(default_factory=dict)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    artifact_rule: ArtifactRule = Field(default_factory=ArtifactRule)


@router.get("/readiness", response_model=SandboxReadinessStatus)
async def sandbox_readiness(
    adapter: ExperimentSandboxAdapter = Depends(get_sandbox_adapter),
) -> SandboxReadinessStatus:
    return adapter.readiness_status()


@router.post("/jobs/validate", response_model=SandboxExecutionResult)
async def validate_sandbox_job(
    body: SubmitSandboxJobBody,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    adapter: ExperimentSandboxAdapter = Depends(get_sandbox_adapter),
) -> SandboxExecutionResult:
    try:
        job = SandboxJob(
            tenant_id=tenant_id,
            runtime=body.runtime,
            entrypoint=body.entrypoint,
            inputs=body.inputs,
            env_vars=body.env_vars,
            resources=body.resources,
            timeout=body.timeout,
            artifact_rule=body.artifact_rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await adapter.register_job(job)


@router.post("/jobs/execute", response_model=SandboxExecutionResult)
async def execute_sandbox_job(
    body: SubmitSandboxJobBody,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    adapter: ExperimentSandboxAdapter = Depends(get_sandbox_adapter),
) -> SandboxExecutionResult:
    job = SandboxJob(
        tenant_id=tenant_id,
        runtime=body.runtime,
        entrypoint=body.entrypoint,
        inputs=body.inputs,
        env_vars=body.env_vars,
        resources=body.resources,
        timeout=body.timeout,
        artifact_rule=body.artifact_rule,
    )
    try:
        return await adapter.execute(job)
    except UntrustedExecutionBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "UNTRUSTED_EXECUTION_BLOCKED",
                "isolation_verified": False,
                "message": str(exc),
            },
        ) from exc
