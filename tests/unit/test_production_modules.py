"""Unit & local-wire tests for Full-Working Production Modules 2, 3, and 5:
- Multi-Provider LLM & Embedding Gateway (`llm_gateway.py`) over real HTTP loopback server
- Scientific Connectors & SSRF-Safe URL Extractor (`scientific_connectors.py`)
- Capability-Guarded Hybrid Local Node Task Executor (`apex_local_node/executor.py`)
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from apex_local_node.executor import LocalNodePolicy, LocalNodeTaskExecutor
from apex_one.platform.llm_gateway import LLMGateway
from apex_one.platform.scientific_connectors import (
    SSRFBlockedError,
    parse_arxiv_atom_xml,
    reconstruct_openalex_abstract,
    validate_public_http_url,
)
from apex_one.platform.workspace_service import ApexPlatformService
from apex_one.sandbox.adapter import (
    RootlessContainerSandboxAdapter,
    UntrustedExecutionBlockedError,
)
from apex_one.sandbox.contracts import SandboxJob


class _MockLLMWireHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))

        if "/fail/" in self.path:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error":"upstream overloaded"}')
            return

        if self.path.endswith("/chat/completions"):
            req_model = str(body.get("model", "gpt-4.1-mini"))
            msg_content = (
                "<think>\nAnalyzing methodology...\n</think>\nQwen3.6 LAN synthesis confirmed."
                if "qwen3.6" in req_model
                else "Empirical verification confirmed across primary sources."
            )
            resp = {
                "id": "chatcmpl-live-1",
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": msg_content,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 45,
                    "total_tokens": 165,
                },
            }
            raw = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if self.path.endswith("/messages"):
            resp = {
                "id": "msg-anthropic-1",
                "content": [{"type": "text", "text": "Anthropic synthesis verified."}],
                "usage": {"input_tokens": 90, "output_tokens": 30},
            }
            raw = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)
            return

        if self.path.endswith("/embeddings"):
            vec = [0.25] * 1536
            resp = {"data": [{"embedding": vec}]}
            raw = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_response(404)
        self.end_headers()


def test_llm_gateway_live_http_fallback_and_embeddings() -> None:
    server = HTTPServer(("127.0.0.1", 0), _MockLLMWireHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        gw = LLMGateway()
        org_id = "org-llm-test"
        catalog = [
            {
                "slug": "broken-primary",
                "category": "cloud",
                "base_url": f"http://127.0.0.1:{port}/fail",
                "allow_dev_loopback": True,
                "auth_env_var": "NONEXISTENT_ENV_1",
                "fallback_provider_slug": "openai",
                "enabled": True,
                "models": [{"id": "gpt-4.1-mini"}],
            },
            {
                "slug": "openai",
                "category": "cloud",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "allow_dev_loopback": True,
                "auth_env_var": "NONEXISTENT_ENV_2",
                "fallback_provider_slug": "anthropic",
                "enabled": True,
                "models": [{"id": "gpt-4.1-mini"}],
            },
            {
                "slug": "anthropic",
                "category": "cloud",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "allow_dev_loopback": True,
                "auth_env_var": "NONEXISTENT_ENV_3",
                "enabled": True,
                "models": [{"id": "claude-sonnet-4-0"}],
            },
        ]

        gw.set_runtime_secret(
            organization_id=org_id, provider_slug="broken-primary", api_key="sk-broken"
        )
        gw.set_runtime_secret(
            organization_id=org_id, provider_slug="openai", api_key="sk-live-test"
        )
        gw.set_runtime_secret(
            organization_id=org_id, provider_slug="anthropic", api_key="sk-ant-test"
        )

        # 1. Primary fails with 503 -> automatically falls back to `openai`
        res = gw.complete_sync(
            organization_id=org_id,
            providers_catalog=catalog,
            preferred_model="broken-primary/gpt-4.1-mini",
            system_prompt="System",
            user_prompt="Verify claims",
        )
        assert res is not None
        assert res.live_llm_invoked is True
        assert res.provider_slug == "openai"
        assert res.fallback_triggered is True
        assert res.total_tokens == 165
        assert "Empirical verification confirmed" in res.content

        # 2. Anthropic native `/messages` protocol
        ant = gw.complete_sync(
            organization_id=org_id,
            providers_catalog=catalog,
            preferred_model="claude-sonnet-4-0",
            system_prompt="System",
            user_prompt="Synthesize",
        )
        assert ant is not None
        assert ant.provider_slug == "anthropic"
        assert "Anthropic synthesis verified." in ant.content

        # 3. Live 1536-d embeddings normalized to unit norm
        emb, backend = gw.embed_text_1536_with_live_provider(
            "pgvector semantic retrieval",
            organization_id=org_id,
            providers_catalog=catalog,
        )
        assert len(emb) == 1536
        assert backend.startswith("openai:")
        norm_sq = sum(v * v for v in emb)
        assert abs(norm_sq - 1.0) < 1e-4

        # 4. End-to-end orchestration run invoking live LLM per specialist agent step
        svc = ApexPlatformService(seed_demo=True)
        login = svc.login(email="admin@apex-one.local", password="ApexResearch!2026")
        oid = login["active_organization_id"]
        uid = login["user"]["id"]
        svc.configure_model_provider_secret(
            user_id=uid,
            organization_id=oid,
            provider_slug="openai",
            api_key="sk-live-orch",
            base_url=f"http://127.0.0.1:{port}/v1",
            allow_dev_loopback=True,
        )
        run = svc.launch_agent_run(
            user_id=uid,
            organization_id=oid,
            objective="Evaluate multi-agent RAG citation verification",
            execution_mode="multi_agent_team",
            model_identifier="gpt-4.1-mini",
        )
        assert run["status"] == "COMPLETED"
        assert len(run["steps"]) == 8
        # Local-First architecture: deterministic/statistical agents do NOT waste LLM calls;
        # semantic critique/writer steps invoke LLM with explicit justification.
        by_agent = {s["agent_slug"]: s for s in run["steps"]}
        assert by_agent["supervisor"]["output_json"]["live_llm_invoked"] is False
        assert by_agent["source-scout"]["output_json"]["live_llm_invoked"] is False
        assert by_agent["data-analyst"]["output_json"]["live_llm_invoked"] is False
        assert by_agent["experiment-engineer"]["output_json"]["live_llm_invoked"] is False
        assert by_agent["research-writer"]["output_json"]["live_llm_invoked"] is True
        assert run["local_first_telemetry"]["local_operations_pct"] >= 75.0
        assert "Synteza Modelu (" in run["output_json"]["report_markdown"]
        assert "gpt-4.1-mini)" in run["output_json"]["report_markdown"]
    finally:
        server.shutdown()


def test_ssrf_guard_and_scientific_parsers() -> None:
    with pytest.raises(SSRFBlockedError):
        validate_public_http_url("http://127.0.0.1:8000/api/v1/health")
    with pytest.raises(SSRFBlockedError):
        validate_public_http_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFBlockedError):
        validate_public_http_url("file:///etc/passwd")

    abstract = reconstruct_openalex_abstract(
        {"Empirical": [0], "verification": [1], "improves": [2], "precision.": [3]}
    )
    assert abstract == "Empirical verification improves precision."

    sample_atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.12345v1</id>
        <published>2025-01-15T00:00:00Z</published>
        <title>Multi-Agent Verification for Scientific Discovery</title>
        <summary>We evaluate specialist critic agents against single-agent baselines.</summary>
        <author><name>Ada Lovelace</name></author>
      </entry>
    </feed>"""
    parsed = parse_arxiv_atom_xml(sample_atom)
    assert len(parsed) == 1
    assert parsed[0]["connector"] == "arxiv"
    assert parsed[0]["authors"] == ["Ada Lovelace"]
    assert "Multi-Agent Verification" in parsed[0]["title"]


