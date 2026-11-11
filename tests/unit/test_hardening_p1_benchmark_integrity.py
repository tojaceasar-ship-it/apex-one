"""HARDENING 1.0 — Stage 6 (P1): Symmetrical & Independent Benchmark Integrity Tests.

Covers all 7 mandatory verification scenarios:
1. Symmetrical input & budget enforcement (rejecting asymmetrical comparisons via `BenchmarkIntegrityError`)
2. Single-Agent Baseline receives contradictory sources symmetrically and is NEVER hardcoded to `contradictions_detected=0`
3. Independent evaluator scores all 7 separate task dimensions from held-out ground truth (immune to self-reported counter spoofing)
4. Precision, Recall, F1, FPR, FNR, and 95% Wilson CIs — returning `NOT_AVAILABLE` when data is insufficient (never faking `1.0`)
5. Explicit `SYNTHETIC` vs `REAL_EMPIRICAL` vs `NOT_AVAILABLE` data-origin labeling
6. Provenance tracking (`model_identifier`, `agent_version`, `prompt_version_hash`) & zero predetermined winner (Single-Agent ties/wins on narrow single-source tasks)
7. Strict step-to-run token reconciliation (`reconcile_run_tokens` rejects tampered/inconsistent token totals)
"""

from __future__ import annotations

import copy
import uuid

import pytest

from apex_one.platform.experiments_and_eval import (
    BENCHMARK_TASK_DIMENSIONS,
    INDEPENDENT_EVALUATOR_VERSION,
    BenchmarkIntegrityError,
    assert_benchmark_symmetry,
    build_blind_benchmark_sources,
    compute_classification_stats,
    evaluate_benchmark_independently,
    reconcile_run_tokens,
)
from apex_one.platform.workspace_service import ApexPlatformService


def test_01_symmetrical_inputs_enforced_and_asymmetry_rejected() -> None:
    """1. Single-Agent and Multi-Agent receive identical blind corpus, objective & budgets; asymmetry raises `BenchmarkIntegrityError`."""
    svc = ApexPlatformService(seed_demo=True)
    evals = list(svc.eval_runs.values())
    single_eval = next(r for r in evals if r["subject_type"] == "single_agent")
    multi_eval = next(r for r in evals if r["subject_type"] == "multi_agent_team")

    comp = svc.compare_symmetrical_benchmark_runs(
        user_id=next(iter(svc.users.keys())),
        organization_id=svc.default_org_id,
        baseline_eval_run_id=single_eval["id"],
        candidate_eval_run_id=multi_eval["id"],
    )
    assert comp["symmetry_verified"] is True

    spec_single = svc.experiment_runs[single_eval["experiment_run_id"]]["spec_snapshot_json"]
    spec_multi = svc.experiment_runs[multi_eval["experiment_run_id"]]["spec_snapshot_json"]
    assert spec_single["dataset_checksum"] == spec_multi["dataset_checksum"]
    assert spec_single["canonical_objective"] == spec_multi["canonical_objective"]
    assert spec_single["source_count"] == spec_multi["source_count"] == 4
    assert spec_single["blind_input_enforced"] is True
    assert spec_multi["blind_input_enforced"] is True
    assert spec_single["budget_limits"] == spec_multi["budget_limits"]

    # Tampering with source_count or canonical_objective in one run MUST fail symmetry validation:
    skewed_spec = copy.deepcopy(spec_multi)
    skewed_spec["source_count"] = 3
    with pytest.raises(BenchmarkIntegrityError, match="source_count"):
        assert_benchmark_symmetry(spec_single, skewed_spec)


def test_02_single_agent_receives_contradictory_sources_without_artificial_zeroing() -> None:
    """2. Single-Agent Baseline processes contradictory sources symmetrically and detects explicit contradictions."""
    svc = ApexPlatformService(seed_demo=True)
    single_eval = next(
        r for r in svc.eval_runs.values() if r["subject_type"] == "single_agent"
    )
    single_agent_run = svc.agent_runs[single_eval["agent_run_id"]]
    out = single_agent_run["output_json"]

    # Blind benchmark sources must NOT leak `labeled_claims` ground truth to either configuration:
    ds_ver = svc.dataset_versions[svc.eval_suites[single_eval["suite_id"]]["dataset_id"]][0]
    blind_srcs = build_blind_benchmark_sources(
        ds_ver["records_json"], organization_id=svc.default_org_id
    )
    for bs in blind_srcs:
        assert "labeled_claims" not in bs["metadata_json"]

    # Single-Agent is NOT crippled to `contradictions_detected == 0`: it extracts contradicting SRC-SYN-03!
    assert out["contradictions_detected"] >= 1
    polarities = {c["evidence_type"] for c in out["extracted_claims"]}
    assert "contradicting" in polarities
    assert "supporting" in polarities


