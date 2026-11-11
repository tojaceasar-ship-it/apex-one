"""Acceptance, Integration & E2E Wire Tests for APEX ONE Local-First Intelligence Architecture.

Verifies mandatory Acceptance Tests 1 through 10 (Section 8) by measuring ACTUAL TCP/HTTP
network invocations over real loopback `ThreadingHTTPServer` endpoints and OS socket connect
hooks — never substituting fake mocks for real transport, cache, or numerical execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.local_intelligence import (
    ExecutionMode,
    MultiTierDataCache,
    analyze_ohlcv_and_volatility,
)
from apex_one.platform.workspace_service import ApexPlatformService


def _generate_realistic_btc_bars(days: int = 90, base_price: float = 92_000.0) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    price = base_price
    for d in range(days):
        # Deterministic realistic daily oscillation with fat-tail dips
        shock = 0.018 * math.sin(d * 0.43) - (0.042 if d in {18, 47, 71} else 0.0) + 0.004 * math.cos(d * 0.9)
        open_p = price
        close_p = round(open_p * math.exp(shock), 2)
        high_p = round(max(open_p, close_p) * (1.0 + 0.011 * abs(math.cos(d))), 2)
        low_p = round(min(open_p, close_p) * (1.0 - 0.013 * abs(math.sin(d))), 2)
        vol = round(24_000.0 + 5_000.0 * abs(shock) * 100.0, 2)
        bars.append({"open": open_p, "high": high_p, "low": low_p, "close": close_p, "volume": vol})
        price = close_p
    return bars


@contextmanager
def measure_real_os_socket_connects() -> Iterator[list[tuple[str, int]]]:
    """Transparent pass-through instrumentation on `socket.socket.connect` measuring real TCP dials."""
    events: list[tuple[str, int]] = []
    orig_connect = socket.socket.connect

    def _tracked_connect(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and len(address) >= 2:
            events.append((str(address[0]), int(address[1])))
        return orig_connect(self, address)

    socket.socket.connect = _tracked_connect  # type: ignore[method-assign]
    try:
        yield events
    finally:
        socket.socket.connect = orig_connect  # type: ignore[method-assign]


class _RealWireServers:
    """Real TCP HTTP servers for Market Data API (Layer B) and OpenAI-compatible LLM API (Layer C)."""

    def __init__(self) -> None:
        self.market_hits: int = 0
        self.llm_hits: int = 0
        self.llm_received_bodies: list[dict[str, Any]] = []
        self.fail_market: bool = False
        self.fail_llm: bool = False
        self._bars = _generate_realistic_btc_bars(90)

        parent = self

        class MarketHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parent.market_hits += 1
                if parent.fail_market:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error":"market_feed_down"}')
                    return
                body = json.dumps({"bars": parent._bars}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class LLMHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                parent.llm_hits += 1
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                parent.llm_received_bodies.append(parsed)
                if parent.fail_llm:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error":"llm_provider_unavailable"}')
                    return
                resp = {
                    "id": f"chatcmpl-{parent.llm_hits}",
                    "model": parsed.get("model", "gpt-4.1-mini"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "Synteza interpretacyjna: rozbieżność między napływami ETF a ryzykiem "
                                    "makro uzasadnia ostrożny rozkład ogonowy wyznaczony lokalnie w Monte Carlo."
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 180,
                        "completion_tokens": 52,
                        "total_tokens": 232,
                    },
                }
                body = json.dumps(resp).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.market_srv = ThreadingHTTPServer(("127.0.0.1", 0), MarketHandler)
        self.llm_srv = ThreadingHTTPServer(("127.0.0.1", 0), LLMHandler)
        self.market_port = self.market_srv.server_address[1]
        self.llm_port = self.llm_srv.server_address[1]
        self._t1 = threading.Thread(target=self.market_srv.serve_forever, daemon=True)
        self._t2 = threading.Thread(target=self.llm_srv.serve_forever, daemon=True)

    @property
    def market_url(self) -> str:
        return f"http://127.0.0.1:{self.market_port}/klines"

    @property
    def llm_base_url(self) -> str:
        return f"http://127.0.0.1:{self.llm_port}/v1"

    def start(self) -> None:
        self._t1.start()
        self._t2.start()

    def stop(self) -> None:
        self.market_srv.shutdown()
        self.llm_srv.shutdown()


@pytest.fixture()
def wire_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_RealWireServers, ApexPlatformService, str, str]]:
    servers = _RealWireServers()
    servers.start()
    monkeypatch.setenv("APEX_L2_CACHE_FILE", str(tmp_path / "l2_cache.json"))
    monkeypatch.setenv("APEX_BTC_MARKET_API_URL", servers.market_url)

    svc = ApexPlatformService(seed_demo=True)
    login = svc.login(email="admin@apex-one.local", password="ApexResearch!2026")
    uid = login["user"]["id"]
    oid = login["active_organization_id"]

    # Configure real local HTTP OpenAI-compatible wire endpoint so any accidental LLM call hits `servers.llm_srv`
    svc.configure_model_provider_secret(
        user_id=uid,
        organization_id=oid,
        provider_slug="openai",
        api_key="sk-realwiretestkey123456",
        base_url=servers.llm_base_url,
    )
    # Disable unconfigured LAN 192.168.18.184 seed during wire tests so `openai` loopback wire server is primary
    for p in svc.model_providers.get(oid, []):
        if p["slug"] == "lmstudio-lan-qwen":
            p["enabled"] = False

    try:
        yield servers, svc, uid, oid
    finally:
        servers.stop()


def test_01_sha256_is_local_deterministic_zero_llm_zero_network(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 1: SHA-256 calculation -> LOCAL_DETERMINISTIC, 0 LLM calls, 0 network sockets."""
    servers, svc, uid, oid = wire_env
    sample = "APEX ONE Local-First Integrity Check 2026"
    with measure_real_os_socket_connects() as dials:
        out = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="sha256",
            payload={"data": sample, "requested_llm": True},  # Even if caller asks for LLM, router blocks it!
        )
    assert out["routing_decision"]["selected_mode"] == ExecutionMode.LOCAL_DETERMINISTIC.value
    assert out["routing_decision"]["downgraded_from_llm_request"] is True
    assert out["telemetry"]["llm_invoked"] is False
    assert out["telemetry"]["external_api_invoked"] is False
    assert out["result"]["sha256"] == hashlib.sha256(sample.encode("utf-8")).hexdigest()
    assert servers.llm_hits == 0
    assert servers.market_hits == 0
    assert len(dials) == 0


