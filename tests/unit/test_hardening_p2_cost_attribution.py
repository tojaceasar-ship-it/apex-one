"""HARDENING 1.0 — Stage 7 (P2): Per-Call Cost Analytics & Token Attribution Tests.

Covers all 8 mandatory Stage 7 scenarios:
1. All 16 mandatory per-call fields populated on every invocation
2. Local LLM API calls (Ollama / LM Studio / Local Node) record `$0.00` external API cost while tracking local tokens
3. Cloud LLM known tariff vs upstream actual billed cost (`known_tariff_estimate` vs `known_actual`)
4. Unknown cloud model pricing returns `cost_status="unknown"` (`None` cost, `pricing_version="unknown"`) — zero fake default tariffs
5. Prompt-cache discount (`cached_tokens`) & cache-hit zero-cost deduplication (zero double counting)
6. Multi-provider / multi-model workflow rollup attributes each step accurately (never overwriting with `llm_tels[-1]`)
7. Strict separation of `cloud_llm_api`, `local_llm_api`, `public_data_api`, `infrastructure`, and `database` cost buckets
8. HTTP endpoint `GET /api/v1/analytics/cost-attribution` & tenant isolation
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.cost_attribution import (
    PRICING_CATALOG_VERSION,
    CostCategory,
    CostStatus,
    aggregate_workflow_cost_attribution,
    build_call_attribution_record,
    compute_call_cost_quote,
)
from apex_one.platform.workspace_service import ApexPlatformService

MANDATORY_16_FIELDS = {
    "execution_id",
    "workflow_run_id",
    "step_id",
    "agent_id",
    "provider",
    "model",
    "execution_mode",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "actual_cost_usd",
    "estimated_cost_usd",
    "pricing_version",
    "latency_ms",
    "request_id",
    "invocation_timestamp",
}


def test_01_all_sixteen_mandatory_fields_present_on_every_step_record() -> None:
    """1. Every executed workflow step produces a call attribution record containing all 16 mandatory fields."""
    svc = ApexPlatformService(seed_demo=True)
    run = next(
        r for r in svc.agent_runs.values() if r["execution_mode"] == "multi_agent_team"
    )
    recs = run["call_attribution_records"]
    assert len(recs) == 8
    for rec in recs:
        missing = MANDATORY_16_FIELDS - set(rec.keys())
        assert not missing, f"Missing mandatory fields: {missing}"
        assert rec["workflow_run_id"] == run["id"]
        assert rec["step_id"]
        assert rec["agent_id"]
        assert rec["execution_id"]
        assert rec["request_id"]
        assert rec["invocation_timestamp"]


def test_02_local_llm_models_record_zero_external_api_cost_and_preserve_tokens() -> None:
    """2. Ollama / LM Studio / Local Node calls record `$0.00` external API cost while tracking local tokens."""
    rec_lmstudio = build_call_attribution_record(
        workflow_run_id="wf-local-1",
        step_id="step-writer",
        agent_id="research-writer",
        provider="lmstudio-lan-qwen",
        model="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        execution_mode="LOCAL_MODEL",
        input_tokens=1400,
        output_tokens=600,
        llm_invoked=True,
        provider_cfg={"category": "local", "base_url": "http://192.168.18.184:1234/v1"},
    )
    assert rec_lmstudio.cost_category == CostCategory.LOCAL_LLM_API.value
    assert rec_lmstudio.cost_status == CostStatus.LOCAL_ZERO_API_COST.value
    assert rec_lmstudio.actual_cost_usd == 0.0
    assert rec_lmstudio.estimated_cost_usd == 0.0
    assert rec_lmstudio.total_tokens == 2000

    rec_ollama = build_call_attribution_record(
        workflow_run_id="wf-local-1",
        step_id="step-critic",
        agent_id="critic-replicator",
        provider="ollama-local",
        model="llama3.2:latest",
        execution_mode="LOCAL_MODEL",
        input_tokens=800,
        output_tokens=200,
        llm_invoked=True,
    )
    assert rec_ollama.cost_category == CostCategory.LOCAL_LLM_API.value
    assert rec_ollama.actual_cost_usd == 0.0
    assert rec_ollama.estimated_cost_usd == 0.0

    rollup = aggregate_workflow_cost_attribution(
        [rec_lmstudio, rec_ollama], workflow_run_id="wf-local-1"
    )
    assert rollup["known_combined_cost_usd"] == 0.0
    assert rollup["cost_breakdown_by_category"]["local_llm_api"]["api_cost_usd"] == 0.0
    assert rollup["cost_breakdown_by_category"]["local_llm_api"]["tokens"] == 3000


def test_03_cloud_llm_known_tariff_estimate_vs_upstream_actual_billing() -> None:
    """3. Known cloud tariff produces `estimated_cost_usd` (`actual=None`), while upstream billing produces `actual_cost_usd` (`estimated=None`)."""
    est_quote = compute_call_cost_quote(
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=2000,
        output_tokens=1000,
        cached_tokens=0,
        llm_invoked=True,
        provider_cfg={"category": "commercial_cloud"},
    )
    # gpt-4o-mini: 2k * 0.00015 + 1k * 0.00060 = 0.0003 + 0.0006 = 0.0009 USD
    assert est_quote.cost_category == CostCategory.CLOUD_LLM_API.value
    assert est_quote.cost_status == CostStatus.KNOWN_TARIFF_ESTIMATE.value
    assert est_quote.actual_cost_usd is None
    assert est_quote.estimated_cost_usd == 0.0009
    assert est_quote.pricing_version == PRICING_CATALOG_VERSION

    actual_quote = compute_call_cost_quote(
        provider="openrouter",
        model="openai/gpt-4o-mini",
        input_tokens=2000,
        output_tokens=1000,
        llm_invoked=True,
        upstream_actual_cost_usd=0.000912,
    )
    assert actual_quote.cost_status == CostStatus.KNOWN_ACTUAL.value
    assert actual_quote.actual_cost_usd == 0.000912
    assert actual_quote.estimated_cost_usd is None


def test_04_unknown_cloud_model_never_invents_fake_default_tariff() -> None:
    """4. Unrecognized commercial cloud model records `cost_status="unknown"` (`None` cost) instead of a fake `$0.0005/$0.0015` fallback."""
    unk_rec = build_call_attribution_record(
        workflow_run_id="wf-unk-1",
        step_id="s-1",
        agent_id="research-writer",
        provider="openai-compatible-cloud",
        model="mystery-unpriced-frontier-2027",
        execution_mode="EXTERNAL_LLM",
        input_tokens=5000,
        output_tokens=2000,
        llm_invoked=True,
        provider_cfg={"category": "commercial_cloud", "models": [{"id": "mystery-unpriced-frontier-2027"}]},
    )
    assert unk_rec.cost_status == CostStatus.UNKNOWN.value
    assert unk_rec.actual_cost_usd is None
    assert unk_rec.estimated_cost_usd is None
    assert unk_rec.pricing_version == "unknown"

    rollup = aggregate_workflow_cost_attribution([unk_rec], workflow_run_id="wf-unk-1")
    assert rollup["has_unknown_pricing"] is True
    assert rollup["unknown_cost_calls_count"] == 1
    assert rollup["known_combined_cost_usd"] == 0.0


def test_05_cached_tokens_discount_and_cache_hits_never_double_counted() -> None:
    """5. Prompt-cached tokens use discounted tariff; L1/L2/L3/idempotency cache hits & duplicate execution_ids are never double-counted."""
    # gpt-4o: input=0.0025/1k, cached_input=0.00125/1k, output=0.0100/1k
    # 2000 input with 1000 cached -> 1000*0.0025/1000 + 1000*0.00125/1000 + 500*0.0100/1000
    # = 0.0025 + 0.00125 + 0.0050 = 0.00875 USD (strictly less than uncached 0.0100 USD!)
    rec_live = build_call_attribution_record(
        execution_id="exec-unique-1",
        workflow_run_id="wf-cache",
        step_id="s-1",
        agent_id="evidence-analyst",
        provider="openai",
        model="gpt-4o",
        execution_mode="EXTERNAL_LLM",
        input_tokens=2000,
        output_tokens=500,
        cached_tokens=1000,
        llm_invoked=True,
    )
    assert rec_live.estimated_cost_usd == 0.00875

    # Full response cache hit -> $0.00 cost and 0 new API tokens!
    rec_cache_hit = build_call_attribution_record(
        execution_id="exec-cache-hit-2",
        workflow_run_id="wf-cache",
        step_id="s-2",
        agent_id="evidence-analyst",
        provider="openai",
        model="gpt-4o",
        execution_mode="EXTERNAL_LLM",
        input_tokens=2000,
        output_tokens=500,
        cache_hit=True,
        llm_invoked=False,
    )
    assert rec_cache_hit.cost_status == CostStatus.CACHE_HIT_ZERO_COST.value
    assert rec_cache_hit.actual_cost_usd == 0.0
    assert rec_cache_hit.estimated_cost_usd == 0.0
    assert rec_cache_hit.total_tokens == 0

    # Pass `rec_live` TWICE with same `execution_id` + `rec_cache_hit` -> rollup deduplicates `exec-unique-1`!
    rollup = aggregate_workflow_cost_attribution(
        [rec_live, rec_live, rec_cache_hit], workflow_run_id="wf-cache"
    )
    assert rollup["call_count"] == 2
    assert rollup["cache_hits_count"] == 1
    assert rollup["cached_tokens_total"] == 1000
    assert rollup["estimated_cost_usd_total"] == 0.00875


def test_06_multi_provider_workflow_attributes_each_step_without_last_step_overwrite() -> None:
    """6. A workflow mixing OpenAI + Anthropic + Local LM Studio attributes exact tokens/cost per provider and per model."""
    svc = ApexPlatformService(seed_demo=False)
    svc.register_user(
        email="cost-owner@example.org",
        password="CostOwnerStrongPassword!2026",
        display_name="Cost Owner",
        organization_name="Cost Org",
    )
    login = svc.login(
        email="cost-owner@example.org", password="CostOwnerStrongPassword!2026"
    )
    uid, oid = login["user"]["id"], login["active_organization_id"]

    run_id = "wf-mixed-providers-01"
    c1 = build_call_attribution_record(
        workflow_run_id=run_id,
        step_id="step-1",
        agent_id="research-planner",
        provider="openai",
        model="gpt-4o-mini",
        execution_mode="EXTERNAL_LLM",
        input_tokens=1000,
        output_tokens=500,
        llm_invoked=True,
    ).to_dict()  # 0.00015 + 0.00030 = 0.00045
    c2 = build_call_attribution_record(
        workflow_run_id=run_id,
        step_id="step-2",
        agent_id="critic-replicator",
        provider="anthropic",
        model="claude-3-5-haiku-latest",
        execution_mode="EXTERNAL_LLM",
        input_tokens=1000,
        output_tokens=250,
        llm_invoked=True,
    ).to_dict()  # 0.00080 + 0.00100 = 0.00180
    c3 = build_call_attribution_record(
        workflow_run_id=run_id,
        step_id="step-3",
        agent_id="research-writer",
        provider="lmstudio-lan-qwen",
        model="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        execution_mode="LOCAL_MODEL",
        input_tokens=2000,
        output_tokens=800,
        llm_invoked=True,
    ).to_dict()  # $0.00 local

    rollup = aggregate_workflow_cost_attribution([c1, c2, c3], workflow_run_id=run_id)
    svc._persist_run_side_effects(
        {
            "id": run_id,
            "organization_id": oid,
            "status": "COMPLETED",
            "call_attribution_records": [c1, c2, c3],
            "cost_usd": rollup["known_combined_cost_usd"],
            "duration_ms": 120,
            "tool_invocations": [],
            "workspace_scope": "research",
            "output_json": {},
        },
        user_id=uid,
    )

    # Verify `usage_records` preserved ALL THREE providers instead of overwriting with `lmstudio-lan-qwen`!
    by_prov = {
        u["provider_name"]: u
        for u in svc.usage_records
        if u["agent_run_id"] == run_id
    }
    assert set(by_prov.keys()) == {"openai", "anthropic", "lmstudio-lan-qwen"}
    assert by_prov["openai"]["cost_usd"] == 0.00045
    assert by_prov["anthropic"]["cost_usd"] == 0.0018
    assert by_prov["lmstudio-lan-qwen"]["cost_usd"] == 0.0
    assert by_prov["lmstudio-lan-qwen"]["total_tokens"] == 2800


def test_07_strict_separation_of_cloud_local_public_api_infra_and_db_buckets() -> None:
    """7. Cloud LLM, Local LLM, Public Data API, Infrastructure, and Database costs stay in separate buckets."""
    r_cloud = build_call_attribution_record(
        workflow_run_id="wf-sep",
        step_id="s1",
        agent_id="research-writer",
        provider="openai",
        model="gpt-4o-mini",
        execution_mode="EXTERNAL_LLM",
        input_tokens=1000,
        output_tokens=1000,
        llm_invoked=True,
    )
    r_pub = build_call_attribution_record(
        workflow_run_id="wf-sep",
        step_id="s2",
        agent_id="source-scout",
        provider="sec_edgar",
        model="none",
        execution_mode="PUBLIC_DATA_API",
        input_tokens=0,
        output_tokens=0,
        external_api_invoked=True,
    )
    rollup_unmetered_infra = aggregate_workflow_cost_attribution(
        [r_cloud, r_pub], workflow_run_id="wf-sep"
    )
    cats = rollup_unmetered_infra["cost_breakdown_by_category"]
    assert cats["cloud_llm_api"]["estimated_cost_usd"] == 0.00075
    assert cats["public_data_api"]["actual_cost_usd"] == 0.0
    assert cats["infrastructure"]["status"] == "NOT_METERED"
    assert cats["infrastructure"]["cost_usd"] is None
    assert cats["database"]["status"] == "NOT_METERED"
    assert cats["database"]["cost_usd"] is None

    # Now add explicit metered infrastructure & database records and verify they stay isolated from LLM API cost:
    r_infra = build_call_attribution_record(
        workflow_run_id="wf-sep",
        step_id="s-infra",
        agent_id="worker-pool",
        provider="worker_compute",
        model="none",
        execution_mode="INFRA_RUNTIME",
        input_tokens=0,
        output_tokens=0,
        upstream_actual_cost_usd=0.0025,
        explicit_category=CostCategory.INFRASTRUCTURE,
    )
    r_db = build_call_attribution_record(
        workflow_run_id="wf-sep",
        step_id="s-db",
        agent_id="postgres-rls",
        provider="postgresql",
        model="none",
        execution_mode="DB_STORAGE",
        input_tokens=0,
        output_tokens=0,
        upstream_actual_cost_usd=0.0004,
        explicit_category=CostCategory.DATABASE,
    )
    rollup_metered = aggregate_workflow_cost_attribution(
        [r_cloud, r_pub, r_infra, r_db], workflow_run_id="wf-sep"
    )
    cats2 = rollup_metered["cost_breakdown_by_category"]
    assert cats2["cloud_llm_api"]["estimated_cost_usd"] == 0.00075
    assert cats2["infrastructure"] == {"status": "METERED", "cost_usd": 0.0025}
    assert cats2["database"] == {"status": "METERED", "cost_usd": 0.0004}


def test_08_http_cost_attribution_endpoint_and_tenant_isolation() -> None:
    """8. `GET /api/v1/analytics/cost-attribution` returns tenant-isolated call ledger & reconciled totals."""
    settings = Settings(env="test", enable_demo_seed=False)
    app = create_app(settings=settings, seed_demo=False)

    with TestClient(app) as client:
        reg_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "tenant-a-cost@example.org",
                "password": "TenantACostPassword!2026",
                "display_name": "Tenant A",
                "organization_name": "Org A",
            },
        )
        assert reg_a.status_code == 201
        login_a = client.post(
            "/api/v1/auth/login",
            json={
                "email": "tenant-a-cost@example.org",
                "password": "TenantACostPassword!2026",
            },
        ).json()
        headers_a = {
            "Authorization": f"Bearer {login_a['access_token']}",
            "X-CSRF-Token": login_a["csrf_token"],
            "X-Organization-Id": login_a["active_organization_id"],
        }

        run_resp = client.post(
            "/api/v1/runs",
            headers=headers_a,
            json={
                "objective": "Verify cost attribution ledger over HTTP",
                "execution_mode": "single_agent",
            },
        )
        assert run_resp.status_code == 201
        run_id = run_resp.json()["id"]

        ledger_a = client.get(
            f"/api/v1/analytics/cost-attribution?workflow_run_id={run_id}",
            headers=headers_a,
        )
        assert ledger_a.status_code == 200
        body_a = ledger_a.json()
        assert body_a["summary"]["call_count"] == 1
        assert MANDATORY_16_FIELDS.issubset(set(body_a["calls"][0].keys()))

        # Register Tenant B and verify Tenant B sees 0 records for Tenant A's run!
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "tenant-b-cost@example.org",
                "password": "TenantBCostPassword!2026",
                "display_name": "Tenant B",
                "organization_name": "Org B",
            },
        )
        login_b = client.post(
            "/api/v1/auth/login",
            json={
                "email": "tenant-b-cost@example.org",
                "password": "TenantBCostPassword!2026",
            },
        ).json()
        headers_b = {
            "Authorization": f"Bearer {login_b['access_token']}",
            "X-CSRF-Token": login_b["csrf_token"],
            "X-Organization-Id": login_b["active_organization_id"],
        }
        ledger_b = client.get(
            f"/api/v1/analytics/cost-attribution?workflow_run_id={run_id}",
            headers=headers_b,
        )
        assert ledger_b.status_code == 200
        assert ledger_b.json()["summary"]["call_count"] == 0
        assert ledger_b.json()["calls"] == []