def test_03_independent_evaluator_scores_all_seven_dimensions_and_ignores_self_reported_lies() -> None:
    """3. Independent evaluator scores all 7 task dimensions from ground truth and ignores spoofed self-counters."""
    svc = ApexPlatformService(seed_demo=True)
    multi_eval = next(
        r for r in svc.eval_runs.values() if r["subject_type"] == "multi_agent_team"
    )
    assert multi_eval["evaluator_version"] == INDEPENDENT_EVALUATOR_VERSION
    dims = multi_eval["task_dimensions"]
    assert set(dims.keys()) == set(BENCHMARK_TASK_DIMENSIONS)

    for dim_name in BENCHMARK_TASK_DIMENSIONS:
        assert dims[dim_name]["status"] == "MEASURED"

    # Now spoof an agent run claiming `contradictions_detected = 999` while `conflicts_json = []`!
    single_eval = next(
        r for r in svc.eval_runs.values() if r["subject_type"] == "single_agent"
    )
    spoofed_run = copy.deepcopy(svc.agent_runs[single_eval["agent_run_id"]])
    spoofed_run["output_json"]["contradictions_detected"] = 999
    spoofed_run["conflicts_json"] = []

    exp = svc.experiments[
        svc.experiment_runs[single_eval["experiment_run_id"]]["experiment_id"]
    ]
    ds = svc.datasets[exp["dataset_id"]]
    ver_obj = svc.dataset_versions[exp["dataset_id"]][0]

    indep_spoofed = evaluate_benchmark_independently(
        dataset=ds,
        dataset_version_obj=ver_obj,
        cases=svc.eval_cases[single_eval["suite_id"]],
        agent_run_result=spoofed_run,
        experiment=exp,
    )
    # Self-reported `999` is completely ignored because `conflicts_json` is empty -> recall=0.0, tp=0!
    assert indep_spoofed["task_dimensions"]["contradiction_detection"]["tp"] == 0
    assert indep_spoofed["task_dimensions"]["contradiction_detection"]["recall"] == 0.0


def test_04_precision_recall_f1_fpr_fnr_ci_and_not_available_on_empty_ground_truth() -> None:
    """4. Classification stats compute P/R/F1/FPR/FNR + Wilson 95% CI, and return `NOT_AVAILABLE` when empty."""
    stats = compute_classification_stats(tp=8, fp=2, fn=2, tn=8)
    assert stats["status"] == "MEASURED"
    assert stats["precision"] == 0.8
    assert stats["recall"] == 0.8
    assert stats["f1"] == 0.8
    assert stats["false_positive_rate"] == 0.2
    assert stats["false_negative_rate"] == 0.2
    assert isinstance(stats["ci_95"], dict)
    assert 0.55 < stats["ci_95"]["low"] < 0.80 < stats["ci_95"]["high"] <= 1.0

    # Empty sample -> NEVER fabricate 1.0!
    empty_stats = compute_classification_stats(tp=0, fp=0, fn=0, tn=0)
    assert empty_stats["status"] == "NOT_AVAILABLE"
    assert empty_stats["precision"] is None
    assert empty_stats["f1"] is None
    assert empty_stats["ci_95"] == "NOT_AVAILABLE"

    # Empty dataset records -> independent evaluator returns `NOT_AVAILABLE`
    dummy_run = {
        "id": "r-empty",
        "steps": [{"tokens_used": 0}],
        "tokens_used": 0,
        "output_json": {},
        "conflicts_json": [],
    }
    indep_empty = evaluate_benchmark_independently(
        dataset={"category": "empirical"},
        dataset_version_obj={"records_json": [], "checksum_sha256": "00"},
        cases=[],
        agent_run_result=dummy_run,
        experiment={"model_identifier": "apex-analytical-local-v1"},
    )
    assert indep_empty["status"] == "NOT_AVAILABLE"
    assert indep_empty["data_origin"] == "NOT_AVAILABLE"
    assert indep_empty["task_dimensions"]["claim_extraction"]["status"] == "NOT_AVAILABLE"


def test_05_synthetic_vs_real_empirical_data_origin_labeling() -> None:
    """5. Synthetic datasets are explicitly labeled `SYNTHETIC`, empirical datasets `REAL_EMPIRICAL`."""
    svc = ApexPlatformService(seed_demo=True)
    uid = next(iter(svc.users.keys()))
    oid = svc.default_org_id

    seeded_eval = next(iter(svc.eval_runs.values()))
    assert seeded_eval["data_origin"] == "SYNTHETIC"

    emp_ds = svc.create_dataset(
        user_id=uid,
        organization_id=oid,
        name="Empirical Market Volatility Corpus",
        description="Curated empirical volatility paper excerpts",
        category="empirical",
        records=[
            {
                "doc_id": "EMP-01",
                "title": "Realized Volatility Clustering in Spot Markets",
                "source_type": "MD",
                "text": "Empirical realized 30-day volatility exhibits persistent GARCH clustering across spot regimes.",
                "labeled_claims": [
                    {
                        "statement": "Empirical realized 30-day volatility exhibits persistent GARCH clustering.",
                        "polarity": "supporting",
                    }
                ],
            }
        ],
    )
    exp_res = svc.create_and_run_experiment(
        user_id=uid,
        organization_id=oid,
        name="Empirical Check",
        description="Verify REAL_EMPIRICAL origin tag",
        dataset_id=emp_ds["id"],
        dataset_version=1,
        execution_mode="single_agent",
    )
    assert exp_res["run"]["data_origin"] == "REAL_EMPIRICAL"


