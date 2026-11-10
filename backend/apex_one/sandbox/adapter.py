"""ExperimentSandboxAdapter implementations with mandatory isolation gating."""

from __future__ import annotations

import uuid
from typing import Protocol

import httpx

from apex_one.config import Settings
from apex_one.db.base import utcnow
from apex_one.sandbox.contracts import (
    SandboxExecutionResult,
    SandboxJob,
    SandboxJobState,
    SandboxReadinessStatus,
    ValidatedArtifact,
)


class UntrustedExecutionBlockedError(PermissionError):
    """Raised when attempting to execute untrusted code without verified isolation."""


class ExperimentSandboxAdapter(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def isolation_verified(self) -> bool: ...

    def readiness_status(self) -> SandboxReadinessStatus: ...

    async def register_job(self, job: SandboxJob) -> SandboxExecutionResult: ...

    async def execute(self, job: SandboxJob) -> SandboxExecutionResult: ...

    async def get_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None: ...

    async def cancel_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None: ...


class GuardedUnverifiedSandboxAdapter:
    """Default adapter when no external isolated microVM sandbox provider is configured.

    Validates contracts, resource limits, timeouts, and manages job records,
    but strictly blocks execution of untrusted agent code.
    """

    def __init__(self, provider_name: str = "none") -> None:
        self._provider = provider_name
        self._jobs: dict[uuid.UUID, SandboxExecutionResult] = {}

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def isolation_verified(self) -> bool:
        return False

    def readiness_status(self) -> SandboxReadinessStatus:
        return SandboxReadinessStatus(
            provider=self._provider,
            isolation_verified=False,
            arbitrary_code_execution_ready=False,
            reason=(
                "No verified external microVM sandbox provider is configured "
                "(APEX_SANDBOX_PROVIDER='none' or APEX_SANDBOX_ISOLATION_ATTESTED=false). "
                "Untrusted code execution is strictly blocked by policy."
            ),
        )

    async def register_job(self, job: SandboxJob) -> SandboxExecutionResult:
        record = SandboxExecutionResult(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            provider=self._provider,
            isolation_verified=False,
            state=SandboxJobState.VALIDATED,
            created_at=utcnow(),
        )
        self._jobs[job.job_id] = record
        return record

    async def execute(self, job: SandboxJob) -> SandboxExecutionResult:
        now = utcnow()
        blocked = SandboxExecutionResult(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            provider=self._provider,
            isolation_verified=False,
            state=SandboxJobState.BLOCKED_UNVERIFIED_ISOLATION,
            error_reason=self.readiness_status().reason,
            created_at=now,
            completed_at=now,
        )
        self._jobs[job.job_id] = blocked
        raise UntrustedExecutionBlockedError(blocked.error_reason)

    async def get_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None:
        rec = self._jobs.get(job_id)
        if rec is None or rec.tenant_id != tenant_id:
            return None
        return rec

    async def cancel_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None:
        rec = await self.get_job(tenant_id, job_id)
        if rec is None:
            return None
        if rec.state in {
            SandboxJobState.SUCCEEDED,
            SandboxJobState.FAILED,
            SandboxJobState.CANCELLED,
            SandboxJobState.BLOCKED_UNVERIFIED_ISOLATION,
        }:
            return rec
        updated = rec.model_copy(
            update={"state": SandboxJobState.CANCELLED, "completed_at": utcnow()}
        )
        self._jobs[job_id] = updated
        return updated


class ExternalMicroVMSandboxAdapter:
    """Adapter for an attested external microVM sandbox provider over HTTPS."""

    def __init__(
        self,
        *,
        provider_name: str,
        endpoint: str,
        api_key: str | None,
        isolation_attested: bool,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider_name
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._isolation_attested = isolation_attested
        self._http_client = http_client
        self._jobs: dict[uuid.UUID, SandboxExecutionResult] = {}

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def isolation_verified(self) -> bool:
        return bool(self._isolation_attested and self._endpoint)

    def readiness_status(self) -> SandboxReadinessStatus:
        if not self.isolation_verified:
            return SandboxReadinessStatus(
                provider=self._provider,
                isolation_verified=False,
                arbitrary_code_execution_ready=False,
                reason="External sandbox provider configured without verified isolation attestation.",
            )
        return SandboxReadinessStatus(
            provider=self._provider,
            isolation_verified=True,
            arbitrary_code_execution_ready=True,
            reason="External microVM sandbox provider configured with attested isolation.",
        )

    async def register_job(self, job: SandboxJob) -> SandboxExecutionResult:
        rec = SandboxExecutionResult(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            provider=self._provider,
            isolation_verified=self.isolation_verified,
            state=SandboxJobState.VALIDATED,
            created_at=utcnow(),
        )
        self._jobs[job.job_id] = rec
        return rec

    async def execute(self, job: SandboxJob) -> SandboxExecutionResult:
        if not self.isolation_verified:
            raise UntrustedExecutionBlockedError(self.readiness_status().reason)

        created = utcnow()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        total_timeout = float(
            job.timeout.wall_time_seconds + job.timeout.grace_kill_seconds + 5
        )
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=total_timeout)
        try:
            resp = await client.post(
                f"{self._endpoint}/v1/sandbox/execute",
                json=job.model_dump(mode="json"),
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("isolation_attested", False):
                raise UntrustedExecutionBlockedError(
                    "External sandbox response lacked isolation_attested=true confirmation."
                )

            allowed_exts = set(job.artifact_rule.allowed_extensions)
            validated_artifacts: list[ValidatedArtifact] = []
            total_bytes = 0
            for raw_art in data.get("artifacts", []):
                art = ValidatedArtifact.model_validate(raw_art)
                if art.extension not in allowed_exts:
                    raise ValueError(
                        f"Remote artifact extension '{art.extension}' disallowed."
                    )
                if art.size_bytes > job.artifact_rule.max_single_file_bytes:
                    raise ValueError("Remote artifact exceeded max_single_file_bytes.")
                total_bytes += art.size_bytes
                if total_bytes > job.artifact_rule.max_total_bytes:
                    raise ValueError("Remote artifacts exceeded max_total_bytes.")
                validated_artifacts.append(art)

            result = SandboxExecutionResult(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                provider=self._provider,
                isolation_verified=True,
                state=SandboxJobState(data.get("state", "SUCCEEDED")),
                exit_code=data.get("exit_code", 0),
                stdout_preview=str(data.get("stdout_preview", ""))[:8192],
                stderr_preview=str(data.get("stderr_preview", ""))[:8192],
                artifacts=validated_artifacts,
                created_at=created,
                completed_at=utcnow(),
            )
            self._jobs[job.job_id] = result
            return result
        finally:
            if owns_client:
                await client.aclose()

    async def get_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None:
        rec = self._jobs.get(job_id)
        if rec is None or rec.tenant_id != tenant_id:
            return None
        return rec

    async def cancel_job(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID
    ) -> SandboxExecutionResult | None:
        rec = await self.get_job(tenant_id, job_id)
        if rec is None:
            return None
        updated = rec.model_copy(
            update={"state": SandboxJobState.CANCELLED, "completed_at": utcnow()}
        )
        self._jobs[job_id] = updated
        return updated


def build_sandbox_adapter(settings: Settings) -> ExperimentSandboxAdapter:
    if (
        settings.sandbox_provider != "none"
        and settings.sandbox_endpoint
        and settings.sandbox_isolation_attested
    ):
        api_key = (
            settings.sandbox_api_key.get_secret_value()
            if settings.sandbox_api_key
            else None
        )
        return ExternalMicroVMSandboxAdapter(
            provider_name=settings.sandbox_provider,
            endpoint=settings.sandbox_endpoint,
            api_key=api_key,
            isolation_attested=settings.sandbox_isolation_attested,
        )
    return GuardedUnverifiedSandboxAdapter(provider_name=settings.sandbox_provider)
