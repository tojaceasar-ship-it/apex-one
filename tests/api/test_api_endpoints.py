"""Suite B — API tests using test-only doubles strictly isolated to `tests/fakes/`."""

from __future__ import annotations

import uuid

import httpx
import pytest

from apex_one.api.dependencies import get_vector_store, get_workflow_store
from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.db.models import DEFAULT_EMBEDDING_DIM
from apex_one.db.vector import VectorSimilarityHit
from tests.fakes.repositories import FakeVectorStore, FakeWorkflowStore

pytestmark = pytest.mark.api


async def test_health_and_honest_arena_environment_status() -> None:
    cfg = Settings(
        env="development",
        apex_database_url=None,
        database_url=None,
        arena_isolated_mode=True,
    )
    app = create_app(settings=cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_health = await client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.json()["docker_required"] is False

        r_env = await client.get("/api/v1/environment/status")
        assert r_env.status_code == 200
        body = r_env.json()
        assert body["verdict"] == "CONDITIONAL"
        assert body["docker_required_for_dev"] is False
        assert body["postgresql"]["configured"] is False
        assert body["postgresql"]["connected"] is False
        assert body["experiment_sandbox"]["arbitrary_code_execution_ready"] is False
        assert body["hybrid_local_node"]["user_machine_connected"] is False


async def test_unconfigured_postgres_returns_503_without_sqlite_fallback() -> None:
    cfg = Settings(env="development", apex_database_url=None, database_url=None)
    app = create_app(settings=cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workflows",
            headers={"X-Tenant-Id": str(uuid.uuid4())},
            json={
                "workflow_type": "literature_review",
                "idempotency_key": "idem-api-503",
                "input_payload": {"topic": "quantum error correction"},
            },
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "POSTGRESQL_NOT_CONFIGURED"


async def test_workflow_lifecycle_and_tenant_isolation_with_test_doubles() -> None:
    cfg = Settings(env="development")
    app = create_app(settings=cfg)
    fake_wf = FakeWorkflowStore()
    app.dependency_overrides[get_workflow_store] = lambda: fake_wf

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create workflow
        r1 = await client.post(
            "/api/v1/workflows",
            headers={"X-Tenant-Id": tenant_a},
            json={
                "workflow_type": "hypothesis_evaluation",
                "idempotency_key": "wf-idem-100",
                "input_payload": {"hypothesis": "H1"},
            },
        )
        assert r1.status_code == 201
        assert r1.json()["created"] is True
        wf_id = r1.json()["workflow"]["id"]

        # Duplicate idempotency key returns same workflow with created=False
        r2 = await client.post(
            "/api/v1/workflows",
            headers={"X-Tenant-Id": tenant_a},
            json={
                "workflow_type": "hypothesis_evaluation",
                "idempotency_key": "wf-idem-100",
                "input_payload": {"hypothesis": "H1"},
            },
        )
        assert r2.status_code == 201
        assert r2.json()["created"] is False
        assert r2.json()["workflow"]["id"] == wf_id

        # Cross-tenant access denied (404)
        r_cross = await client.get(
            f"/api/v1/workflows/{wf_id}",
            headers={"X-Tenant-Id": tenant_b},
        )
        assert r_cross.status_code == 404

        # Cancel workflow
        r_cancel = await client.post(
            f"/api/v1/workflows/{wf_id}/cancel",
            headers={"X-Tenant-Id": tenant_a},
        )
        assert r_cancel.status_code == 200
        assert r_cancel.json()["workflow"]["status"] == "CANCELLED"


async def test_sandbox_api_validates_contracts_and_blocks_unisolated_execution() -> None:
    cfg = Settings(env="development", sandbox_provider="none")
    app = create_app(settings=cfg)
    tenant = str(uuid.uuid4())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_val = await client.post(
            "/api/v1/sandbox/jobs/validate",
            headers={"X-Tenant-Id": tenant},
            json={
                "runtime": "python3.11",
                "entrypoint": ["python3", "sim.py"],
            },
        )
        assert r_val.status_code == 200
        assert r_val.json()["state"] == "VALIDATED"
        assert r_val.json()["isolation_verified"] is False

        r_exec = await client.post(
            "/api/v1/sandbox/jobs/execute",
            headers={"X-Tenant-Id": tenant},
            json={
                "runtime": "python3.11",
                "entrypoint": ["python3", "sim.py"],
            },
        )
        assert r_exec.status_code == 503
        assert r_exec.json()["detail"]["code"] == "UNTRUSTED_EXECUTION_BLOCKED"


async def test_knowledge_vector_search_endpoint_and_validation() -> None:
    cfg = Settings(env="development")
    app = create_app(settings=cfg)
    fake_vec = FakeVectorStore()
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    emb = [1.0] + [0.0] * (DEFAULT_EMBEDDING_DIM - 1)
    fake_vec.items.append(
        (
            tid,
            emb,
            VectorSimilarityHit(
                chunk_id=uuid.uuid4(),
                project_id=pid,
                source_uri="arxiv:2611.00001",
                content="Vector indexed research abstract",
                cosine_distance=0.0,
                similarity_score=1.0,
                metadata={"year": 2026},
            ),
        )
    )
    app.dependency_overrides[get_vector_store] = lambda: fake_vec

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Wrong dimension -> 422
        r_bad = await client.post(
            "/api/v1/knowledge/search",
            headers={"X-Tenant-Id": str(tid)},
            json={"query_vector": [0.1, 0.2]},
        )
        assert r_bad.status_code == 422

        # Valid 1536-d vector -> 200
        r_ok = await client.post(
            "/api/v1/knowledge/search",
            headers={"X-Tenant-Id": str(tid)},
            json={"query_vector": emb, "limit": 3},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["count"] == 1
        assert r_ok.json()["hits"][0]["source_uri"] == "arxiv:2611.00001"