def test_06_provenance_tracking_and_no_predetermined_winner_on_simple_factoid_suite() -> None:
    """6. Provenance tracks model/version/prompt hash; on a 1-doc factoid suite Single-Agent ties accuracy & wins efficiency."""
    svc = ApexPlatformService(seed_demo=False)
    svc.register_user(
        email="bench-fair@example.org",
        password="BenchFairStrongPassword!2026",
        display_name="Fair Researcher",
        organization_name="Fair Lab",
    )
    login = svc.login(
        email="bench-fair@example.org", password="BenchFairStrongPassword!2026"
    )
    uid, oid = login["user"]["id"], login["active_organization_id"]

    ds = svc.create_dataset(
        user_id=uid,
        organization_id=oid,
        name="Narrow Factoid Single-Source Corpus",
        description="Single unambiguous source where single-agent has zero context saturation",
        category="empirical",
        records=[
            {
                "doc_id": "FACT-01",
                "title": "Transactional Outbox Idempotency Study",
                "source_type": "TXT",
                "text": "Step-level idempotency keys in a PostgreSQL transactional outbox prevented 100% of duplicate tool calls across 500 restarts.",
                "labeled_claims": [
                    {
                        "statement": "Step-level idempotency keys in a PostgreSQL transactional outbox prevented 100% of duplicate tool calls.",
                        "polarity": "supporting",
                    }
                ],
            }
        ],
    )
    suite_id = str(uuid.uuid4())
    svc.eval_suites[suite_id] = {
        "id": suite_id,
        "organization_id": oid,
        "name": "Narrow Factoid Benchmark",
        "description": "1-doc symmetrical test",
        "dataset_id": ds["id"],
        "dataset_version": 1,
        "rubric_json": {},
        "workspace_scope": "research",
        "created_at": "2026-11-10T00:00:00Z",
    }
    svc.eval_cases[suite_id] = [
        {
            "id": str(uuid.uuid4()),
            "suite_id": suite_id,
            "prompt_text": "What prevents duplicate tool calls across restarts?",
            "reference_output": "Step-level idempotency keys in PostgreSQL transactional outbox (FACT-01).",
            "expected_claims_json": ["FACT-01"],
        }
    ]

    eval_single = svc.run_evaluation_benchmark(
        user_id=uid,
        organization_id=oid,
        suite_id=suite_id,
        label="Single-Agent on Factoid",
        execution_mode="single_agent",
    )
    eval_multi = svc.run_evaluation_benchmark(
        user_id=uid,
        organization_id=oid,
        suite_id=suite_id,
        label="Multi-Agent on Factoid",
        execution_mode="multi_agent_team",
        baseline_eval_run_id=eval_single["id"],
    )

    # Provenance fields are populated
    assert eval_single["provenance"]["model_identifier"] == "apex-analytical-local-v1"
    assert len(eval_single["provenance"]["prompt_version_hash"]) == 16
    assert eval_single["provenance"]["dataset_checksum_sha256"] == eval_multi["provenance"]["dataset_checksum_sha256"]

    # No predetermined winner: Single-Agent achieves 1.0 claim extraction F1 & higher weighted_overall on narrow 1-doc factoid!
    assert eval_single["task_dimensions"]["claim_extraction"]["f1"] == 1.0
    assert (
        eval_single["summary_scores"]["weighted_overall"]
        >= eval_multi["summary_scores"]["weighted_overall"]
    )
    assert (
        eval_single["summary_scores"]["latency"]
        > eval_multi["summary_scores"]["latency"]
    )


def test_07_token_reconciliation_enforces_exact_step_to_run_sum() -> None:
    """7. `reconcile_run_tokens` verifies step-level and telemetry-level token totals match run total."""
    valid_run = {
        "id": "run-tok-ok",
        "tokens_used": 320,
        "steps": [
            {"tokens_used": 120},
            {"tokens_used": 200},
        ],
        "checkpoint_json": {
            "step_telemetry": [
                {"input_tokens": 80, "output_tokens": 40},
                {"input_tokens": 150, "output_tokens": 50},
            ]
        },
    }
    rec = reconcile_run_tokens(valid_run)
    assert rec["reconciled"] is True
    assert rec["reported_run_tokens"] == 320
    assert rec["discrepancy"] == 0

    tampered_run = copy.deepcopy(valid_run)
    tampered_run["tokens_used"] = 150  # Under-reported run tokens!
    with pytest.raises(BenchmarkIntegrityError, match="Token reconciliation failed"):
        reconcile_run_tokens(tampered_run)
