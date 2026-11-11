"""Health and honest Arena environment adaptation readiness endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from apex_one import __version__
from apex_one.api.dependencies import AppContainer, get_container
from apex_one.config import check_env_var_presence_only

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "apex-one-backend",
        "version": __version__,
        "docker_required": False,
    }


@router.get("/environment/status")
async def environment_status(
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    db_report = await container.db_adapter.health_check()
    cache_report = await container.cache_adapter.health_check()
    sandbox_report = container.sandbox_adapter.readiness_status()
    local_node_report = container.local_node_registry.status_snapshot()
    env_presence = check_env_var_presence_only()

    verdict = (
        "READY"
        if (db_report.connected and sandbox_report.arbitrary_code_execution_ready)
        else "CONDITIONAL"
    )

    conditions: list[str] = []
    if not db_report.configured:
        conditions.append(
            "External PostgreSQL (Neon / PostgreSQL 16+ with pgvector) is not yet configured via APEX_DATABASE_URL or DATABASE_URL."
        )
    elif not db_report.connected:
        conditions.append(f"External PostgreSQL connection failed: {db_report.error}")

    if not sandbox_report.arbitrary_code_execution_ready:
        conditions.append(
            "External isolated microVM sandbox provider is not attested; arbitrary untrusted agent code execution remains blocked by policy."
        )

    if container.settings.arena_isolated_mode:
        conditions.append(
            "Arena runs inside an isolated sandbox; Hybrid Local Node is verified via protocol/unit tests and is NOT connected to the user's physical computer."
        )

    return {
        "verdict": verdict,
        "environment_profile": container.settings.env,
        "demo_seed_active": container.platform.demo_seeded,
        "docker_required_for_dev": False,
        "apt_required_for_dev": False,
        "env_var_presence": env_presence,
        "postgresql": asdict(db_report),
        "redis": asdict(cache_report),
        "rate_limiter": container.rate_limiter.telemetry_snapshot(),
        "persistence_and_concurrency": {
            "authoritative_store": (
                "postgresql_rls_occ"
                if container.platform_persistence.active
                else "single_instance_local_checkpoint"
            ),
            "optimistic_concurrency_control": True,
            "idempotency_ledger_enabled": True,
            "atomic_fsync_checkpointing": True,
            "multi_instance_safe": bool(container.platform_persistence.active),
            "ephemeral_shared_state": (
                "redis" if cache_report.connected else "process_local"
            ),
            "architecture_note": (
                "PostgreSQL with RLS + version_num OCC is authoritative across replicas."
                if container.platform_persistence.active
                else "Single-instance mode: local atomic checkpoint active; configure APEX_DATABASE_URL for multi-instance horizontal scaling."
            ),
        },
        "worker_outbox": {
            "mode": "postgresql_transactional_outbox",
            "daemon_assumed": False,
            "docker_required": False,
            "features": [
                "durable_workflow_persistence",
                "for_update_skip_locked",
                "exponential_backoff_retry",
                "idempotency_keys",
                "stale_lease_recovery",
                "worker_heartbeat",
                "cooperative_cancellation",
            ],
        },
        "experiment_sandbox": sandbox_report.model_dump(),
        "hybrid_local_node": local_node_report,
        "conditions": conditions,
    }