def test_local_node_capability_guarded_executor(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "workspace"
    allowed_dir.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("top-secret", encoding="utf-8")
    inside_doc = allowed_dir / "notes.md"
    inside_doc.write_text("# Field Notes\nVerified observation.", encoding="utf-8")

    # Read-only policy blocks writes and path traversal
    ro_exec = LocalNodeTaskExecutor(
        LocalNodePolicy(
            allowed_directories=(allowed_dir,),
            can_read=True,
            can_write=False,
        )
    )
    listed = ro_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "fs.list_directory", "params": {"path": str(allowed_dir)}}
    )
    assert listed["result"]["entries"][0]["name"] == "notes.md"

    read_res = ro_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "fs.read_file", "params": {"path": str(inside_doc)}}
    )
    assert "Verified observation" in read_res["result"]["content"]
    assert len(read_res["result"]["checksum_sha256"]) == 64

    # Path traversal outside allowed_directories is blocked
    traversal = ro_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "fs.read_file", "params": {"path": str(allowed_dir / ".." / "secret.txt")}}
    )
    assert "error" in traversal
    assert traversal["error"]["code"] == -32001

    # Write blocked when can_write=False
    denied_write = ro_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 4, "method": "fs.write_report", "params": {"path": str(allowed_dir / "out.md"), "content": "hi"}}
    )
    assert "error" in denied_write

    # Write permitted for .md when can_write=True, but executable .sh is still blocked
    rw_exec = LocalNodeTaskExecutor(
        LocalNodePolicy(
            allowed_directories=(allowed_dir,),
            can_read=True,
            can_write=True,
        )
    )
    ok_write = rw_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 5, "method": "fs.write_report", "params": {"path": str(allowed_dir / "report.md"), "content": "# Output"}}
    )
    assert ok_write["result"]["bytes_written"] == 8

    bad_ext = rw_exec.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 6, "method": "fs.write_report", "params": {"path": str(allowed_dir / "exploit.sh"), "content": "rm -rf /"}}
    )
    assert "error" in bad_ext


