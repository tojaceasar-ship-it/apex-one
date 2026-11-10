"""ExperimentSandboxAdapter implementations with mandatory isolation gating."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path
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


class RootlessContainerSandboxAdapter:
    """Ephemeral rootless Podman/Docker container sandbox (`--network=none --cap-drop=ALL`).

    Never executes code directly on the host OS: requires BOTH a real `podman` or
    `docker` container runtime binary AND `isolation_attested=True`.
    """

    RUNTIME_IMAGES: dict[str, str] = {
        "python3.11": "python:3.11-slim",
        "node22": "node:22-slim",
        "r4": "rocker/r-base:4.4.0",
    }

    def __init__(
        self,
        *,
        isolation_attested: bool,
        runtime_binary: str | None = None,
    ) -> None:
        detected = runtime_binary or shutil.which("podman") or shutil.which("docker")
        self._binary = detected
        self._isolation_attested = isolation_attested
        self._jobs: dict[uuid.UUID, SandboxExecutionResult] = {}

    @property
    def provider_name(self) -> str:
        return "rootless_container"

    @property
    def isolation_verified(self) -> bool:
        return bool(self._isolation_attested and self._binary)

    def readiness_status(self) -> SandboxReadinessStatus:
        if not self.isolation_verified:
            return SandboxReadinessStatus(
                provider="rootless_container",
                isolation_verified=False,
                arbitrary_code_execution_ready=False,
                reason=(
                    "Rootless container runtime (`podman`/`docker`) unavailable or "
                    "APEX_SANDBOX_ISOLATION_ATTESTED=false. Untrusted execution blocked."
                ),
            )
        return SandboxReadinessStatus(
            provider="rootless_container",
            isolation_verified=True,
            arbitrary_code_execution_ready=True,
            reason=f"Isolated rootless container runtime verified ({self._binary}).",
        )

    def build_container_command(self, job: SandboxJob, host_workdir: Path) -> list[str]:
        if not self._binary:
            raise UntrustedExecutionBlockedError(self.readiness_status().reason)
        net_flag = "bridge" if job.resources.allow_network else "none"
        cmd = [
            self._binary,
            "run",
            "--rm",
            f"--network={net_flag}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={job.resources.max_pids}",
            f"--memory={job.resources.max_memory_mb}m",
            f"--cpus={job.resources.max_cpu_cores}",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
            "-v",
            f"{host_workdir}:/workspace:rw",
            "-w",
            "/workspace",
        ]
        for k, v in job.env_vars.items():
            cmd.extend(["-e", f"{k}={v}"])
        cmd.append(self.RUNTIME_IMAGES[job.runtime])
        cmd.extend(job.entrypoint)
        return cmd

    async def register_job(self, job: SandboxJob) -> SandboxExecutionResult:
        rec = SandboxExecutionResult(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            provider="rootless_container",
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
        with tempfile.TemporaryDirectory(prefix="apex-sbx-") as tmp_dir:
            work_path = Path(tmp_dir)
            out_dir = work_path / "artifacts"
            out_dir.mkdir(parents=True, exist_ok=True)
            for inp in job.inputs:
                dest = (work_path / inp.relative_path).resolve()
                if not dest.is_relative_to(work_path.resolve()):
                    raise ValueError("Input file escaped workspace.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(inp.content_utf8, encoding="utf-8")

            cmd = self.build_container_command(job, work_path)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(job.timeout.wall_time_seconds),
                )
                state = (
                    SandboxJobState.SUCCEEDED
                    if proc.returncode == 0
                    else SandboxJobState.FAILED
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                stdout_b, stderr_b = b"", b"Container wall_time_seconds exceeded."
                state = SandboxJobState.TIMED_OUT

            allowed_exts = set(job.artifact_rule.allowed_extensions)
            artifacts: list[ValidatedArtifact] = []
            total_bytes = 0
            for candidate in sorted(out_dir.rglob("*")):
                if not candidate.is_file():
                    continue
                ext = candidate.suffix.lower()
                if ext not in allowed_exts:
                    continue
                raw = candidate.read_bytes()
                if len(raw) > job.artifact_rule.max_single_file_bytes:
                    continue
                total_bytes += len(raw)
                if total_bytes > job.artifact_rule.max_total_bytes:
                    break
                artifacts.append(
                    ValidatedArtifact(
                        relative_path=str(candidate.relative_to(out_dir)),
                        size_bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest(),
                        extension=ext,
                    )
                )
                if len(artifacts) >= job.artifact_rule.max_files:
                    break

            result = SandboxExecutionResult(
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                provider="rootless_container",
                isolation_verified=True,
                state=state,
                exit_code=proc.returncode,
                stdout_preview=stdout_b.decode("utf-8", errors="replace")[:8192],
                stderr_preview=stderr_b.decode("utf-8", errors="replace")[:8192],
                artifacts=artifacts,
                created_at=created,
                completed_at=utcnow(),
            )
            self._jobs[job.job_id] = result
            return result

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
    if settings.sandbox_provider == "rootless_container" and settings.sandbox_isolation_attested:
        return RootlessContainerSandboxAdapter(
            isolation_attested=settings.sandbox_isolation_attested
        )
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
