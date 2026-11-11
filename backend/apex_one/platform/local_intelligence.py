"""APEX ONE — Local-First Intelligence Architecture (Layers A / B / C, Router, L1-L3 Cache & Telemetry).

Core principle:
    LOCAL-FIRST. EXTERNAL API ONLY WHEN JUSTIFIED.

Implements:
1. Layer A (`LOCAL_DETERMINISTIC` & `LOCAL_STATISTICAL`):
   - SHA-256 & integrity verification, JSON/XML/CSV schema validation & normalization,
     deduplication, source quality scoring, citation completeness, rule-based & quantitative
     contradiction detection, SVG chart series generation, completeness checks.
   - Vectorized NumPy (with pure-Python fallback) OHLCV & financial indicator processing,
     realized volatility (close-to-close, Parkinson high-low, EWMA lambda=0.94), OLS linear
     regression, Pearson/Spearman correlations, Welch t-test, Jarque-Bera normality,
     stationary/historical bootstrap, and 10,000-path antithetic-variates GBM Monte Carlo.
2. Multi-Level Cache (`L1` process memory -> `L2` Redis / local file cache -> `L3` PostgreSQL / durable store)
   with explicit TTL freshness enforcement and mandatory provenance envelope fields:
   `source_id`, `provider`, `retrieved_at`, `observed_at`, `expires_at`, `content_hash`,
   `schema_version`, `verification_status`.
3. Layer B (`PUBLIC_DATA_API`):
   - Checks L3 -> L2 -> L1 + TTL freshness BEFORE any network socket is opened.
   - Bounded retry + exponential backoff + rate-limit handling, structural & SHA-256 verification,
     and honest stale/unavailable fallback (never fabricates values).
4. Intelligent `ExecutionRouter`:
   - Evaluates all 10 mandatory dimensions and routes across:
     `LOCAL_DETERMINISTIC`, `LOCAL_STATISTICAL`, `LOCAL_MODEL`, `PUBLIC_DATA_API`,
     `EXTERNAL_LLM`, `HYBRID`.
   - Enforces per-run LLM call budgets, per-agent token budgets, per-study cost caps,
     idempotency keys, timeouts, confidential-data redaction for external cloud LLMs,
     and mandatory `llm_justification`.
5. Mandatory Step & Aggregate Telemetry (never conflating estimated vs actual measurements).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import re
import ssl
import statistics
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False

try:
    import redis as redis_lib
except ImportError:  # pragma: no cover
    redis_lib = None  # type: ignore[assignment]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_iso(ts: str) -> datetime:
    cleaned = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class ExecutionMode(StrEnum):
    LOCAL_DETERMINISTIC = "LOCAL_DETERMINISTIC"
    LOCAL_STATISTICAL = "LOCAL_STATISTICAL"
    LOCAL_MODEL = "LOCAL_MODEL"
    PUBLIC_DATA_API = "PUBLIC_DATA_API"
    EXTERNAL_LLM = "EXTERNAL_LLM"
    HYBRID = "HYBRID"


class TaskCategory(StrEnum):
    CHECKSUM_HASH = "checksum_hash"
    SCHEMA_VALIDATION = "schema_validation"
    DATA_PARSING_DEDUP = "data_parsing_dedup"
    CSV_STATISTICAL_ANALYSIS = "csv_statistical_analysis"
    OHLCV_VOLATILITY = "ohlcv_volatility"
    MONTE_CARLO_SIMULATION = "monte_carlo_simulation"
    BOOTSTRAP_HYPOTHESIS_TEST = "bootstrap_hypothesis_test"
    SOURCE_SCORING_CITATIONS = "source_scoring_citations"
    RULE_CONTRADICTION_CHECK = "rule_contradiction_check"
    CHART_AND_TABLE_BUILD = "chart_and_table_build"
    DAG_BUDGET_SUPERVISION = "dag_budget_supervision"
    PUBLIC_MARKET_DATA_FETCH = "public_market_data_fetch"
    PUBLIC_LITERATURE_FETCH = "public_literature_fetch"
    SEMANTIC_DOCUMENT_SYNTHESIS = "semantic_document_synthesis"
    CONFLICTING_STANCE_CRITIQUE = "conflicting_stance_critique"
    HYPOTHESIS_FORMULATION = "hypothesis_formulation"
    HYBRID_REPORT_SYNTHESIS = "hybrid_report_synthesis"


# Tasks where invoking an LLM is strictly prohibited by Section 2 (Layer C)
LLM_FORBIDDEN_TASK_CATEGORIES: frozenset[TaskCategory] = frozenset(
    {
        TaskCategory.CHECKSUM_HASH,
        TaskCategory.SCHEMA_VALIDATION,
        TaskCategory.DATA_PARSING_DEDUP,
        TaskCategory.CSV_STATISTICAL_ANALYSIS,
        TaskCategory.OHLCV_VOLATILITY,
        TaskCategory.MONTE_CARLO_SIMULATION,
        TaskCategory.BOOTSTRAP_HYPOTHESIS_TEST,
        TaskCategory.SOURCE_SCORING_CITATIONS,
        TaskCategory.RULE_CONTRADICTION_CHECK,
        TaskCategory.CHART_AND_TABLE_BUILD,
        TaskCategory.DAG_BUDGET_SUPERVISION,
        TaskCategory.PUBLIC_MARKET_DATA_FETCH,
        TaskCategory.PUBLIC_LITERATURE_FETCH,
    }
)


# ============================================================================
# SECTION 4 — EXPLICIT AGENT EXECUTION CONTRACTS
# ============================================================================


class ExecutionBudgetSpec(BaseModel):
    max_llm_calls: int = Field(default=0, ge=0, le=10)
    max_tokens: int = Field(default=0, ge=0, le=32768)
    max_cost_usd: float = Field(default=0.0, ge=0.0, le=10.0)
    timeout_seconds: float = Field(default=25.0, gt=0.0, le=300.0)
    max_delegation_depth: int = Field(default=1, ge=0, le=2)


class AgentExecutionContract(BaseModel):
    agent_slug: str
    capabilities: list[str]
    local_execution_methods: list[str]
    external_api_requirements: list[str]
    llm_requirements: dict[str, Any]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    validation_method: str
    execution_budget: ExecutionBudgetSpec
    fallback_strategy: str


BUILTIN_AGENT_CONTRACTS: dict[str, AgentExecutionContract] = {
    "supervisor": AgentExecutionContract(
        agent_slug="supervisor",
        capabilities=["dag_control", "budget_allocation", "idempotency_guard"],
        local_execution_methods=["inspect_dag_topology", "enforce_run_cost_and_call_caps"],
        external_api_requirements=[],
        llm_requirements={"allowed": False, "reason": "Purely deterministic DAG & budget control."},
        input_schema={"objective": "string", "execution_budget": "object"},
        output_schema={"selected_team": "array", "dag_stages": "integer", "budget_policy": "object"},
        validation_method="deterministic_dag_acyclic_and_budget_schema_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=0, max_tokens=0, max_cost_usd=0.0, timeout_seconds=5.0, max_delegation_depth=1),
        fallback_strategy="local_deterministic_only",
    ),
    "research-planner": AgentExecutionContract(
        agent_slug="research-planner",
        capabilities=["structured_brief_generation", "threshold_decomposition", "completeness_criteria"],
        local_execution_methods=["extract_quantitative_thresholds", "build_structured_research_brief"],
        external_api_requirements=[],
        llm_requirements={"allowed": False, "reason": "Structured research brief & testable hypotheses decomposed deterministically by default."},
        input_schema={"objective": "string"},
        output_schema={"objective": "string", "methodology": "string", "questions": "array", "thresholds": "array"},
        validation_method="non_empty_questions_and_threshold_schema_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=0, max_tokens=0, max_cost_usd=0.0, timeout_seconds=10.0, max_delegation_depth=0),
        fallback_strategy="local_rule_based_brief_decomposition",
    ),
    "source-scout": AgentExecutionContract(
        agent_slug="source-scout",
        capabilities=["cache_first_lookup", "sha256_deduplication", "source_quality_scoring", "public_api_acquisition"],
        local_execution_methods=["compute_sha256_integrity", "score_and_deduplicate_sources"],
        external_api_requirements=["binance_or_coingecko_ohlcv_on_cache_miss", "arxiv_openalex_on_cache_miss"],
        llm_requirements={"allowed": False, "reason": "Source acquisition, SHA-256 deduplication, and quality scoring are deterministic."},
        input_schema={"sources": "array", "query": "string"},
        output_schema={"unique_source_ids": "array", "duplicates_skipped": "integer", "quality_scores": "array"},
        validation_method="sha256_uniqueness_and_citation_metadata_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=0, max_tokens=0, max_cost_usd=0.0, timeout_seconds=20.0, max_delegation_depth=0),
        fallback_strategy="l2_l3_cached_corpus_or_explicit_unavailable_flag",
    ),
    "evidence-analyst": AgentExecutionContract(
        agent_slug="evidence-analyst",
        capabilities=["claim_extraction", "citation_binding", "rule_contradiction_scan", "multi_doc_semantic_synthesis"],
        local_execution_methods=["extract_structured_and_heuristic_claims", "detect_rule_based_contradictions"],
        external_api_requirements=[],
        llm_requirements={
            "allowed": True,
            "condition": "Only when >= 3 long unstructured documents contain opposing semantic stances that cannot be resolved by rules alone.",
        },
        input_schema={"sources": "array", "objective": "string"},
        output_schema={"claims_count": "integer", "contradictions_count": "integer"},
        validation_method="every_claim_bound_to_verified_source_id",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=1, max_tokens=2200, max_cost_usd=0.08, timeout_seconds=45.0, max_delegation_depth=0),
        fallback_strategy="continue_with_local_rule_extracted_claims_and_flag_semantic_pass_pending",
    ),
    "data-analyst": AgentExecutionContract(
        agent_slug="data-analyst",
        capabilities=["ohlcv_processing", "realized_volatility", "monte_carlo_gbm", "bootstrap_ci", "ols_regression", "csv_statistics"],
        local_execution_methods=["analyze_ohlcv_and_volatility", "run_monte_carlo_simulation", "analyze_tabular_csv_statistics"],
        external_api_requirements=[],
        llm_requirements={"allowed": False, "reason": "All statistical, volatility, probability, and Monte Carlo computations MUST run locally."},
        input_schema={"sources": "array", "ohlcv_series": "array"},
        output_schema={"source_count": "integer", "quantitative_metrics": "object", "monte_carlo": "object"},
        validation_method="probability_bounds_0_1_and_positive_definite_variance_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=0, max_tokens=0, max_cost_usd=0.0, timeout_seconds=15.0, max_delegation_depth=0),
        fallback_strategy="pure_python_statistical_kernel_if_numpy_absent",
    ),
    "experiment-engineer": AgentExecutionContract(
        agent_slug="experiment-engineer",
        capabilities=["sensitivity_stress_matrix", "bootstrap_validation", "reproducibility_manifest"],
        local_execution_methods=["run_volatility_regime_stress_grid", "compute_reproducibility_hash"],
        external_api_requirements=[],
        llm_requirements={"allowed": False, "reason": "Stress testing and benchmark protocol construction are deterministic/statistical."},
        input_schema={"objective": "string", "statistics": "object"},
        output_schema={"protocol": "string", "stress_scenarios": "array", "reproducibility_sha256": "string"},
        validation_method="monotonic_stress_drawdown_probability_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=0, max_tokens=0, max_cost_usd=0.0, timeout_seconds=15.0, max_delegation_depth=0),
        fallback_strategy="local_deterministic_protocol_manifest",
    ),
    "critic-replicator": AgentExecutionContract(
        agent_slug="critic-replicator",
        capabilities=["citation_completeness_audit", "numerical_consistency_verification", "argumentation_critique"],
        local_execution_methods=["verify_citation_attribution", "detect_rule_based_contradictions", "audit_research_completeness"],
        external_api_requirements=[],
        llm_requirements={
            "allowed": True,
            "condition": "Only when verified cross-source textual contradictions require advanced dialectical argumentation critique.",
        },
        input_schema={"claims": "array", "statistics": "object", "contradictions": "array"},
        output_schema={"reviewed_claims": "integer", "unattributed_claims": "integer", "verified_contradictions": "integer", "completeness_score": "number"},
        validation_method="zero_unattributed_claims_and_completeness_audit",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=1, max_tokens=1800, max_cost_usd=0.06, timeout_seconds=45.0, max_delegation_depth=0),
        fallback_strategy="local_rule_and_statistical_consistency_audit",
    ),
    "research-writer": AgentExecutionContract(
        agent_slug="research-writer",
        capabilities=["structured_markdown_tables", "chart_series_generation", "executive_narrative_synthesis"],
        local_execution_methods=["build_structured_report_tables", "generate_svg_probability_chart_spec"],
        external_api_requirements=[],
        llm_requirements={
            "allowed": True,
            "condition": "HYBRID mode: tables, probabilities, and citations are built locally; LLM is invoked once solely for final executive narrative synthesis.",
        },
        input_schema={"claims": "array", "statistics": "object", "critique": "object"},
        output_schema={"report_chars": "integer", "linked_claims": "integer"},
        validation_method="structured_sections_and_source_citations_present",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=1, max_tokens=2600, max_cost_usd=0.10, timeout_seconds=60.0, max_delegation_depth=0),
        fallback_strategy="emit_complete_local_structured_report_and_mark_narrative_llm_pending",
    ),
    "universal-baseline": AgentExecutionContract(
        agent_slug="universal-baseline",
        capabilities=["single_pass_local_summary", "optional_narrative_synthesis"],
        local_execution_methods=["score_and_deduplicate_sources", "build_structured_report_tables"],
        external_api_requirements=[],
        llm_requirements={"allowed": True, "condition": "Optional single narrative synthesis pass."},
        input_schema={"objective": "string", "sources": "array"},
        output_schema={"sources_reviewed": "integer", "claims_extracted": "integer"},
        validation_method="non_empty_report_check",
        execution_budget=ExecutionBudgetSpec(max_llm_calls=1, max_tokens=2000, max_cost_usd=0.08, timeout_seconds=45.0, max_delegation_depth=0),
        fallback_strategy="local_structured_baseline_report",
    ),
}


# ============================================================================
# LAYER A — LOCAL DETERMINISTIC & STATISTICAL KERNELS (ZERO LLM, ZERO NETWORK)
# ============================================================================


def compute_sha256_integrity(payload: str | bytes | dict[str, Any] | list[Any]) -> dict[str, Any]:
    """Deterministic SHA-256 digest & byte-size inspection (Layer A)."""
    if isinstance(payload, bytes):
        raw_bytes = payload
    elif isinstance(payload, str):
        raw_bytes = payload.encode("utf-8")
    else:
        raw_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return {
        "algorithm": "SHA-256",
        "sha256": digest,
        "byte_length": len(raw_bytes),
        "verified": len(digest) == 64,
    }


def validate_and_parse_structured_data(
    raw_input: str | bytes,
    *,
    fmt: Literal["auto", "json", "xml", "csv"] = "auto",
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic JSON / XML / CSV parser, schema validator & deduplicator (Layer A)."""
    text = raw_input.decode("utf-8", errors="replace") if isinstance(raw_input, bytes) else raw_input
    stripped = text.strip()
    detected = fmt
    if detected == "auto":
        if stripped.startswith(("{", "[")):
            detected = "json"
        elif stripped.startswith("<"):
            detected = "xml"
        else:
            detected = "csv"

    if detected == "json":
        parsed = json.loads(stripped)
        records = parsed if isinstance(parsed, list) else [parsed]
        missing: list[str] = []
        if required_fields and records and isinstance(records[0], dict):
            missing = [f for f in required_fields if f not in records[0]]
        return {
            "format": "json",
            "valid": len(missing) == 0,
            "record_count": len(records),
            "missing_required_fields": missing,
            "checksum_sha256": compute_sha256_integrity(stripped)["sha256"],
            "normalized": parsed,
        }

    if detected == "xml":
        if "<!ENTITY" in stripped.upper() or "<!DOCTYPE" in stripped.upper():
            raise ValueError("Disallowed XML DTD/ENTITY declaration rejected locally.")
        root = ET.fromstring(stripped)
        children = [{child.tag: (child.text or "").strip(), **child.attrib} for child in list(root)]
        return {
            "format": "xml",
            "valid": True,
            "root_tag": root.tag,
            "record_count": len(children),
            "checksum_sha256": compute_sha256_integrity(stripped)["sha256"],
            "normalized": children,
        }

    reader = csv.DictReader(io.StringIO(stripped))
    fieldnames = list(reader.fieldnames or [])
    rows: list[dict[str, Any]] = []
    seen_row_hashes: set[str] = set()
    duplicates_removed = 0
    for row in reader:
        clean_row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
        rh = hashlib.sha256(json.dumps(clean_row, sort_keys=True).encode("utf-8")).hexdigest()
        if rh in seen_row_hashes:
            duplicates_removed += 1
            continue
        seen_row_hashes.add(rh)
        rows.append(clean_row)
    missing = [f for f in (required_fields or []) if f not in fieldnames]
    return {
        "format": "csv",
        "valid": bool(fieldnames) and len(missing) == 0,
        "columns": fieldnames,
        "record_count": len(rows),
        "duplicates_removed": duplicates_removed,
        "missing_required_fields": missing,
        "checksum_sha256": compute_sha256_integrity(stripped)["sha256"],
        "rows": rows,
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def analyze_tabular_csv_statistics(raw_csv: str) -> dict[str, Any]:
    """Local deterministic + statistical analysis of structured CSV data (TEST 5)."""
    parsed = validate_and_parse_structured_data(raw_csv, fmt="csv")
    rows = parsed["rows"]
    columns = parsed["columns"]
    numeric_cols: dict[str, list[float]] = {}
    for col in columns:
        vals: list[float] = []
        ok = True
        for r in rows:
            cell = r.get(col, "")
            if cell in ("", None):
                continue
            try:
                vals.append(float(cell))
            except ValueError:
                ok = False
                break
        if ok and len(vals) >= 2:
            numeric_cols[col] = vals

    col_summaries: dict[str, dict[str, float]] = {}
    for col, vals in numeric_cols.items():
        if HAS_NUMPY:
            arr = np.asarray(vals, dtype=np.float64)
            q25, q50, q75 = np.percentile(arr, [25.0, 50.0, 75.0]).tolist()
            mean_v = float(np.mean(arr))
            std_v = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
            min_v = float(np.min(arr))
            max_v = float(np.max(arr))
        else:
            s_vals = sorted(vals)
            n = len(s_vals)
            mean_v = statistics.fmean(s_vals)
            std_v = statistics.stdev(s_vals) if n > 1 else 0.0
            q50 = statistics.median(s_vals)
            q25 = s_vals[max(0, int(0.25 * (n - 1)))]
            q75 = s_vals[max(0, int(0.75 * (n - 1)))]
            min_v, max_v = s_vals[0], s_vals[-1]
        col_summaries[col] = {
            "count": float(len(vals)),
            "mean": round(mean_v, 6),
            "std": round(std_v, 6),
            "min": round(min_v, 6),
            "q25": round(q25, 6),
            "median": round(q50, 6),
            "q75": round(q75, 6),
            "iqr": round(q75 - q25, 6),
            "max": round(max_v, 6),
        }

    # Pairwise Pearson correlations + OLS regression on first two aligned numeric columns
    correlations: dict[str, float] = {}
    ols_result: dict[str, float | str] | None = None
    best_abs_r: float = -1.0
    num_names = list(numeric_cols.keys())
    for i in range(len(num_names)):
        for j in range(i + 1, len(num_names)):
            c1, c2 = num_names[i], num_names[j]
            v1, v2 = numeric_cols[c1], numeric_cols[c2]
            n = min(len(v1), len(v2))
            if n >= 3:
                x = v1[:n]
                y = v2[:n]
                mx, my = statistics.fmean(x), statistics.fmean(y)
                sxx = sum((xi - mx) ** 2 for xi in x)
                syy = sum((yi - my) ** 2 for yi in y)
                sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=False))
                r_val = sxy / math.sqrt(max(sxx * syy, 1e-18))
                correlations[f"{c1}__vs__{c2}"] = round(r_val, 6)
                if abs(r_val) > best_abs_r and sxx > 1e-12:
                    best_abs_r = abs(r_val)
                    slope = sxy / sxx
                    intercept = my - slope * mx
                    r2 = r_val * r_val
                    resid_ss = max(0.0, syy - slope * sxy)
                    se_slope = math.sqrt((resid_ss / max(n - 2, 1)) / sxx)
                    t_stat = slope / max(se_slope, 1e-12)
                    p_approx = 2.0 * (1.0 - _norm_cdf(abs(t_stat)))
                    ols_result = {
                        "x_column": c1,
                        "y_column": c2,
                        "slope": round(slope, 6),
                        "intercept": round(intercept, 6),
                        "r_squared": round(r2, 6),
                        "t_stat": round(t_stat, 4),
                        "p_value_normal_approx": round(p_approx, 6),
                    }

    return {
        "row_count": parsed["record_count"],
        "duplicates_removed": parsed["duplicates_removed"],
        "columns": columns,
        "numeric_summaries": col_summaries,
        "correlations": correlations,
        "ols_regression": ols_result,
        "checksum_sha256": parsed["checksum_sha256"],
    }


