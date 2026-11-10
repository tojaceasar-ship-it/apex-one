"""API tests for Asynchronous Background DAG Runs, Live SSE Streaming,
Runtime Provider Secrets, and SSRF-Guarded Source Fetching."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from apex_one.api.main import create_app
from apex_one.config import Settings


@pytest.mark.asyncio
async def test_async_background_run_live_sse_and_midflight_cancel() -> None:
    app = create_app(Settings(env="development"), seed_demo=True)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@apex-one.local", "password": "ApexResearch!2026"},
        )
        assert r_login.status_code == 200
        tok = r_login.json()["access_token"]
        headers = {"X-Apex-Session-Token": tok}

        # 1. Launch async background 8-agent run -> returns 201 immediately in RUNNING state
        r_async = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "objective": "Asynchronous multi-agent verification with live SSE stream",
                "execution_mode": "multi_agent_team",
                "async_execution": True,
            },
        )
        assert r_async.status_code == 201
        run_id = r_async.json()["id"]

        # 2. Stream live SSE events until completion
        r_stream = await client.get(f"/api/v1/runs/{run_id}/stream", headers=headers)
        assert r_stream.status_code == 200
        body_text = r_stream.text
        assert "event: step" in body_text
        assert "event: complete" in body_text

        # Verify run finalized all 8 specialist steps
        r_runs = await client.get("/api/v1/runs", headers=headers)
        completed = next(x for x in r_runs.json()["items"] if x["id"] == run_id)
        assert completed["status"] == "COMPLETED"
        assert len(completed["steps"]) == 8

        # 3. Launch second async run and cancel mid-flight
        r_cancel_target = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "objective": "Long run to cancel mid-execution",
                "execution_mode": "multi_agent_team",
                "async_execution": True,
            },
        )
        cancel_id = r_cancel_target.json()["id"]
        await asyncio.sleep(0.05)
        r_cancel = await client.post(
            f"/api/v1/runs/{cancel_id}/cancel", headers=headers
        )
        assert r_cancel.status_code == 200
        assert r_cancel.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_provider_secret_binding_and_ssrf_url_rejection() -> None:
    app = create_app(Settings(env="development"), seed_demo=True)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@apex-one.local", "password": "ApexResearch!2026"},
        )
        tok = r_login.json()["access_token"]
        headers = {"X-Apex-Session-Token": tok}

        # Configure runtime secret -> never echoes raw key
        r_sec = await client.post(
            "/api/v1/models/providers/openai/secret",
            headers=headers,
            json={"api_key": "sk-super-secret-do-not-echo"},
        )
        assert r_sec.status_code == 200
        assert r_sec.json()["credentials_present"] is True
        assert "sk-super-secret-do-not-echo" not in r_sec.text

        # Clear secret and test local diagnostic fallback
        await client.post(
            "/api/v1/models/providers/openai/secret",
            headers=headers,
            json={"api_key": ""},
        )
        r_probe = await client.post(
            "/api/v1/models/test-inference",
            headers=headers,
            json={"model_identifier": "apex-analytical-local-v1", "prompt": "Ping"},
        )
        assert r_probe.status_code == 200
        assert r_probe.json()["fallback_triggered"] is True

        # SSRF attempt against cloud metadata endpoint is rejected with 400 SSRF_BLOCKED
        r_ssrf = await client.post(
            "/api/v1/sources/fetch-url",
            headers=headers,
            json={"url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert r_ssrf.status_code == 400
        assert r_ssrf.json()["error"]["code"] == "SSRF_BLOCKED"
