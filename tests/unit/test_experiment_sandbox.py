"""Suite A — Unit tests for ExperimentSandboxAdapter, limits, IO staging, and isolation guards."""

from __future__ import annotations

import hashlib
import os
import uuid

import httpx
import pytest

from apex_one.sandbox.adapter import (
    ExternalMicroVMSandboxAdapter,
    GuardedUnverifiedSandboxAdapter,
    UntrustedExecutionBlockedError,
)
from apex_one.sandbox.contracts import (
    ArtifactRule,
    InputFileSpec,
    ResourceLimits,
    SandboxJob,
    SandboxJobState,
    TimeoutPolicy,
)
from apex_one.sandbox.validator import (
    ArtifactValidationError,
    IsolatedIOWorkspace,
)

pytestmark = pytest.mark.unit


def test_sandbox_job_rejects_secret_env_vars_and_traversal_inputs() -> None:
    tid = uuid.uuid4()
    with pytest.raises(ValueError, match="forbidden"):
        SandboxJob(
            tenant_id=tid,
            entrypoint=["python3", "exp.py"],
            env_vars={"DATABASE_URL": "postgresql://leak"},
        )

    with pytest.raises(ValueError, match="Path traversal"):
        InputFileSpec(relative_path="../../etc/passwd", content_utf8="x")


def test_isolated_io_workspace_and_artifact_validator_guards() -> None:
    inputs = [
        InputFileSpec(relative_path="data/dataset.csv", content_utf8="a,b\n1,2\n")
    ]
    rule = ArtifactRule(
        max_files=2,
        max_single_file_bytes=1024,
        max_total_bytes=1500,
        allowed_extensions=[".json", ".csv"],
    )

    with IsolatedIOWorkspace(inputs, rule) as ws:
        staged = ws.input_dir / "data" / "dataset.csv"
        assert staged.exists()
        # Input file staged read-only (0400)
        assert (staged.stat().st_mode & 0o777) == 0o400

        # Valid output artifact
        out_json = ws.output_dir / "metrics.json"
        payload = b'{"accuracy": 0.97}'
        out_json.write_bytes(payload)

        validated = ws.collect_validated_artifacts()
        assert len(validated) == 1
        assert validated[0].relative_path == "metrics.json"
        assert validated[0].sha256 == hashlib.sha256(payload).hexdigest()

        # Disallowed executable extension rejected
        bad_bin = ws.output_dir / "exploit.sh"
        bad_bin.write_text("#!/bin/sh\nid\n", encoding="utf-8")
        with pytest.raises(ArtifactValidationError, match="allowed_extensions"):
            ws.collect_validated_artifacts()
        bad_bin.unlink()

        # Symlink escape rejected
        symlink_path = ws.output_dir / "leak.json"
        os.symlink("/etc/hostname", symlink_path)
        with pytest.raises(ArtifactValidationError, match="Symlink"):
            ws.collect_validated_artifacts()


async def test_guarded_adapter_blocks_untrusted_code_execution() -> None:
    adapter = GuardedUnverifiedSandboxAdapter()
    readiness = adapter.readiness_status()
    assert readiness.isolation_verified is False
    assert readiness.arbitrary_code_execution_ready is False

    job = SandboxJob(
        tenant_id=uuid.uuid4(),
        runtime="python3.11",
        entrypoint=["python3", "run_experiment.py"],
        resources=ResourceLimits(max_cpu_cores=1.0, max_memory_mb=256),
        timeout=TimeoutPolicy(wall_time_seconds=30),
    )

    reg = await adapter.register_job(job)
    assert reg.state == SandboxJobState.VALIDATED

    with pytest.raises(UntrustedExecutionBlockedError):
        await adapter.execute(job)

    stored = await adapter.get_job(job.tenant_id, job.job_id)
    assert stored is not None
    assert stored.state == SandboxJobState.BLOCKED_UNVERIFIED_ISOLATION


async def test_external_microvm_adapter_validates_isolation_attestation() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-sandbox-key"
        return httpx.Response(
            200,
            json={
                "isolation_attested": True,
                "state": "SUCCEEDED",
                "exit_code": 0,
                "stdout_preview": "loss=0.042",
                "artifacts": [
                    {
                        "relative_path": "results.json",
                        "size_bytes": 18,
                        "sha256": "a" * 64,
                        "extension": ".json",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    try:
        adapter = ExternalMicroVMSandboxAdapter(
            provider_name="external_http",
            endpoint="https://sandbox.internal",
            api_key="test-sandbox-key",
            isolation_attested=True,
            http_client=client,
        )
        assert adapter.readiness_status().arbitrary_code_execution_ready is True
        job = SandboxJob(
            tenant_id=uuid.uuid4(),
            entrypoint=["python3", "eval.py"],
        )
        res = await adapter.execute(job)
        assert res.state == SandboxJobState.SUCCEEDED
        assert res.isolation_verified is True
        assert len(res.artifacts) == 1
    finally:
        await client.aclose()