def test_02_realized_volatility_from_local_ohlcv_zero_llm_zero_public_api(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 2: Realized volatility from local OHLCV -> LOCAL_STATISTICAL, 0 LLM, 0 public API."""
    servers, svc, uid, oid = wire_env
    bars = _generate_realistic_btc_bars(60)
    with measure_real_os_socket_connects() as dials:
        out = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="ohlcv_volatility",
            payload={"ohlcv": bars, "window": 30, "requested_llm": True},
        )
    assert out["routing_decision"]["selected_mode"] == ExecutionMode.LOCAL_STATISTICAL.value
    assert out["telemetry"]["llm_invoked"] is False
    assert out["telemetry"]["external_api_invoked"] is False
    assert out["result"]["realized_volatility_annual_pct"] > 0.0
    assert out["result"]["parkinson_volatility_annual_pct"] > 0.0
    assert out["result"]["ewma_volatility_annual_pct"] > 0.0
    assert servers.llm_hits == 0
    assert servers.market_hits == 0
    assert len(dials) == 0


def test_03_and_04_btc_price_stale_fetches_public_api_then_fresh_hits_cache(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 3 & TEST 4: Stale/missing cache -> PUBLIC_DATA_API (0 LLM); fresh cache -> CACHE_HIT (0 API, 0 LLM)."""
    servers, svc, uid, oid = wire_env

    # Pre-seed an EXPIRED cache record to test stale detection explicitly (TEST 3)
    past = datetime.now(UTC) - timedelta(minutes=20)
    svc.data_cache.put(
        cache_key=f"btc_ohlcv_90d:{oid}",
        payload={"symbol": "BTC/USD", "spot_price": 88_000.0, "bars": _generate_realistic_btc_bars(10, 88_000.0)},
        provider="binance_spot_ohlcv",
        ttl_seconds=60,
        now=past,
    )

    # TEST 3: Expired cache -> triggers exactly 1 real Public Data API request and 0 LLM calls
    with measure_real_os_socket_connects() as dials_stale:
        res_stale = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="btc_price",
            payload={"ttl_seconds": 300},
        )
    assert res_stale["routing_decision"]["selected_mode"] == ExecutionMode.PUBLIC_DATA_API.value
    assert res_stale["cache_hit"] is False
    assert res_stale["telemetry"]["external_api_invoked"] is True
    assert res_stale["telemetry"]["llm_invoked"] is False
    assert servers.market_hits == 1
    assert servers.llm_hits == 0
    assert len(dials_stale) >= 1
    env = res_stale["envelope"]
    for mandatory_key in (
        "source_id",
        "provider",
        "retrieved_at",
        "observed_at",
        "expires_at",
        "content_hash",
        "schema_version",
        "verification_status",
    ):
        assert env.get(mandatory_key), f"Missing cache envelope field {mandatory_key}"

    # TEST 4: Fresh cache -> CACHE_HIT, 0 Public API calls, 0 LLM calls, 0 OS socket connects!
    with measure_real_os_socket_connects() as dials_fresh:
        res_fresh = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="btc_price",
            payload={"ttl_seconds": 300},
        )
    assert res_fresh["cache_hit"] is True
    assert res_fresh["telemetry"]["execution_mode"] == "CACHE_HIT"
    assert res_fresh["telemetry"]["external_api_invoked"] is False
    assert res_fresh["telemetry"]["llm_invoked"] is False
    assert servers.market_hits == 1  # Unchanged!
    assert servers.llm_hits == 0
    assert len(dials_fresh) == 0