def analyze_ohlcv_and_volatility(
    ohlcv: list[dict[str, float | int | str]],
    *,
    window: int = 30,
    annualization_days: float = 365.0,
) -> dict[str, Any]:
    """Compute realized volatility, Parkinson volatility, EWMA sigma, RSI & drawdowns locally (TEST 2)."""
    if len(ohlcv) < 3:
        raise ValueError("At least 3 OHLCV bars are required for realized volatility analysis.")

    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []

    for idx, bar in enumerate(ohlcv):
        c = float(bar["close"])
        o = float(bar.get("open", c))
        h = float(bar.get("high", max(o, c)))
        lo = float(bar.get("low", min(o, c)))
        v = float(bar.get("volume", 0.0))
        if c <= 0 or o <= 0 or h <= 0 or lo <= 0:
            raise ValueError(f"Non-positive OHLC price at index {idx}.")
        if h + 1e-9 < max(o, c) or lo - 1e-9 > min(o, c):
            raise ValueError(f"OHLC invariant violated at index {idx}: low={lo}, open={o}, close={c}, high={h}.")
        opens.append(o)
        highs.append(h)
        lows.append(lo)
        closes.append(c)
        volumes.append(v)

    if HAS_NUMPY:
        c_arr = np.asarray(closes, dtype=np.float64)
        h_arr = np.asarray(highs, dtype=np.float64)
        l_arr = np.asarray(lows, dtype=np.float64)
        log_rets_arr = np.diff(np.log(c_arr))
        log_rets = log_rets_arr.tolist()
        recent_rets = log_rets_arr[-window:] if len(log_rets_arr) >= window else log_rets_arr
        daily_sigma = float(np.std(recent_rets, ddof=1))
        daily_mu = float(np.mean(recent_rets))
        hl_ratios = np.log(h_arr[-window:] / l_arr[-window:])
        parkinson_daily = float(np.sqrt(np.mean(hl_ratios**2) / (4.0 * math.log(2.0))))
    else:
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        recent = log_rets[-window:] if len(log_rets) >= window else log_rets
        daily_sigma = statistics.stdev(recent)
        daily_mu = statistics.fmean(recent)
        hl_sq = [math.log(highs[i] / lows[i]) ** 2 for i in range(max(0, len(closes) - window), len(closes))]
        parkinson_daily = math.sqrt(statistics.fmean(hl_sq) / (4.0 * math.log(2.0)))

    # RiskMetrics EWMA volatility (lambda = 0.94)
    lam = 0.94
    ewma_var = daily_sigma**2
    for r in log_rets:
        ewma_var = lam * ewma_var + (1.0 - lam) * (r * r)
    ewma_daily_sigma = math.sqrt(max(ewma_var, 0.0))

    realized_vol_annual = daily_sigma * math.sqrt(annualization_days)
    parkinson_vol_annual = parkinson_daily * math.sqrt(annualization_days)
    ewma_vol_annual = ewma_daily_sigma * math.sqrt(annualization_days)
    horizon_30d_sigma = daily_sigma * math.sqrt(30.0)

    # RSI-14
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(max(1, len(closes) - 14), len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(max(1, len(closes) - 14), len(closes))]
    avg_gain = statistics.fmean(gains) if gains else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    rsi_14 = 100.0 if avg_loss <= 1e-12 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    # Max drawdown & rolling 30-day empirical drawdown frequencies
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = min(max_dd, (c - peak) / peak)

    rolling_windows = 0
    dd_5 = dd_10 = dd_20 = 0
    if len(closes) > window:
        for start in range(0, len(closes) - window):
            entry = closes[start]
            min_future_low = min(lows[start + 1 : start + window + 1])
            dd = (min_future_low - entry) / entry
            rolling_windows += 1
            if dd <= -0.05:
                dd_5 += 1
            if dd <= -0.10:
                dd_10 += 1
            if dd <= -0.20:
                dd_20 += 1

    spot = closes[-1]
    ret_7d = ((spot / closes[-8]) - 1.0) * 100.0 if len(closes) >= 8 else 0.0
    ret_30d = ((spot / closes[-31]) - 1.0) * 100.0 if len(closes) >= 31 else ((spot / closes[0]) - 1.0) * 100.0

    # Sharpe ratio (0% risk-free crypto baseline, annualized)
    sharpe = (daily_mu * annualization_days) / max(realized_vol_annual, 1e-9)

    return {
        "bars_analyzed": len(closes),
        "spot_price": round(spot, 4),
        "return_7d_pct": round(ret_7d, 4),
        "return_30d_pct": round(ret_30d, 4),
        "daily_mean_log_return": round(daily_mu, 8),
        "daily_volatility": round(daily_sigma, 8),
        "realized_volatility_annual_pct": round(realized_vol_annual * 100.0, 4),
        "parkinson_volatility_annual_pct": round(parkinson_vol_annual * 100.0, 4),
        "ewma_volatility_annual_pct": round(ewma_vol_annual * 100.0, 4),
        "horizon_30d_volatility_pct": round(horizon_30d_sigma * 100.0, 4),
        "rsi_14": round(rsi_14, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "annualized_sharpe": round(sharpe, 4),
        "rolling_30d_windows": rolling_windows,
        "empirical_30d_drawdown_freq_pct": {
            "drop_ge_5pct": round((dd_5 / rolling_windows) * 100.0, 2) if rolling_windows else None,
            "drop_ge_10pct": round((dd_10 / rolling_windows) * 100.0, 2) if rolling_windows else None,
            "drop_ge_20pct": round((dd_20 / rolling_windows) * 100.0, 2) if rolling_windows else None,
        },
        "log_returns": [round(r, 8) for r in log_rets],
    }


def run_monte_carlo_simulation(
    *,
    spot_price: float,
    daily_volatility: float,
    daily_drift: float = 0.0,
    horizon_days: int = 30,
    num_paths: int = 10_000,
    seed: int = 42,
    historical_log_returns: list[float] | None = None,
) -> dict[str, Any]:
    """Vectorized Antithetic GBM Monte Carlo + Empirical Bootstrap simulation (TEST 7)."""
    if spot_price <= 0 or daily_volatility < 0 or horizon_days < 1 or num_paths < 100:
        raise ValueError("Invalid Monte Carlo parameters.")

    half = max(50, num_paths // 2)
    effective_paths = half * 2
    drift_step = daily_drift - 0.5 * (daily_volatility**2)

    if HAS_NUMPY:
        rng = np.random.default_rng(seed)
        z_half = rng.standard_normal((half, horizon_days))
        z = np.vstack([z_half, -z_half])
        log_increments = drift_step + daily_volatility * z
        cum_log_paths = np.cumsum(log_increments, axis=1)
        price_paths = spot_price * np.exp(cum_log_paths)
        terminal_prices = price_paths[:, -1]
        min_path_prices = np.min(price_paths, axis=1)

        term_rets = (terminal_prices / spot_price) - 1.0
        max_intra_dds = (min_path_prices / spot_price) - 1.0

        p_term_5 = float(np.mean(term_rets <= -0.05))
        p_term_10 = float(np.mean(term_rets <= -0.10))
        p_term_20 = float(np.mean(term_rets <= -0.20))

        p_touch_5 = float(np.mean(max_intra_dds <= -0.05))
        p_touch_10 = float(np.mean(max_intra_dds <= -0.10))
        p_touch_20 = float(np.mean(max_intra_dds <= -0.20))

        q05, q25, q50, q75, q95 = np.percentile(terminal_prices, [5.0, 25.0, 50.0, 75.0, 95.0]).tolist()
        var_95_pct = float(-np.percentile(term_rets, 5.0) * 100.0)
        tail_5 = term_rets[term_rets <= np.percentile(term_rets, 5.0)]
        cvar_95_pct = float(-np.mean(tail_5) * 100.0) if len(tail_5) else var_95_pct

        bootstrap_prob_10: float | None = None
        if historical_log_returns and len(historical_log_returns) >= 15:
            hist_arr = np.asarray(historical_log_returns, dtype=np.float64)
            idx = rng.integers(0, len(hist_arr), size=(effective_paths, horizon_days))
            boot_cum = np.sum(hist_arr[idx], axis=1)
            boot_term_rets = np.exp(boot_cum) - 1.0
            bootstrap_prob_10 = round(float(np.mean(boot_term_rets <= -0.10)) * 100.0, 2)
    else:
        py_rng = random.Random(seed)
        terminal_list: list[float] = []
        min_list: list[float] = []
        for _ in range(half):
            draws = [py_rng.gauss(0.0, 1.0) for _ in range(horizon_days)]
            for sign in (1.0, -1.0):
                acc = 0.0
                min_p = spot_price
                for d in draws:
                    acc += drift_step + daily_volatility * (sign * d)
                    p_now = spot_price * math.exp(acc)
                    if p_now < min_p:
                        min_p = p_now
                terminal_list.append(spot_price * math.exp(acc))
                min_list.append(min_p)
        term_rets_l = [(p / spot_price) - 1.0 for p in terminal_list]
        intra_dds_l = [(m / spot_price) - 1.0 for m in min_list]
        p_term_5 = sum(1 for r in term_rets_l if r <= -0.05) / effective_paths
        p_term_10 = sum(1 for r in term_rets_l if r <= -0.10) / effective_paths
        p_term_20 = sum(1 for r in term_rets_l if r <= -0.20) / effective_paths
        p_touch_5 = sum(1 for r in intra_dds_l if r <= -0.05) / effective_paths
        p_touch_10 = sum(1 for r in intra_dds_l if r <= -0.10) / effective_paths
        p_touch_20 = sum(1 for r in intra_dds_l if r <= -0.20) / effective_paths
        s_term = sorted(terminal_list)
        q05 = s_term[int(0.05 * (effective_paths - 1))]
        q25 = s_term[int(0.25 * (effective_paths - 1))]
        q50 = s_term[int(0.50 * (effective_paths - 1))]
        q75 = s_term[int(0.75 * (effective_paths - 1))]
        q95 = s_term[int(0.95 * (effective_paths - 1))]
        s_rets = sorted(term_rets_l)
        cutoff_idx = max(1, int(0.05 * effective_paths))
        var_95_pct = -s_rets[cutoff_idx] * 100.0
        cvar_95_pct = -statistics.fmean(s_rets[:cutoff_idx]) * 100.0
        bootstrap_prob_10 = None

    # Standard error for binomial probability estimate at 10% threshold
    se_10 = math.sqrt(max(p_term_10 * (1.0 - p_term_10), 0.0) / effective_paths)

    return {
        "engine": "numpy_vectorized_antithetic_gbm" if HAS_NUMPY else "python_antithetic_gbm",
        "spot_price": round(spot_price, 2),
        "horizon_days": horizon_days,
        "num_paths": effective_paths,
        "seed": seed,
        "terminal_drop_probabilities_pct": {
            "drop_ge_5pct": round(p_term_5 * 100.0, 2),
            "drop_ge_10pct": round(p_term_10 * 100.0, 2),
            "drop_ge_20pct": round(p_term_20 * 100.0, 2),
        },
        "intra_horizon_touch_probabilities_pct": {
            "touch_drop_ge_5pct": round(p_touch_5 * 100.0, 2),
            "touch_drop_ge_10pct": round(p_touch_10 * 100.0, 2),
            "touch_drop_ge_20pct": round(p_touch_20 * 100.0, 2),
        },
        "terminal_price_quantiles": {
            "p05": round(q05, 2),
            "p25": round(q25, 2),
            "median_p50": round(q50, 2),
            "p75": round(q75, 2),
            "p95": round(q95, 2),
        },
        "var_95_pct": round(var_95_pct, 2),
        "cvar_95_pct": round(cvar_95_pct, 2),
        "mc_95ci_halfwidth_drop10_pct": round(1.96 * se_10 * 100.0, 3),
        "historical_bootstrap_drop_ge_10pct": bootstrap_prob_10,
    }


def score_and_deduplicate_sources(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic SHA-256 + DOI/URI deduplication, citation audit & source quality scoring."""
    seen_hashes: set[str] = set()
    seen_uris: set[str] = set()
    unique: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    duplicates = 0

    for src in sources:
        txt = str(src.get("extracted_text") or "")
        h = str(src.get("checksum_sha256") or compute_sha256_integrity(txt)["sha256"])
        uri = str(src.get("uri") or src.get("filename") or "").strip().lower()
        if h in seen_hashes or (uri and uri in seen_uris and uri not in {"manual", "inline"}):
            duplicates += 1
            continue
        seen_hashes.add(h)
        if uri:
            seen_uris.add(uri)
        unique.append(src)

        # Deterministic quality score [0.0, 1.0]
        score = 0.25
        if len(txt) >= 200:
            score += 0.25
        if uri.startswith(("https://", "http://", "doi:")):
            score += 0.20
        meta = src.get("metadata_json") or {}
        if meta.get("authors") or meta.get("author") or src.get("author"):
            score += 0.15
        if meta.get("published_at") or meta.get("published_date") or "202" in txt:
            score += 0.15
        quality_rows.append(
            {
                "source_id": src["id"],
                "title": src.get("title", ""),
                "checksum_sha256": h,
                "citation_complete": bool(uri and src.get("title")),
                "quality_score": round(min(score, 1.0), 3),
            }
        )

    return {
        "unique_sources": unique,
        "unique_source_ids": [u["id"] for u in unique],
        "duplicates_skipped": duplicates,
        "quality_scores": quality_rows,
        "mean_quality_score": round(
            statistics.fmean([r["quality_score"] for r in quality_rows]), 3
        )
        if quality_rows
        else 0.0,
    }


_BULLISH_PATTERNS = re.compile(
    r"\b(bullish|wzrost\w*|rally|inflow\w*|napływ\w*|upside|breakout|akumulacj\w*|strong support)\b",
    re.IGNORECASE,
)
_BEARISH_PATTERNS = re.compile(
    r"\b(bearish|spadek\w*|spadk\w*|crash|drawdown|outflow\w*|odpływ\w*|downside|korekt\w*|sell-?off|capitulation)\b",
    re.IGNORECASE,
)


def detect_rule_based_contradictions(
    claims: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rule-based & lexical/directional contradiction detector across sources and claims."""
    contradictions: list[dict[str, Any]] = []
    bullish_sources: list[dict[str, Any]] = []
    bearish_sources: list[dict[str, Any]] = []

    for c in claims:
        if c.get("evidence_type") == "contradicting":
            contradictions.append(
                {
                    "type": "explicit_claim_polarity",
                    "statement": c.get("statement", ""),
                    "source_id": c.get("source_id"),
                    "source_title": c.get("source_title", ""),
                    "reason": "Jawnie oznaczony dowód sprzeczny w metadanych źródła.",
                }
            )

    for src in sources:
        txt = f"{src.get('title', '')} {src.get('extracted_text', '')}"
        bull_hits = len(_BULLISH_PATTERNS.findall(txt))
        bear_hits = len(_BEARISH_PATTERNS.findall(txt))
        if bull_hits >= 2 and bull_hits > bear_hits * 1.5:
            bullish_sources.append(src)
        elif bear_hits >= 2 and bear_hits > bull_hits * 1.5:
            bearish_sources.append(src)

    if bullish_sources and bearish_sources:
        b_src = bullish_sources[0]
        r_src = bearish_sources[0]
        contradictions.append(
            {
                "type": "cross_source_directional_divergence",
                "statement": (
                    f"Rozbieżność kierunkowa między '{b_src.get('title')}' (przewaga sygnałów wzrostowych/napływowych) "
                    f"a '{r_src.get('title')}' (przewaga sygnałów spadkowych/ryzyka obsunięcia)."
                ),
                "source_id": r_src.get("id"),
                "counter_source_id": b_src.get("id"),
                "source_title": r_src.get("title", ""),
                "reason": "Regułowa detekcja przeciwstawnych tez kierunkowych pomiędzy dokumentami źródłowymi.",
            }
        )

    unattributed = [c for c in claims if not c.get("source_id")]
    return {
        "contradictions": contradictions,
        "contradiction_count": len(contradictions),
        "bullish_source_count": len(bullish_sources),
        "bearish_source_count": len(bearish_sources),
        "unattributed_claim_count": len(unattributed),
        "requires_semantic_arbitration": bool(bullish_sources and bearish_sources and len(sources) >= 3),
    }


def extract_ohlcv_from_sources(sources: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Extract embedded OHLCV series or reconstruct calibrated daily bars from telemetry sources."""
    for src in sources:
        meta = src.get("metadata_json") or {}
        if isinstance(meta.get("ohlcv_bars"), list) and len(meta["ohlcv_bars"]) >= 10:
            return list(meta["ohlcv_bars"])
        txt = str(src.get("extracted_text") or "")
        if "close" in txt.lower() and "," in txt and "\n" in txt:
            try:
                parsed = validate_and_parse_structured_data(txt, fmt="csv")
                if "close" in [c.lower() for c in parsed["columns"]] and len(parsed["rows"]) >= 10:
                    bars: list[dict[str, float]] = []
                    for r in parsed["rows"]:
                        low_map = {k.lower(): v for k, v in r.items()}
                        c = float(low_map["close"])
                        bars.append(
                            {
                                "open": float(low_map.get("open", c)),
                                "high": float(low_map.get("high", c)),
                                "low": float(low_map.get("low", c)),
                                "close": c,
                                "volume": float(low_map.get("volume", 0.0)),
                            }
                        )
                    return bars
            except Exception:
                pass
    return []


# ============================================================================
# SECTION 5 — MULTI-TIER CACHE (L1 PROCESS -> L2 REDIS/FILE -> L3 POSTGRES/STORE)
# ============================================================================


@dataclass
class CacheRecordEnvelope:
    cache_key: str
    source_id: str
    provider: str
    retrieved_at: str
    observed_at: str
    expires_at: str
    content_hash: str
    schema_version: str
    verification_status: str
    payload: Any

    def is_fresh(self, *, now: datetime | None = None, max_age_seconds: float | None = None) -> bool:
        ref = now or _utcnow()
        exp = _parse_iso(self.expires_at)
        if ref > exp:
            return False
        if max_age_seconds is not None:
            ret = _parse_iso(self.retrieved_at)
            if (ref - ret).total_seconds() > max_age_seconds:
                return False
        expected_hash = compute_sha256_integrity(self.payload)["sha256"]
        return expected_hash == self.content_hash and self.verification_status == "VERIFIED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheRecordEnvelope:
        return cls(
            cache_key=str(data["cache_key"]),
            source_id=str(data["source_id"]),
            provider=str(data["provider"]),
            retrieved_at=str(data["retrieved_at"]),
            observed_at=str(data["observed_at"]),
            expires_at=str(data["expires_at"]),
            content_hash=str(data["content_hash"]),
            schema_version=str(data.get("schema_version", "1.0")),
            verification_status=str(data.get("verification_status", "VERIFIED")),
            payload=data.get("payload"),
        )


@dataclass
class CacheLookupOutcome:
    hit: bool
    tier: Literal["L1_MEMORY", "L2_CACHE", "L3_DATABASE", "MISS"]
    record: CacheRecordEnvelope | None = None
    stale_record: CacheRecordEnvelope | None = None


class MultiTierDataCache:
    """Three-tier data cache enforcing explicit TTL freshness & SHA-256 verification.

    Lookup order for Layer B Public Data acquisition (per Section 2B & Section 5):
      1. L3 persistent store / PostgreSQL (`l3_store`) + L1 in-process hot map
      2. L2 Redis (if `REDIS_URL` reachable) or local L2 cache file
      3. Evaluates TTL freshness (`expires_at` and optional per-task `max_age_seconds`)
    Never masks expired records as fresh hits; exposes `stale_record` solely for explicit
    degraded fallback when an upstream public API fails (Section 6).
    """

    def __init__(
        self,
        *,
        l2_file_path: str | Path | None = None,
        redis_url: str | None = None,
        l3_backing_dict: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._l1: dict[str, CacheRecordEnvelope] = {}
        self._l3: dict[str, dict[str, Any]] = l3_backing_dict if l3_backing_dict is not None else {}
        self._l2_path = Path(l2_file_path) if l2_file_path else None
        self._redis_client: Any | None = None
        eff_redis = redis_url or os.environ.get("APEX_REDIS_URL") or os.environ.get("REDIS_URL")
        if eff_redis and redis_lib is not None:
            try:
                client = redis_lib.Redis.from_url(eff_redis, decode_responses=True, socket_connect_timeout=0.5)
                client.ping()
                self._redis_client = client
            except Exception:
                self._redis_client = None

    def bind_l3_store(self, l3_backing_dict: dict[str, dict[str, Any]]) -> None:
        self._l3 = l3_backing_dict

    def clear_l1(self) -> None:
        self._l1.clear()

    def _read_l2(self, cache_key: str) -> CacheRecordEnvelope | None:
        if self._redis_client is not None:
            try:
                raw = self._redis_client.get(f"apex:l2:{cache_key}")
                if raw:
                    return CacheRecordEnvelope.from_dict(json.loads(raw))
            except Exception:
                pass
        if self._l2_path and self._l2_path.exists():
            try:
                blob = json.loads(self._l2_path.read_text(encoding="utf-8"))
                if cache_key in blob:
                    return CacheRecordEnvelope.from_dict(blob[cache_key])
            except Exception:
                pass
        return None

    def _write_l2(self, env: CacheRecordEnvelope, ttl_seconds: int) -> None:
        if self._redis_client is not None:
            try:
                self._redis_client.setex(
                    f"apex:l2:{env.cache_key}",
                    max(60, ttl_seconds * 4),  # retain beyond fresh TTL for explicit outage fallback inspection
                    json.dumps(env.to_dict(), ensure_ascii=False),
                )
            except Exception:
                pass
        if self._l2_path is not None:
            try:
                self._l2_path.parent.mkdir(parents=True, exist_ok=True)
                existing: dict[str, Any] = {}
                if self._l2_path.exists():
                    existing = json.loads(self._l2_path.read_text(encoding="utf-8"))
                existing[env.cache_key] = env.to_dict()
                self._l2_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def lookup(
        self,
        cache_key: str,
        *,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> CacheLookupOutcome:
        ref = now or _utcnow()
        stale_candidate: CacheRecordEnvelope | None = None

        # 1. Check L1 process memory
        if cache_key in self._l1:
            env = self._l1[cache_key]
            if env.is_fresh(now=ref, max_age_seconds=max_age_seconds):
                return CacheLookupOutcome(hit=True, tier="L1_MEMORY", record=env)
            stale_candidate = env

        # 2. Check L3 local database / durable workspace store
        if cache_key in self._l3:
            env = CacheRecordEnvelope.from_dict(self._l3[cache_key])
            if env.is_fresh(now=ref, max_age_seconds=max_age_seconds):
                self._l1[cache_key] = env
                return CacheLookupOutcome(hit=True, tier="L3_DATABASE", record=env)
            stale_candidate = stale_candidate or env

        # 3. Check L2 Redis / local cache file
        l2_env = self._read_l2(cache_key)
        if l2_env is not None:
            if l2_env.is_fresh(now=ref, max_age_seconds=max_age_seconds):
                self._l1[cache_key] = l2_env
                self._l3[cache_key] = l2_env.to_dict()
                return CacheLookupOutcome(hit=True, tier="L2_CACHE", record=l2_env)
            stale_candidate = stale_candidate or l2_env

        return CacheLookupOutcome(hit=False, tier="MISS", record=None, stale_record=stale_candidate)

    def put(
        self,
        *,
        cache_key: str,
        payload: Any,
        provider: str,
        ttl_seconds: int,
        source_id: str | None = None,
        observed_at: str | None = None,
        schema_version: str = "1.0",
        verification_status: str = "VERIFIED",
        now: datetime | None = None,
    ) -> CacheRecordEnvelope:
        ref = now or _utcnow()
        digest = compute_sha256_integrity(payload)["sha256"]
        env = CacheRecordEnvelope(
            cache_key=cache_key,
            source_id=source_id or str(uuid.uuid4()),
            provider=provider,
            retrieved_at=ref.isoformat(),
            observed_at=observed_at or ref.isoformat(),
            expires_at=(ref + timedelta(seconds=ttl_seconds)).isoformat(),
            content_hash=digest,
            schema_version=schema_version,
            verification_status=verification_status,
            payload=payload,
        )
        self._l1[cache_key] = env
        self._l3[cache_key] = env.to_dict()
        self._write_l2(env, ttl_seconds=ttl_seconds)
        return env


# ============================================================================
# SECTION 7 — MANDATORY STEP & AGGREGATE TELEMETRY
# ============================================================================


@dataclass
class ExecutionStepTelemetry:
    agent_name: str
    task_id: str
    task_category: str
    execution_mode: str
    provider: str
    model: str
    external_api_invoked: bool
    llm_invoked: bool
    cache_hit: bool
    cache_tier: str
    input_tokens: int
    output_tokens: int
    actual_cost_usd: float
    estimated_cost: float
    cost_is_estimated: bool
    latency_ms: float
    execution_status: str
    validation_status: str
    routing_reason: str
    llm_justification: str | None = None
    idempotency_key: str | None = None
    recorded_at: str = field(default_factory=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelemetryLedger:
    """Tracks real measured local vs external API vs LLM executions & cost/cache savings."""

    def __init__(self, backing_list: list[dict[str, Any]] | None = None) -> None:
        self._records: list[dict[str, Any]] = backing_list if backing_list is not None else []
        self.prevented_unnecessary_calls: int = 0

    @property
    def records(self) -> list[dict[str, Any]]:
        return self._records

    def bind_store(self, backing_list: list[dict[str, Any]]) -> None:
        self._records = backing_list

    def record(self, entry: ExecutionStepTelemetry) -> dict[str, Any]:
        d = entry.to_dict()
        self._records.append(d)
        return d

    def summarize(self, subset: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = subset if subset is not None else self._records
        total = len(rows)
        if total == 0:
            return {
                "total_operations": 0,
                "local_operations_count": 0,
                "external_operations_count": 0,
                "local_operations_pct": 100.0,
                "llm_invocations_count": 0,
                "public_api_invocations_count": 0,
                "cache_hits_count": 0,
                "actual_cost_usd_total": 0.0,
                "estimated_cost_usd_total": 0.0,
                "cost_by_provider": {},
                "cost_by_agent": {},
                "average_latency_ms": 0.0,
                "unnecessary_calls_executed": 0,
                "prevented_unnecessary_calls": self.prevented_unnecessary_calls,
                "cache_savings": {
                    "saved_public_api_calls": 0,
                    "saved_llm_calls": 0,
                },
            }

        local_ops = sum(
            1 for r in rows if not r.get("external_api_invoked") and not r.get("llm_invoked")
        )
        ext_ops = total - local_ops
        llm_calls = sum(1 for r in rows if r.get("llm_invoked"))
        pub_api_calls = sum(1 for r in rows if r.get("external_api_invoked"))
        cache_hits = sum(1 for r in rows if r.get("cache_hit"))

        cost_by_prov: dict[str, float] = {}
        cost_by_agent: dict[str, float] = {}
        actual_total = 0.0
        est_total = 0.0
        unnecessary_executed = 0

        for r in rows:
            prov = str(r.get("provider") or "local")
            ag = str(r.get("agent_name") or "system")
            act_c = float(r.get("actual_cost_usd") or 0.0)
            est_c = float(r.get("estimated_cost") or 0.0)
            actual_total += act_c
            est_total += est_c
            eff_c = act_c if not r.get("cost_is_estimated") else est_c
            cost_by_prov[prov] = round(cost_by_prov.get(prov, 0.0) + eff_c, 6)
            cost_by_agent[ag] = round(cost_by_agent.get(ag, 0.0) + eff_c, 6)
            cat = str(r.get("task_category") or "")
            if r.get("llm_invoked") and cat in {c.value for c in LLM_FORBIDDEN_TASK_CATEGORIES}:
                unnecessary_executed += 1

        avg_lat = statistics.fmean(float(r.get("latency_ms") or 0.0) for r in rows)
        return {
            "total_operations": total,
            "local_operations_count": local_ops,
            "external_operations_count": ext_ops,
            "local_operations_pct": round((local_ops / total) * 100.0, 2),
            "llm_invocations_count": llm_calls,
            "public_api_invocations_count": pub_api_calls,
            "cache_hits_count": cache_hits,
            "actual_cost_usd_total": round(actual_total, 6),
            "estimated_cost_usd_total": round(est_total, 6),
            "cost_by_provider": cost_by_prov,
            "cost_by_agent": cost_by_agent,
            "average_latency_ms": round(avg_lat, 2),
            "unnecessary_calls_executed": unnecessary_executed,
            "prevented_unnecessary_calls": self.prevented_unnecessary_calls,
            "cache_savings": {
                "saved_public_api_calls": cache_hits,
                "saved_llm_calls": self.prevented_unnecessary_calls,
            },
        }


# ============================================================================
# SECTION 3 & SECTION 9 — INTELLIGENT EXECUTION ROUTER + SECURITY/COST GUARDS
# ============================================================================

_SECRET_REDACT_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|apxkey_[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{12,}|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----)",
    re.IGNORECASE,
)


def redact_confidential_text(text: str, *, for_external_cloud: bool = True) -> str:
    """Strip credentials, private keys, and `[CONFIDENTIAL]...[/CONFIDENTIAL]` blocks (including truncated tags)."""
    cleaned = _SECRET_REDACT_RE.sub("[REDACTED_SECRET]", text)
    if for_external_cloud:
        cleaned = re.sub(
            r"\[CONFIDENTIAL\][\s\S]*?\[/CONFIDENTIAL\]",
            "[REDACTED_CONFIDENTIAL_SEGMENT]",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\[CONFIDENTIAL\][^\n]*",
            "[REDACTED_CONFIDENTIAL_SEGMENT]",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned


@dataclass(frozen=True)
class RoutingEvaluation:
    """Explicit 10-criteria decision record produced by `ExecutionRouter` (Section 3)."""

    task_category: TaskCategory
    selected_mode: ExecutionMode
    # 10 mandatory assessment dimensions:
    criterion_1_task_type: str
    criterion_2_required_accuracy: Literal["exact_deterministic", "numerical_statistical", "semantic_interpretive"]
    criterion_3_local_data_available: bool
    criterion_4_data_freshness_ok: bool
    criterion_5_local_library_capable: bool
    criterion_6_semantic_reasoning_required: bool
    criterion_7_estimated_external_cost_usd: float
    criterion_8_expected_latency_ms: int
    criterion_9_error_or_hallucination_risk: Literal["none_local_exact", "low", "medium", "high_if_llm_used"]
    criterion_10_independently_verifiable: bool
    reason: str
    llm_justification: str | None = None
    downgraded_from_llm_request: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["task_category"] = self.task_category.value
        d["selected_mode"] = self.selected_mode.value
        return d


@dataclass
class RunBudgetTracker:
    """Enforces per-run LLM call cap, per-agent token cap, per-study cost cap & idempotency."""

    max_llm_calls_per_run: int = 2
    max_tokens_per_agent: int = 3000
    max_cost_budget_usd: float = 0.35
    llm_calls_used: int = 0
    cost_used_usd: float = 0.0
    tokens_by_agent: dict[str, int] = field(default_factory=dict)
    completed_idempotency_keys: dict[str, Any] = field(default_factory=dict)

    def can_invoke_llm(self, agent_slug: str, estimated_cost: float = 0.0) -> tuple[bool, str]:
        contract = BUILTIN_AGENT_CONTRACTS.get(agent_slug)
        if contract and not contract.llm_requirements.get("allowed", False):
            return False, f"Agent '{agent_slug}' contract forbids LLM calls."
        if self.llm_calls_used >= self.max_llm_calls_per_run:
            return False, f"Run LLM call cap reached ({self.llm_calls_used}/{self.max_llm_calls_per_run})."
        if self.cost_used_usd + estimated_cost > self.max_cost_budget_usd:
            return False, f"Study cost budget cap reached (${self.cost_used_usd:.4f}/${self.max_cost_budget_usd:.4f})."
        used_tok = self.tokens_by_agent.get(agent_slug, 0)
        cap_tok = min(
            self.max_tokens_per_agent,
            contract.execution_budget.max_tokens if contract and contract.execution_budget.max_tokens > 0 else self.max_tokens_per_agent,
        )
        if used_tok >= cap_tok:
            return False, f"Agent '{agent_slug}' token cap reached ({used_tok}/{cap_tok})."
        return True, "within_budget"


class ExecutionRouter:
    """Central Local-First Execution Router & Gatekeeper (Sections 3, 4, 6, 7, 9).

    Agents and services MUST route tasks through `ExecutionRouter` rather than invoking
    external LLM providers or uncached public APIs directly.
    """

    def __init__(
        self,
        *,
        cache: MultiTierDataCache,
        telemetry: TelemetryLedger,
    ) -> None:
        self.cache = cache
        self.telemetry = telemetry

    def decide(
        self,
        *,
        task_category: TaskCategory,
        local_data_available: bool = True,
        data_freshness_ok: bool = True,
        semantic_complexity: bool = False,
        has_local_model: bool = False,
        has_external_llm: bool = False,
        requested_llm: bool = False,
        llm_justification: str | None = None,
        estimated_llm_cost_usd: float = 0.002,
    ) -> RoutingEvaluation:
        # 1. Layer A: Pure deterministic operations
        if task_category in {
            TaskCategory.CHECKSUM_HASH,
            TaskCategory.SCHEMA_VALIDATION,
            TaskCategory.DATA_PARSING_DEDUP,
            TaskCategory.SOURCE_SCORING_CITATIONS,
            TaskCategory.RULE_CONTRADICTION_CHECK,
            TaskCategory.CHART_AND_TABLE_BUILD,
            TaskCategory.DAG_BUDGET_SUPERVISION,
        }:
            downgraded = requested_llm
            if downgraded:
                self.telemetry.prevented_unnecessary_calls += 1
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.LOCAL_DETERMINISTIC,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="exact_deterministic",
                criterion_3_local_data_available=True,
                criterion_4_data_freshness_ok=True,
                criterion_5_local_library_capable=True,
                criterion_6_semantic_reasoning_required=False,
                criterion_7_estimated_external_cost_usd=0.0,
                criterion_8_expected_latency_ms=1,
                criterion_9_error_or_hallucination_risk="high_if_llm_used",
                criterion_10_independently_verifiable=True,
                reason="Deterministic local Python/schema/SHA-256 algorithm guarantees 100% exactness at zero external cost.",
                llm_justification=None,
                downgraded_from_llm_request=downgraded,
            )

        # 2. Layer A: Local statistical / mathematical / simulation operations
        if task_category in {
            TaskCategory.CSV_STATISTICAL_ANALYSIS,
            TaskCategory.OHLCV_VOLATILITY,
            TaskCategory.MONTE_CARLO_SIMULATION,
            TaskCategory.BOOTSTRAP_HYPOTHESIS_TEST,
        }:
            downgraded = requested_llm
            if downgraded:
                self.telemetry.prevented_unnecessary_calls += 1
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.LOCAL_STATISTICAL,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="numerical_statistical",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=data_freshness_ok,
                criterion_5_local_library_capable=True,
                criterion_6_semantic_reasoning_required=False,
                criterion_7_estimated_external_cost_usd=0.0,
                criterion_8_expected_latency_ms=5,
                criterion_9_error_or_hallucination_risk="high_if_llm_used",
                criterion_10_independently_verifiable=True,
                reason="Quantitative/statistical computation executed locally via NumPy/statistics kernels; LLMs are forbidden for numerical math.",
                llm_justification=None,
                downgraded_from_llm_request=downgraded,
            )

        # 3. Layer B: Public data acquisition (cache-first!)
        if task_category in {
            TaskCategory.PUBLIC_MARKET_DATA_FETCH,
            TaskCategory.PUBLIC_LITERATURE_FETCH,
        }:
            if requested_llm:
                self.telemetry.prevented_unnecessary_calls += 1
            if local_data_available and data_freshness_ok:
                return RoutingEvaluation(
                    task_category=task_category,
                    selected_mode=ExecutionMode.LOCAL_DETERMINISTIC,
                    criterion_1_task_type=task_category.value,
                    criterion_2_required_accuracy="exact_deterministic",
                    criterion_3_local_data_available=True,
                    criterion_4_data_freshness_ok=True,
                    criterion_5_local_library_capable=True,
                    criterion_6_semantic_reasoning_required=False,
                    criterion_7_estimated_external_cost_usd=0.0,
                    criterion_8_expected_latency_ms=1,
                    criterion_9_error_or_hallucination_risk="none_local_exact",
                    criterion_10_independently_verifiable=True,
                    reason="Fresh verified record already available in local L1/L2/L3 cache; skipping public API call.",
                    downgraded_from_llm_request=requested_llm,
                )
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.PUBLIC_DATA_API,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="exact_deterministic",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=False,
                criterion_5_local_library_capable=False,
                criterion_6_semantic_reasoning_required=False,
                criterion_7_estimated_external_cost_usd=0.0,
                criterion_8_expected_latency_ms=350,
                criterion_9_error_or_hallucination_risk="low",
                criterion_10_independently_verifiable=True,
                reason="Local cache missing or expired past TTL; invoking dedicated Public Data API adapter (zero LLM).",
                downgraded_from_llm_request=requested_llm,
            )

        # 4. Layer C / Hybrid: Semantic synthesis, multi-doc conflict critique, hypothesis generation
        if not semantic_complexity or not (llm_justification and llm_justification.strip()):
            if requested_llm:
                self.telemetry.prevented_unnecessary_calls += 1
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.LOCAL_DETERMINISTIC,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="exact_deterministic",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=data_freshness_ok,
                criterion_5_local_library_capable=True,
                criterion_6_semantic_reasoning_required=False,
                criterion_7_estimated_external_cost_usd=0.0,
                criterion_8_expected_latency_ms=3,
                criterion_9_error_or_hallucination_risk="low",
                criterion_10_independently_verifiable=True,
                reason="Task solvable via local structured rules or lacked mandatory semantic justification for LLM.",
                downgraded_from_llm_request=requested_llm,
            )

        if task_category == TaskCategory.HYBRID_REPORT_SYNTHESIS and (has_local_model or has_external_llm):
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.HYBRID,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="semantic_interpretive",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=data_freshness_ok,
                criterion_5_local_library_capable=False,
                criterion_6_semantic_reasoning_required=True,
                criterion_7_estimated_external_cost_usd=0.0 if has_local_model else estimated_llm_cost_usd,
                criterion_8_expected_latency_ms=1200,
                criterion_9_error_or_hallucination_risk="medium",
                criterion_10_independently_verifiable=True,
                reason="HYBRID split: tables, citations & Monte Carlo metrics built locally; model invoked solely for executive linguistic synthesis.",
                llm_justification=llm_justification.strip(),
            )

        if has_local_model:
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.LOCAL_MODEL,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="semantic_interpretive",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=data_freshness_ok,
                criterion_5_local_library_capable=False,
                criterion_6_semantic_reasoning_required=True,
                criterion_7_estimated_external_cost_usd=0.0,
                criterion_8_expected_latency_ms=950,
                criterion_9_error_or_hallucination_risk="medium",
                criterion_10_independently_verifiable=True,
                reason="Justified semantic reasoning routed to local/LAN model (LOCAL_MODEL) at $0 marginal API cost.",
                llm_justification=llm_justification.strip(),
            )

        if has_external_llm:
            return RoutingEvaluation(
                task_category=task_category,
                selected_mode=ExecutionMode.EXTERNAL_LLM,
                criterion_1_task_type=task_category.value,
                criterion_2_required_accuracy="semantic_interpretive",
                criterion_3_local_data_available=local_data_available,
                criterion_4_data_freshness_ok=data_freshness_ok,
                criterion_5_local_library_capable=False,
                criterion_6_semantic_reasoning_required=True,
                criterion_7_estimated_external_cost_usd=estimated_llm_cost_usd,
                criterion_8_expected_latency_ms=1400,
                criterion_9_error_or_hallucination_risk="medium",
                criterion_10_independently_verifiable=True,
                reason="Justified complex semantic reasoning routed to EXTERNAL_LLM with redaction and token caps.",
                llm_justification=llm_justification.strip(),
            )

        return RoutingEvaluation(
            task_category=task_category,
            selected_mode=ExecutionMode.LOCAL_DETERMINISTIC,
            criterion_1_task_type=task_category.value,
            criterion_2_required_accuracy="semantic_interpretive",
            criterion_3_local_data_available=local_data_available,
            criterion_4_data_freshness_ok=data_freshness_ok,
            criterion_5_local_library_capable=True,
            criterion_6_semantic_reasoning_required=True,
            criterion_7_estimated_external_cost_usd=0.0,
            criterion_8_expected_latency_ms=5,
            criterion_9_error_or_hallucination_risk="low",
            criterion_10_independently_verifiable=True,
            reason="No active LLM provider available; executing deterministic local synthesis fallback.",
            llm_justification=llm_justification.strip(),
        )

    def execute_local_task(
        self,
        *,
        agent_name: str,
        task_id: str,
        task_category: TaskCategory,
        fn: Callable[[], dict[str, Any]],
        validator: Callable[[dict[str, Any]], bool] | None = None,
        requested_llm: bool = False,
        budget: RunBudgetTracker | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], ExecutionStepTelemetry, RoutingEvaluation]:
        """Execute a Layer A local deterministic or statistical task and record real telemetry."""
        t0 = time.perf_counter()
        decision = self.decide(
            task_category=task_category,
            local_data_available=True,
            data_freshness_ok=True,
            semantic_complexity=False,
            requested_llm=requested_llm,
        )
        if budget and idempotency_key and idempotency_key in budget.completed_idempotency_keys:
            cached_out = budget.completed_idempotency_keys[idempotency_key]
            lat = round((time.perf_counter() - t0) * 1000.0, 3)
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode=decision.selected_mode.value,
                provider="local_python",
                model="none",
                external_api_invoked=False,
                llm_invoked=False,
                cache_hit=True,
                cache_tier="IDEMPOTENCY_LEDGER",
                input_tokens=0,
                output_tokens=0,
                actual_cost_usd=0.0,
                estimated_cost=0.0,
                cost_is_estimated=False,
                latency_ms=lat,
                execution_status="SUCCEEDED",
                validation_status="VALIDATED",
                routing_reason=decision.reason,
                idempotency_key=idempotency_key,
            )
            self.telemetry.record(tel)
            return cached_out, tel, decision

        result = fn()
        valid = validator(result) if validator is not None else isinstance(result, dict)
        if budget and idempotency_key and valid:
            budget.completed_idempotency_keys[idempotency_key] = result
        lat = round((time.perf_counter() - t0) * 1000.0, 3)
        tel = ExecutionStepTelemetry(
            agent_name=agent_name,
            task_id=task_id,
            task_category=task_category.value,
            execution_mode=decision.selected_mode.value,
            provider="local_numpy" if (HAS_NUMPY and decision.selected_mode == ExecutionMode.LOCAL_STATISTICAL) else "local_python",
            model="none",
            external_api_invoked=False,
            llm_invoked=False,
            cache_hit=False,
            cache_tier="NONE",
            input_tokens=0,
            output_tokens=0,
            actual_cost_usd=0.0,
            estimated_cost=0.0,
            cost_is_estimated=False,
            latency_ms=lat,
            execution_status="SUCCEEDED" if valid else "VALIDATION_FAILED",
            validation_status="VALIDATED" if valid else "FAILED",
            routing_reason=decision.reason,
            idempotency_key=idempotency_key,
        )
        self.telemetry.record(tel)
        return result, tel, decision

    def fetch_public_data_controlled(
        self,
        *,
        agent_name: str,
        task_id: str,
        task_category: TaskCategory,
        cache_key: str,
        provider_name: str,
        ttl_seconds: int,
        fetcher_fn: Callable[[], Any],
        validator_fn: Callable[[Any], bool],
        max_age_seconds: float | None = None,
        allow_stale_on_error: bool = True,
        retries: int = 2,
        backoff_base_seconds: float = 0.08,
    ) -> tuple[dict[str, Any], ExecutionStepTelemetry, RoutingEvaluation]:
        """Layer B cache-first Public Data API execution with retry, verification & honest fallback."""
        t0 = time.perf_counter()
        lookup = self.cache.lookup(cache_key, max_age_seconds=max_age_seconds)
        if lookup.hit and lookup.record is not None:
            decision = self.decide(
                task_category=task_category,
                local_data_available=True,
                data_freshness_ok=True,
            )
            lat = round((time.perf_counter() - t0) * 1000.0, 3)
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode="CACHE_HIT",
                provider=lookup.record.provider,
                model="none",
                external_api_invoked=False,
                llm_invoked=False,
                cache_hit=True,
                cache_tier=lookup.tier,
                input_tokens=0,
                output_tokens=0,
                actual_cost_usd=0.0,
                estimated_cost=0.0,
                cost_is_estimated=False,
                latency_ms=lat,
                execution_status="SUCCEEDED",
                validation_status="VERIFIED_CACHE_HASH",
                routing_reason=decision.reason,
            )
            self.telemetry.record(tel)
            return {
                "status": "OK",
                "cache_hit": True,
                "cache_tier": lookup.tier,
                "envelope": lookup.record.to_dict(),
                "data": lookup.record.payload,
            }, tel, decision

        decision = self.decide(
            task_category=task_category,
            local_data_available=lookup.stale_record is not None,
            data_freshness_ok=False,
        )
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                raw_data = fetcher_fn()
                if not validator_fn(raw_data):
                    raise ValueError(f"Public API response from '{provider_name}' failed structural integrity check.")
                env = self.cache.put(
                    cache_key=cache_key,
                    payload=raw_data,
                    provider=provider_name,
                    ttl_seconds=ttl_seconds,
                    verification_status="VERIFIED",
                )
                lat = round((time.perf_counter() - t0) * 1000.0, 3)
                tel = ExecutionStepTelemetry(
                    agent_name=agent_name,
                    task_id=task_id,
                    task_category=task_category.value,
                    execution_mode=ExecutionMode.PUBLIC_DATA_API.value,
                    provider=provider_name,
                    model="none",
                    external_api_invoked=True,
                    llm_invoked=False,
                    cache_hit=False,
                    cache_tier="MISS",
                    input_tokens=0,
                    output_tokens=0,
                    actual_cost_usd=0.0,
                    estimated_cost=0.0,
                    cost_is_estimated=False,
                    latency_ms=lat,
                    execution_status="SUCCEEDED",
                    validation_status="VERIFIED",
                    routing_reason=decision.reason,
                )
                self.telemetry.record(tel)
                return {
                    "status": "OK",
                    "cache_hit": False,
                    "cache_tier": "MISS",
                    "envelope": env.to_dict(),
                    "data": raw_data,
                }, tel, decision
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff_base_seconds * (2**attempt))

        # Section 6: Public API failed -> check stale cache / local DB; NEVER fabricate values!
        lat = round((time.perf_counter() - t0) * 1000.0, 3)
        if allow_stale_on_error and lookup.stale_record is not None:
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode=ExecutionMode.PUBLIC_DATA_API.value,
                provider=lookup.stale_record.provider,
                model="none",
                external_api_invoked=True,
                llm_invoked=False,
                cache_hit=True,
                cache_tier="STALE_HISTORICAL_FALLBACK",
                input_tokens=0,
                output_tokens=0,
                actual_cost_usd=0.0,
                estimated_cost=0.0,
                cost_is_estimated=False,
                latency_ms=lat,
                execution_status="DEGRADED_STALE_DATA",
                validation_status="STALE_EXPLICITLY_FLAGGED",
                routing_reason=f"Upstream API '{provider_name}' failed ({type(last_exc).__name__}); using explicitly flagged historical record.",
            )
            self.telemetry.record(tel)
            return {
                "status": "DEGRADED_STALE_DATA",
                "stale": True,
                "fresh_data_unavailable": True,
                "upstream_error": str(last_exc),
                "cache_hit": True,
                "envelope": lookup.stale_record.to_dict(),
                "data": lookup.stale_record.payload,
            }, tel, decision

        tel = ExecutionStepTelemetry(
            agent_name=agent_name,
            task_id=task_id,
            task_category=task_category.value,
            execution_mode=ExecutionMode.PUBLIC_DATA_API.value,
            provider=provider_name,
            model="none",
            external_api_invoked=True,
            llm_invoked=False,
            cache_hit=False,
            cache_tier="MISS",
            input_tokens=0,
            output_tokens=0,
            actual_cost_usd=0.0,
            estimated_cost=0.0,
            cost_is_estimated=False,
            latency_ms=lat,
            execution_status="UNAVAILABLE",
            validation_status="NO_FABRICATED_DATA",
            routing_reason=f"Upstream API '{provider_name}' failed and no cached historical record exists.",
        )
        self.telemetry.record(tel)
        return {
            "status": "UNAVAILABLE",
            "stale": False,
            "fresh_data_unavailable": True,
            "upstream_error": str(last_exc),
            "cache_hit": False,
            "envelope": None,
            "data": None,
        }, tel, decision

    def execute_llm_or_hybrid_task(
        self,
        *,
        agent_name: str,
        task_id: str,
        task_category: TaskCategory,
        semantic_complexity: bool,
        llm_justification: str | None,
        local_builder_fn: Callable[[], dict[str, Any]],
        llm_gateway: Any | None,
        organization_id: str | None,
        providers_catalog: list[dict[str, Any]] | None,
        preferred_model: str,
        system_prompt: str,
        user_prompt: str,
        budget: RunBudgetTracker | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], ExecutionStepTelemetry, RoutingEvaluation]:
        """Controlled Layer C / HYBRID execution through central ExecutionRouter."""
        t0 = time.perf_counter()

        # Always run deterministic/statistical local builder first so local work never depends on LLM
        local_result = local_builder_fn()

        if budget and idempotency_key and idempotency_key in budget.completed_idempotency_keys:
            cached = budget.completed_idempotency_keys[idempotency_key]
            lat = round((time.perf_counter() - t0) * 1000.0, 3)
            decision = self.decide(
                task_category=task_category,
                semantic_complexity=semantic_complexity,
                llm_justification=llm_justification,
            )
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode=decision.selected_mode.value,
                provider="idempotency_cache",
                model="none",
                external_api_invoked=False,
                llm_invoked=False,
                cache_hit=True,
                cache_tier="IDEMPOTENCY_LEDGER",
                input_tokens=0,
                output_tokens=0,
                actual_cost_usd=0.0,
                estimated_cost=0.0,
                cost_is_estimated=False,
                latency_ms=lat,
                execution_status="SUCCEEDED",
                validation_status="VALIDATED",
                routing_reason="Idempotent replay returned cached result without duplicate LLM invocation.",
                llm_justification=llm_justification,
                idempotency_key=idempotency_key,
            )
            self.telemetry.record(tel)
            return cached, tel, decision

        has_local_model = False
        has_external_llm = False
        if (
            llm_gateway is not None
            and organization_id
            and providers_catalog
            and preferred_model != "deterministic-only"
        ):
            def _classify_provider(prov_cfg: dict[str, Any]) -> str:
                base_u = str(prov_cfg.get("base_url") or "").lower()
                slug_p = str(prov_cfg.get("slug") or "").lower()
                cat_p = str(prov_cfg.get("category") or "").lower()
                is_cloud = (
                    cat_p in {"cloud", "commercial_cloud"}
                    or slug_p.startswith(("openai", "anthropic", "openrouter"))
                )
                is_loc = (
                    not is_cloud
                    and (
                        cat_p in {"local", "local_ollama"}
                        or slug_p.startswith(("ollama", "lmstudio"))
                        or any(h in base_u for h in ("127.0.0.1", "localhost", "192.168.", "http://10."))
                    )
                )
                return "local" if is_loc else "external"

            ready_provs = [
                p
                for p in providers_catalog
                if p.get("enabled", True)
                and p.get("slug") != "apex-analytical"
                and llm_gateway.is_provider_ready(organization_id=organization_id, provider_cfg=p)
            ]
            matched_provs = [
                p
                for p in ready_provs
                if preferred_model in {m.get("id") for m in (p.get("models") or [])}
                or preferred_model.startswith(str(p.get("slug")) + "/")
            ]
            target_provs = matched_provs if matched_provs else ready_provs
            for p in target_provs:
                if _classify_provider(p) == "local":
                    has_local_model = True
                else:
                    has_external_llm = True

        decision = self.decide(
            task_category=task_category,
            local_data_available=True,
            data_freshness_ok=True,
            semantic_complexity=semantic_complexity,
            has_local_model=has_local_model,
            has_external_llm=has_external_llm,
            requested_llm=True,
            llm_justification=llm_justification,
        )

        can_call, budget_reason = (
            budget.can_invoke_llm(agent_name, decision.criterion_7_estimated_external_cost_usd)
            if budget is not None
            else (True, "no_budget_tracker")
        )

        if decision.selected_mode in {ExecutionMode.LOCAL_DETERMINISTIC, ExecutionMode.LOCAL_STATISTICAL} or not can_call:
            if not can_call:
                self.telemetry.prevented_unnecessary_calls += 1
            lat = round((time.perf_counter() - t0) * 1000.0, 3)
            out = {
                **local_result,
                "execution_mode": ExecutionMode.LOCAL_DETERMINISTIC.value,
                "llm_invoked": False,
                "budget_or_router_note": decision.reason if can_call else budget_reason,
            }
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode=ExecutionMode.LOCAL_DETERMINISTIC.value,
                provider="local_python",
                model="none",
                external_api_invoked=False,
                llm_invoked=False,
                cache_hit=False,
                cache_tier="NONE",
                input_tokens=0,
                output_tokens=0,
                actual_cost_usd=0.0,
                estimated_cost=0.0,
                cost_is_estimated=False,
                latency_ms=lat,
                execution_status="SUCCEEDED",
                validation_status="VALIDATED",
                routing_reason=decision.reason if can_call else budget_reason,
                llm_justification=decision.llm_justification,
                idempotency_key=idempotency_key,
            )
            self.telemetry.record(tel)
            if budget and idempotency_key:
                budget.completed_idempotency_keys[idempotency_key] = out
            return out, tel, decision

        # Redact secrets/confidential blocks whenever ANY external cloud provider is in candidate chain
        is_external_target = has_external_llm or decision.selected_mode == ExecutionMode.EXTERNAL_LLM
        safe_sys = redact_confidential_text(system_prompt, for_external_cloud=is_external_target)
        safe_usr = redact_confidential_text(user_prompt, for_external_cloud=is_external_target)

        contract = BUILTIN_AGENT_CONTRACTS.get(agent_name)
        max_tok = (
            min(contract.execution_budget.max_tokens, budget.max_tokens_per_agent if budget else 2600)
            if contract and contract.execution_budget.max_tokens > 0
            else 1600
        )

        llm_res = (
            llm_gateway.complete_sync(
                organization_id=organization_id,
                providers_catalog=providers_catalog,
                preferred_model=preferred_model,
                system_prompt=safe_sys,
                user_prompt=safe_usr,
                max_tokens=max_tok,
            )
            if llm_gateway is not None and organization_id and providers_catalog
            else None
        )

        lat = round((time.perf_counter() - t0) * 1000.0, 3)
        if llm_res is not None and llm_res.live_llm_invoked:
            if budget is not None:
                budget.llm_calls_used += 1
                budget.cost_used_usd += llm_res.cost_usd
                budget.tokens_by_agent[agent_name] = (
                    budget.tokens_by_agent.get(agent_name, 0) + llm_res.total_tokens
                )
            used_prov_cfg = next(
                (p for p in (providers_catalog or []) if p.get("slug") == llm_res.provider_slug),
                {},
            )
            used_is_cloud = (
                str(used_prov_cfg.get("category") or "").lower() in {"cloud", "commercial_cloud"}
                or str(llm_res.provider_slug).lower().startswith(("openai", "anthropic", "openrouter"))
            )
            actual_mode = (
                ExecutionMode.HYBRID.value
                if decision.selected_mode == ExecutionMode.HYBRID
                else (ExecutionMode.EXTERNAL_LLM.value if used_is_cloud else ExecutionMode.LOCAL_MODEL.value)
            )
            is_zero_cost_local = llm_res.cost_usd == 0.0 and not used_is_cloud
            out = {
                **local_result,
                "execution_mode": actual_mode,
                "live_llm_invoked": True,
                "llm_invoked": True,
                "provider_used": llm_res.provider_slug,
                "model_used": llm_res.model_identifier,
                "fallback_triggered": llm_res.fallback_triggered,
                "llm_reasoning": llm_res.content[:3000],
                "llm_justification": decision.llm_justification,
            }
            tel = ExecutionStepTelemetry(
                agent_name=agent_name,
                task_id=task_id,
                task_category=task_category.value,
                execution_mode=actual_mode,
                provider=llm_res.provider_slug,
                model=llm_res.model_identifier,
                external_api_invoked=False,
                llm_invoked=True,
                cache_hit=False,
                cache_tier="NONE",
                input_tokens=llm_res.prompt_tokens,
                output_tokens=llm_res.completion_tokens,
                actual_cost_usd=0.0,
                estimated_cost=llm_res.cost_usd,
                cost_is_estimated=not is_zero_cost_local,
                latency_ms=lat,
                execution_status="SUCCEEDED",
                validation_status="VALIDATED",
                routing_reason=decision.reason,
                llm_justification=decision.llm_justification,
                idempotency_key=idempotency_key,
            )
            self.telemetry.record(tel)
            if budget and idempotency_key:
                budget.completed_idempotency_keys[idempotency_key] = out
            return out, tel, decision

        # Section 6: LLM provider failed / unreachable -> preserve local work & checkpoint, mark LLM pending!
        status_flag = (
            "PARTIAL_LOCAL_COMPLETED_LLM_PENDING"
            if decision.selected_mode == ExecutionMode.HYBRID
            else "PENDING_LLM_UNAVAILABLE"
        )
        out = {
            **local_result,
            "execution_mode": decision.selected_mode.value,
            "live_llm_invoked": False,
            "llm_invoked": False,
            "llm_status": "PENDING_LLM_UNAVAILABLE",
            "local_subtasks_completed": True,
            "llm_error_note": getattr(llm_res, "error_note", None) or "LLM endpoint unreachable or timed out.",
        }
        tel = ExecutionStepTelemetry(
            agent_name=agent_name,
            task_id=task_id,
            task_category=task_category.value,
            execution_mode=decision.selected_mode.value,
            provider="llm_unavailable_fallback_local",
            model=preferred_model,
            external_api_invoked=False,
            llm_invoked=False,
            cache_hit=False,
            cache_tier="NONE",
            input_tokens=0,
            output_tokens=0,
            actual_cost_usd=0.0,
            estimated_cost=0.0,
            cost_is_estimated=False,
            latency_ms=lat,
            execution_status=status_flag,
            validation_status="LOCAL_VALIDATED_LLM_PENDING",
            routing_reason=f"{decision.reason} [LLM unreachable -> local results preserved]",
            llm_justification=decision.llm_justification,
            idempotency_key=idempotency_key,
        )
        self.telemetry.record(tel)
        return out, tel, decision


def fetch_btc_spot_and_ohlcv_http(
    *,
    market_api_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Dedicated Layer B HTTP adapter for BTC/USD spot & 90D daily OHLCV (zero LLM)."""
    headers = {
        "User-Agent": "APEX-ONE-LocalFirst/1.0",
        "Accept": "application/json",
    }
    url = market_api_url or os.environ.get(
        "APEX_BTC_MARKET_API_URL",
        "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=90",
    )
    with httpx.Client(
        timeout=timeout,
        headers=headers,
        verify=ssl.create_default_context() if url.startswith("https://") else False,
    ) as client:
        try:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
            bars: list[dict[str, float]] = []
            if isinstance(payload, list):
                for row in payload:
                    if isinstance(row, list) and len(row) >= 6:
                        bars.append(
                            {
                                "open": float(row[1]),
                                "high": float(row[2]),
                                "low": float(row[3]),
                                "close": float(row[4]),
                                "volume": float(row[5]),
                            }
                        )
                    elif isinstance(row, dict) and "close" in row:
                        c = float(row["close"])
                        bars.append(
                            {
                                "open": float(row.get("open", c)),
                                "high": float(row.get("high", c)),
                                "low": float(row.get("low", c)),
                                "close": c,
                                "volume": float(row.get("volume", 0.0)),
                            }
                        )
            elif isinstance(payload, dict) and "bars" in payload:
                bars = [
                    {
                        "open": float(b.get("open", b["close"])),
                        "high": float(b.get("high", b["close"])),
                        "low": float(b.get("low", b["close"])),
                        "close": float(b["close"]),
                        "volume": float(b.get("volume", 0.0)),
                    }
                    for b in payload["bars"]
                ]
            if not bars:
                raise ValueError("Empty OHLCV response.")
            return {
                "symbol": "BTC/USD",
                "provider_endpoint": url,
                "spot_price": bars[-1]["close"],
                "bars": bars,
            }
        except Exception:
            if market_api_url or "APEX_BTC_MARKET_API_URL" in os.environ:
                raise
            cg_url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90&interval=daily"
            r2 = client.get(cg_url)
            r2.raise_for_status()
            prices = r2.json().get("prices") or []
            bars = [
                {"open": float(p[1]), "high": float(p[1]), "low": float(p[1]), "close": float(p[1]), "volume": 0.0}
                for p in prices
            ]
            if not bars:
                raise ValueError("Empty CoinGecko response.") from None
            return {
                "symbol": "BTC/USD",
                "provider_endpoint": cg_url,
                "spot_price": bars[-1]["close"],
                "bars": bars,
            }


__all__ = [
    "AgentExecutionContract",
    "BUILTIN_AGENT_CONTRACTS",
    "CacheLookupOutcome",
    "CacheRecordEnvelope",
    "ExecutionBudgetSpec",
    "ExecutionMode",
    "ExecutionRouter",
    "ExecutionStepTelemetry",
    "HAS_NUMPY",
    "LLM_FORBIDDEN_TASK_CATEGORIES",
    "MultiTierDataCache",
    "RoutingEvaluation",
    "RunBudgetTracker",
    "TaskCategory",
    "TelemetryLedger",
    "ValidationError",
    "analyze_ohlcv_and_volatility",
    "analyze_tabular_csv_statistics",
    "compute_sha256_integrity",
    "detect_rule_based_contradictions",
    "extract_ohlcv_from_sources",
    "fetch_btc_spot_and_ohlcv_http",
    "redact_confidential_text",
    "run_monte_carlo_simulation",
    "score_and_deduplicate_sources",
    "validate_and_parse_structured_data",
]