@pytest.mark.asyncio
async def test_rootless_container_sandbox_command_hardening_and_attestation_gate(
    tmp_path: Path,
) -> None:
    job = SandboxJob(
        tenant_id=uuid.uuid4(),
        runtime="python3.11",
        entrypoint=["python3", "run_exp.py"],
    )

    unattested = RootlessContainerSandboxAdapter(
        isolation_attested=False, runtime_binary="/usr/bin/podman"
    )
    assert unattested.isolation_verified is False
    with pytest.raises(UntrustedExecutionBlockedError):
        await unattested.execute(job)

    attested = RootlessContainerSandboxAdapter(
        isolation_attested=True, runtime_binary="/usr/bin/podman"
    )
    assert attested.isolation_verified is True
    cmd = attested.build_container_command(job, tmp_path)
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "--user=65534:65534" in cmd


def test_lmstudio_lan_qwen36_provider_and_think_tag_stripping() -> None:
    svc = ApexPlatformService(seed_demo=True)
    login = svc.login(email="admin@apex-one.local", password="ApexResearch!2026")
    uid = login["user"]["id"]
    oid = login["active_organization_id"]

    providers = svc.list_model_providers(user_id=uid, organization_id=oid)
    lmstudio = next(p for p in providers if p["slug"] == "lmstudio-lan-qwen")
    assert lmstudio["base_url"] == "http://192.168.18.184:1234/v1"
    assert lmstudio["credentials_present"] is True
    assert (
        lmstudio["models"][0]["id"]
        == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    )

    # Point base_url to local wire mock server to verify keyless LM Studio + <think> stripping
    server = HTTPServer(("127.0.0.1", 0), _MockLLMWireHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        svc.configure_model_provider_secret(
            user_id=uid,
            organization_id=oid,
            provider_slug="lmstudio",
            api_key="",
            base_url=f"http://127.0.0.1:{port}/v1",
        )
        res = svc.test_model_provider_inference(
            user_id=uid,
            organization_id=oid,
            model_identifier="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            prompt="Synthesize evidence.",
        )
        assert res["live_llm_invoked"] is True
        assert res["provider_slug"] == "lmstudio-lan-qwen"
        assert res["model_identifier"] == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
        assert res["cost_usd"] == 0.0
        assert "<think>" not in res["content"]
        assert "Qwen3.6 LAN synthesis confirmed." in res["content"]
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_reverse_lan_bridge_relays_arena_preview_to_local_qwen36() -> None:
    import asyncio

    from apex_one.platform.lan_llm_bridge import build_lan_bridge_python_script

    script_src = build_lan_bridge_python_script(
        arena_base_url="https://5173-demo.e2b.app",
        auth_token="apxsess_testtoken",
        organization_id="org-1",
    )
    # Verify generated standalone Python bridge has zero syntax errors
    compile(script_src, "apex_lan_bridge.py", "exec")
    assert "http://192.168.18.184:1234/v1" in script_src
    assert "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive" in script_src

    svc = ApexPlatformService(seed_demo=True)
    login = svc.login(email="admin@apex-one.local", password="ApexResearch!2026")
    uid = login["user"]["id"]
    oid = login["active_organization_id"]

    # Register LAN bridge presence via initial short poll
    initial = await svc.llm_gateway.lan_bridge.poll_next_job_async(
        organization_id=oid,
        endpoint="http://192.168.18.184:1234/v1",
        models=["qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"],
        bridge_host="workstation-lan",
        wait_seconds=0.05,
    )
    assert initial is None
    assert svc.llm_gateway.lan_bridge.is_online(oid) is True

    providers = svc.list_model_providers(user_id=uid, organization_id=oid)
    lmstudio = next(p for p in providers if p["slug"] == "lmstudio-lan-qwen")
    assert lmstudio["health_status"] == "BRIDGE_ONLINE_LAN"

    async def _simulated_user_pc_bridge() -> None:
        job = await svc.llm_gateway.lan_bridge.poll_next_job_async(
            organization_id=oid,
            endpoint="http://192.168.18.184:1234/v1",
            models=["qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"],
            bridge_host="workstation-lan",
            wait_seconds=5.0,
        )
        assert job is not None
        assert job["model"] == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
        svc.llm_gateway.lan_bridge.complete_job(
            organization_id=oid,
            job_id=job["job_id"],
            result_payload={
                "content": "<think>Estimating 30d volatility...</think>Szacowane prawdopodobieństwo spadku BTC >= 10% w 30 dni wynosi 38%.",
                "model": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                "prompt_tokens": 180,
                "completion_tokens": 64,
                "total_tokens": 244,
                "latency_ms": 420,
            },
        )

    bridge_task = asyncio.create_task(_simulated_user_pc_bridge())
    infer_res = await asyncio.to_thread(
        svc.test_model_provider_inference,
        user_id=uid,
        organization_id=oid,
        model_identifier="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        prompt="Oceń szanse spadku Bitcoina w ciągu 30 dni.",
    )
    await bridge_task

    assert infer_res["live_llm_invoked"] is True
    assert infer_res["provider_slug"] == "lmstudio-lan-qwen"
    assert "<think>" not in infer_res["content"]
    assert "38%" in infer_res["content"]


def test_local_checkpoint_roundtrip_preserves_sessions_and_audit(tmp_path: Path) -> None:
    ckpt = tmp_path / "apex_state.json"
    svc = ApexPlatformService(seed_demo=True)
    login = svc.login(email="admin@apex-one.local", password="ApexResearch!2026")
    token = login["access_token"]
    oid = login["active_organization_id"]
    svc.save_local_checkpoint(ckpt)

    restored = ApexPlatformService(seed_demo=False)
    assert restored.load_local_checkpoint(ckpt) is True
    ctx = restored.authenticate_token(token)
    assert ctx["user"]["email"] == "admin@apex-one.local"
    assert restored.audit.verify_chain(oid)["valid"] is True