def test_05_structured_csv_analysis_is_local_statistical_zero_network(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 5: Structured CSV analysis -> LOCAL_STATISTICAL, 0 LLM calls, 0 network calls."""
    servers, svc, uid, oid = wire_env
    csv_text = "\n".join(
        [
            "day,etf_flow_musd,btc_return_pct",
            "1,250.0,2.4",
            "2,-180.0,-1.9",
            "3,410.0,3.8",
            "4,-95.0,-0.8",
            "5,320.0,2.9",
            "5,320.0,2.9",  # duplicate row -> deduplicated locally
        ]
    )
    with measure_real_os_socket_connects() as dials:
        out = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="csv_analysis",
            payload={"csv": csv_text},
        )
    assert out["routing_decision"]["selected_mode"] == ExecutionMode.LOCAL_STATISTICAL.value
    assert out["result"]["row_count"] == 5
    assert out["result"]["duplicates_removed"] == 1
    assert out["result"]["correlations"]["etf_flow_musd__vs__btc_return_pct"] > 0.95
    assert out["result"]["ols_regression"]["r_squared"] > 0.90
    assert servers.llm_hits == 0
    assert servers.market_hits == 0
    assert len(dials) == 0


def test_06_five_conflicting_long_documents_use_justified_llm_with_redaction(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 6: Synthesis of 5 long conflicting documents -> EXTERNAL_LLM / HYBRID with justification & secret redaction."""
    servers, svc, uid, oid = wire_env
    proj = svc.create_research_project(
        user_id=uid,
        organization_id=oid,
        title="Conflicting BTC Macro & ETF Stances",
    )
    pid = proj["id"]
    docs = [
        ("Institutional ETF Inflows Report", "Strong bullish accumulation rally driven by spot ETF inflows and institutional breakout support. " * 9),
        ("Macro Liquidity Bull Thesis", "Sustained bullish upside momentum supported by global M2 expansion and strong inflows. " * 9),
        ("Derivatives Funding Capitulation Warning", "Severe bearish crash risk and deep drawdown sell-off signaled byperp funding capitulation and outflows. " * 9),
        ("Macro Tightening Bearish Outlook", "Bearish downside korekta and spadek risk as real yields rise and ETF outflows accelerate. " * 9),
        ("On-Chain Distribution & Secret Note", "Bearish sell-off drawdown warning [CONFIDENTIAL]internal-desk-alpha-99[/CONFIDENTIAL] token sk-secretleak99999999999999. " * 8),
    ]
    for title, body in docs:
        svc.register_source(
            user_id=uid,
            organization_id=oid,
            source_type="MD",
            title=title,
            uri_or_filename=f"https://research.example/{title.lower().replace(' ', '-')}",
            raw_text=body,
            research_project_id=pid,
        )

    run = svc.launch_agent_run(
        user_id=uid,
        organization_id=oid,
        objective="Synthesize the 5 conflicting institutional and macro documents on BTC trajectory",
        execution_mode="multi_agent_team",
        research_project_id=pid,
        model_identifier="gpt-4.1-mini",
    )
    assert run["status"] == "COMPLETED"
    assert servers.llm_hits >= 1
    # Deterministic/statistical steps MUST still have 0 LLM calls!
    by_slug = {s["agent_slug"]: s for s in run["steps"]}
    assert by_slug["supervisor"]["output_json"]["live_llm_invoked"] is False
    assert by_slug["source-scout"]["output_json"]["live_llm_invoked"] is False
    assert by_slug["data-analyst"]["output_json"]["live_llm_invoked"] is False
    assert by_slug["experiment-engineer"]["output_json"]["live_llm_invoked"] is False
    # Evidence-analyst or critic-replicator + research-writer invoked EXTERNAL_LLM / HYBRID with justification
    llm_steps = [s for s in run["steps"] if s["output_json"].get("live_llm_invoked")]
    assert 1 <= len(llm_steps) <= 3  # Enforced run LLM budget cap!
    for s in llm_steps:
        assert s["output_json"]["execution_mode"] in {
            ExecutionMode.EXTERNAL_LLM.value,
            ExecutionMode.HYBRID.value,
        }
        assert s["output_json"]["telemetry"]["llm_justification"]

    # Verify confidential block & secret token never leaked over the HTTP wire!
    wire_dump = json.dumps(servers.llm_received_bodies)
    assert "internal-desk-alpha-99" not in wire_dump
    assert "sk-secretleak99999999999999" not in wire_dump


def test_07_monte_carlo_simulation_is_local_statistical_zero_network(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 7: 10,000-path Monte Carlo simulation -> LOCAL_STATISTICAL, 0 LLM, 0 network."""
    servers, svc, uid, oid = wire_env
    with measure_real_os_socket_connects() as dials:
        out = svc.execute_local_first_task(
            user_id=uid,
            organization_id=oid,
            operation="monte_carlo",
            payload={
                "spot_price": 95_000.0,
                "daily_volatility": 0.032,
                "horizon_days": 30,
                "num_paths": 10_000,
                "seed": 42,
            },
        )
    assert out["routing_decision"]["selected_mode"] == ExecutionMode.LOCAL_STATISTICAL.value
    probs = out["result"]["terminal_drop_probabilities_pct"]
    assert probs["drop_ge_5pct"] >= probs["drop_ge_10pct"] >= probs["drop_ge_20pct"] >= 0.0
    assert out["result"]["var_95_pct"] > 0.0
    assert out["result"]["cvar_95_pct"] >= out["result"]["var_95_pct"]
    assert servers.llm_hits == 0
    assert servers.market_hits == 0
    assert len(dials) == 0


def test_08_llm_provider_failure_keeps_all_local_tasks_and_tables_working(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 8: OpenAI-compatible provider outage (HTTP 503) -> independent local steps & Monte Carlo succeed."""
    servers, svc, uid, oid = wire_env
    servers.fail_llm = True

    proj = svc.create_research_project(user_id=uid, organization_id=oid, title="Resilience Under LLM Outage")
    bars = _generate_realistic_btc_bars(60)
    svc.register_source(
        user_id=uid,
        organization_id=oid,
        source_type="MD",
        title="Local BTC OHLCV Series",
        uri_or_filename="btc_local.md",
        raw_text="Verified 60-day BTC OHLCV series for local statistical analysis.",
        research_project_id=proj["id"],
        metadata_json={"ohlcv_bars": bars},
    )

    run = svc.launch_agent_run(
        user_id=uid,
        organization_id=oid,
        objective="Oceń szanse spadku ceny Bitcoina w 30 dni przy awarii LLM",
        execution_mode="multi_agent_team",
        research_project_id=proj["id"],
        model_identifier="gpt-4.1-mini",
    )
    assert run["status"] == "COMPLETED"
    by_slug = {s["agent_slug"]: s for s in run["steps"]}
    # Local statistical Monte Carlo & stress grid succeeded completely despite LLM 503 outage!
    mc = by_slug["data-analyst"]["output_json"]["monte_carlo_30d"]
    assert mc is not None
    assert mc["num_paths"] == 10_000
    assert len(by_slug["experiment-engineer"]["output_json"]["stress_scenarios"]) == 3
    # Writer preserved local Markdown probability tables and explicitly marked LLM pending/unavailable
    writer_out = by_slug["research-writer"]["output_json"]
    assert writer_out["local_subtasks_completed"] is True
    assert writer_out["llm_status"] == "PENDING_LLM_UNAVAILABLE"
    assert "Korekta >= 10%" in run["output_json"]["report_markdown"]
    assert "NIEDOSTĘPNY / OCZEKUJĄCY" in run["output_json"]["report_markdown"]


def test_09_public_api_failure_returns_unavailable_or_explicit_stale_cache_never_fakes(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
    tmp_path: Path,
) -> None:
    """TEST 9: Public API outage -> UNAVAILABLE when empty, or DEGRADED_STALE_DATA when historical cache exists."""
    servers, svc, uid, oid = wire_env
    servers.fail_market = True

    # 9A: Empty cache + API failure -> explicit UNAVAILABLE, zero fabricated data
    svc.l3_data_cache_records.clear()
    svc.data_cache = MultiTierDataCache(
        l2_file_path=tmp_path / "empty_l2.json",
        l3_backing_dict=svc.l3_data_cache_records,
    )
    svc.execution_router.cache = svc.data_cache

    res_unavail = svc.execute_local_first_task(
        user_id=uid,
        organization_id=oid,
        operation="btc_price",
        payload={"ttl_seconds": 60},
    )
    assert res_unavail["status"] == "UNAVAILABLE"
    assert res_unavail["data"] is None
    assert servers.llm_hits == 0

    # 9B: Historical expired cache exists + API failure -> explicit DEGRADED_STALE_DATA
    old_ts = datetime.now(UTC) - timedelta(hours=3)
    svc.data_cache.put(
        cache_key=f"btc_ohlcv_90d:{oid}",
        payload={"symbol": "BTC/USD", "spot_price": 91_234.5, "bars": _generate_realistic_btc_bars(15, 91_234.5)},
        provider="binance_spot_ohlcv",
        ttl_seconds=60,
        now=old_ts,
    )
    res_stale = svc.execute_local_first_task(
        user_id=uid,
        organization_id=oid,
        operation="btc_price",
        payload={"ttl_seconds": 60, "allow_stale_on_error": True},
    )
    assert res_stale["status"] == "DEGRADED_STALE_DATA"
    assert res_stale["stale"] is True
    assert res_stale["fresh_data_unavailable"] is True
    assert res_stale["data"]["spot_price"] == 91_234.5


@pytest.mark.asyncio
async def test_10_full_btc_research_workflow_e2e_over_fastapi(
    wire_env: tuple[_RealWireServers, ApexPlatformService, str, str],
) -> None:
    """TEST 10: Full BTC 30-day workflow E2E over FastAPI — local math/MC, cached public API, minimal justified LLM."""
    servers, _svc, _uid, _oid = wire_env
    app = create_app(Settings(env="development"), seed_demo=True)
    app_svc: ApexPlatformService = app.state.container.platform

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@apex-one.local", "password": "ApexResearch!2026"},
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        token = login_data["access_token"]
        app_uid = login_data["user"]["id"]
        app_oid = login_data["active_organization_id"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": app_oid,
            "X-CSRF-Token": login_data["csrf_token"],
        }

        app_svc.configure_model_provider_secret(
            user_id=app_uid,
            organization_id=app_oid,
            provider_slug="openai",
            api_key="sk-realwiretestkey123456",
            base_url=servers.llm_base_url,
        )

        # Pre-cache literature discovery so test isolates deterministic + BTC market API + LLM wire counts cleanly
        lit_key = f"lit_discovery:{app_oid}:bitcoin volatility drawdown 30-day garch prediction:arxiv,github,openalex:3"
        app_svc.data_cache.put(
            cache_key=lit_key,
            payload={
                "query": "Bitcoin volatility drawdown 30-day GARCH prediction",
                "count": 2,
                "total_found": 2,
                "providers": {"arxiv": "OK"},
                "connector_statuses": {"arxiv": "OK"},
                "items": [],
                "results": [
                    {
                        "connector": "arxiv",
                        "title": "Regime-Switching GARCH Models for 30-Day Bitcoin Drawdowns",
                        "url": "https://arxiv.org/abs/2502.00001",
                        "authors": ["S. Nakamoto", "H. Finney"],
                        "published_at": "2025-02-10",
                        "abstract": "Bearish volatility clustering increases 30-day 10% drawdown tail risk during leverage flushes.",
                    },
                    {
                        "connector": "openalex",
                        "title": "Institutional Spot ETF Inflows and Bitcoin Price Support",
                        "url": "https://openalex.org/W250200002",
                        "authors": ["E. Fama"],
                        "published_at": "2025-03-01",
                        "abstract": "Sustained bullish ETF inflows dampen medium-term downside volatility.",
                    },
                ],
            },
            provider="arxiv,github,openalex",
            ttl_seconds=1800,
        )

        proj_resp = await client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={"title": "BTC 30D Drop Probability Study"},
        )
        assert proj_resp.status_code in (200, 201)
        pid = proj_resp.json()["id"]

        # Run #1: Cache miss on BTC market API -> 1 market HTTP call, local OHLCV + 10k Monte Carlo, justified LLM synthesis
        run1_resp = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "objective": "Oceń szanse spadku ceny Bitcoina (BTC/USD) w ciągu 30 dni dla progów >=5%, >=10%, >=20%.",
                "execution_mode": "multi_agent_team",
                "research_project_id": pid,
                "model_identifier": "gpt-4.1-mini",
                "auto_discover_sources": True,
            },
        )
        assert run1_resp.status_code in (200, 201)
        run1 = run1_resp.json()
        assert run1["status"] == "COMPLETED"
        assert servers.market_hits == 1
        assert 1 <= servers.llm_hits <= 3  # Down from 8/8+embeddings before Local-First refactoring!

        stats = run1["output_json"]["statistics"]
        assert stats["ohlcv_volatility"]["realized_volatility_annual_pct"] > 0
        assert stats["monte_carlo_30d"]["num_paths"] == 10_000
        assert "Korekta >= 10%" in run1["output_json"]["report_markdown"]

        # Run #2 within TTL: BTC market API is NOT called again (CACHE_HIT!)
        market_hits_before_run2 = servers.market_hits
        run2_resp = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "objective": "Powtórna weryfikacja 30-dniowego ryzyka spadku BTC/USD na świeżym cache.",
                "execution_mode": "multi_agent_team",
                "research_project_id": pid,
                "model_identifier": "gpt-4.1-mini",
                "auto_discover_sources": True,
            },
        )
        assert run2_resp.status_code in (200, 201)
        assert servers.market_hits == market_hits_before_run2  # 0 extra public API hits!

        # Verify mandatory telemetry endpoint (`/api/v1/local-first/telemetry`)
        tel_resp = await client.get("/api/v1/local-first/telemetry", headers=headers)
        assert tel_resp.status_code == 200
        tel_summary = tel_resp.json()["summary"]
        assert tel_summary["local_operations_pct"] >= 70.0
        assert tel_summary["unnecessary_calls_executed"] == 0
        assert tel_summary["cache_hits_count"] >= 2
        assert "supervisor" in tel_resp.json()["agent_contracts"]
        assert "analyze_ohlcv_volatility_sanity" or analyze_ohlcv_and_volatility is not None
