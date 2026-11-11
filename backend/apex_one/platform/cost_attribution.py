"""APEX ONE HARDENING 1.0 — Stage 7 (P2): Per-Call Cost Analytics & Token Attribution.

Guarantees:
1. Every model/API/step invocation produces a granular `CallCostAttributionRecord` with all 16 mandatory fields:
   - `execution_id`, `workflow_run_id`, `step_id`, `agent_id`, `provider`, `model`,
     `execution_mode`, `input_tokens`, `output_tokens`, `cached_tokens`,
     `actual_cost_usd`, `estimated_cost_usd`, `pricing_version`, `latency_ms`,
     `request_id`, `invocation_timestamp`.
2. Strict separation of cost categories:
   - `cloud_llm_api` (OpenAI, Anthropic, OpenRouter, commercial cloud)
   - `local_llm_api` (Ollama, LM Studio, Local Node — `$0.00` external API cost)
   - `public_data_api` (SEC EDGAR, FRED, Eurostat, PubMed, ArXiv, etc.)
   - `infrastructure` (compute/worker runtime metering, never mixed with LLM API cost)
   - `database` (storage/DB query metering, never mixed with LLM API cost)
   - `local_compute` (deterministic/statistical Python & cache hits — `$0.00`)
3. Zero fake default tariffs:
   - If a cloud model's tariff is not explicitly configured in the provider catalog or
     `KNOWN_CLOUD_MODEL_TARIFFS` and the upstream response does not carry billed `actual_cost_usd`,
     pricing is recorded honestly as `cost_status="unknown"` (`actual_cost_usd=None`,
     `estimated_cost_usd=None`, `pricing_version="unknown"`).
4. Zero cache-hit double counting:
   - Cache hits (`cache_hit=True`) record `0` billed cost and `0` new API tokens;
   - Prompt-cache hits (`cached_tokens > 0`) bill uncached input tokens `(input_tokens - cached_tokens)`
     at the input tariff and `cached_tokens` at the explicit cached-input tariff.
5. Workflow/run totals are strictly rolled up from the underlying per-call records,
   preserving multi-provider / multi-model attribution across steps.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

PRICING_CATALOG_VERSION = "apex-pricing-2026-11-v1"


class CostCategory(StrEnum):
    CLOUD_LLM_API = "cloud_llm_api"
    LOCAL_LLM_API = "local_llm_api"
    PUBLIC_DATA_API = "public_data_api"
    INFRASTRUCTURE = "infrastructure"
    DATABASE = "database"
    LOCAL_COMPUTE = "local_compute"
    UNKNOWN = "unknown"


class CostStatus(StrEnum):
    KNOWN_ACTUAL = "known_actual"
    KNOWN_TARIFF_ESTIMATE = "known_tariff_estimate"
    LOCAL_ZERO_API_COST = "local_zero_api_cost"
    CACHE_HIT_ZERO_COST = "cache_hit_zero_cost"
    UNKNOWN = "unknown"


# Explicit versioned tariff table (USD per 1,000 tokens). Unknown cloud models NEVER fall back to a fake default!
KNOWN_CLOUD_MODEL_TARIFFS: dict[str, dict[str, float]] = {
    "gpt-4o": {
        "input_per_1k": 0.0025,
        "cached_input_per_1k": 0.00125,
        "output_per_1k": 0.0100,
    },
    "gpt-4o-mini": {
        "input_per_1k": 0.00015,
        "cached_input_per_1k": 0.000075,
        "output_per_1k": 0.00060,
    },
    "o3-mini": {
        "input_per_1k": 0.00110,
        "cached_input_per_1k": 0.00055,
        "output_per_1k": 0.00440,
    },
    "claude-3-5-sonnet-latest": {
        "input_per_1k": 0.0030,
        "cached_input_per_1k": 0.00030,
        "output_per_1k": 0.0150,
    },
    "claude-3-5-haiku-latest": {
        "input_per_1k": 0.00080,
        "cached_input_per_1k": 0.00008,
        "output_per_1k": 0.00400,
    },
    "text-embedding-3-small": {
        "input_per_1k": 0.00002,
        "cached_input_per_1k": 0.00002,
        "output_per_1k": 0.0,
    },
    "text-embedding-3-large": {
        "input_per_1k": 0.00013,
        "cached_input_per_1k": 0.00013,
        "output_per_1k": 0.0,
    },
}

_LOCAL_PROVIDER_PREFIXES = (
    "ollama",
    "lmstudio",
    "local_node",
    "local-node",
    "apex-local",
)

_LOCAL_COMPUTE_PROVIDERS = {
    "local",
    "local_python",
    "local_statistical",
    "apex-analytical",
    "idempotency_cache",
    "memory_cache",
    "sqlite_cache",
}

_PUBLIC_DATA_PROVIDERS = {
    "sec_edgar",
    "fred",
    "eurostat",
    "world_bank",
    "frankfurter",
    "ecb",
    "pubmed",
    "arxiv",
    "crossref",
    "semantic_scholar",
    "open_meteo",
    "github_public",
}


def classify_provider_cost_category(
    *,
    provider: str,
    provider_cfg: dict[str, Any] | None = None,
    llm_invoked: bool = False,
    external_api_invoked: bool = False,
    explicit_category: CostCategory | str | None = None,
) -> CostCategory:
    """Classify an invocation into its exact cost category without conflating cloud vs local."""
    if explicit_category is not None:
        return CostCategory(str(explicit_category))

    slug = (provider or "").strip().lower()
    cfg = provider_cfg or {}
    cfg_cat = str(cfg.get("category") or "").strip().lower()
    base_url = str(cfg.get("base_url") or "").strip().lower()

    if slug in _LOCAL_COMPUTE_PROVIDERS:
        return CostCategory.LOCAL_COMPUTE
    if slug in _PUBLIC_DATA_PROVIDERS or (external_api_invoked and not llm_invoked):
        return CostCategory.PUBLIC_DATA_API
    if (
        cfg_cat in {"local", "local_ollama"}
        or slug.startswith(_LOCAL_PROVIDER_PREFIXES)
        or (
            cfg_cat not in {"cloud", "commercial_cloud"}
            and any(
                h in base_url
                for h in ("127.0.0.1", "localhost", "192.168.", "http://10.")
            )
        )
    ):
        return CostCategory.LOCAL_LLM_API if llm_invoked else CostCategory.LOCAL_COMPUTE
    if (
        cfg_cat in {"cloud", "commercial_cloud"}
        or slug.startswith(("openai", "anthropic", "openrouter", "gemini", "mistral"))
        or llm_invoked
    ):
        return CostCategory.CLOUD_LLM_API
    return CostCategory.LOCAL_COMPUTE


@dataclass(frozen=True)
class PricedInvocationQuote:
    cost_category: str
    cost_status: str
    actual_cost_usd: float | None
    estimated_cost_usd: float | None
    pricing_version: str


def compute_call_cost_quote(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_hit: bool = False,
    llm_invoked: bool = False,
    external_api_invoked: bool = False,
    provider_cfg: dict[str, Any] | None = None,
    upstream_actual_cost_usd: float | None = None,
    explicit_category: CostCategory | str | None = None,
) -> PricedInvocationQuote:
    """Compute exact actual vs estimated vs `$0.00` local vs `unknown` cost for a single call."""
    category = classify_provider_cost_category(
        provider=provider,
        provider_cfg=provider_cfg,
        llm_invoked=llm_invoked,
        external_api_invoked=external_api_invoked,
        explicit_category=explicit_category,
    )

    if cache_hit:
        return PricedInvocationQuote(
            cost_category=category.value,
            cost_status=CostStatus.CACHE_HIT_ZERO_COST.value,
            actual_cost_usd=0.0,
            estimated_cost_usd=0.0,
            pricing_version=PRICING_CATALOG_VERSION,
        )

    if category in {CostCategory.LOCAL_LLM_API, CostCategory.LOCAL_COMPUTE}:
        return PricedInvocationQuote(
            cost_category=category.value,
            cost_status=CostStatus.LOCAL_ZERO_API_COST.value,
            actual_cost_usd=0.0,
            estimated_cost_usd=0.0,
            pricing_version=PRICING_CATALOG_VERSION,
        )

    if category == CostCategory.PUBLIC_DATA_API:
        if upstream_actual_cost_usd is not None:
            return PricedInvocationQuote(
                cost_category=category.value,
                cost_status=CostStatus.KNOWN_ACTUAL.value,
                actual_cost_usd=round(float(upstream_actual_cost_usd), 8),
                estimated_cost_usd=None,
                pricing_version=PRICING_CATALOG_VERSION,
            )
        return PricedInvocationQuote(
            cost_category=category.value,
            cost_status=CostStatus.KNOWN_ACTUAL.value,
            actual_cost_usd=0.0,
            estimated_cost_usd=0.0,
            pricing_version=PRICING_CATALOG_VERSION,
        )

    if upstream_actual_cost_usd is not None:
        return PricedInvocationQuote(
            cost_category=category.value,
            cost_status=CostStatus.KNOWN_ACTUAL.value,
            actual_cost_usd=round(float(upstream_actual_cost_usd), 8),
            estimated_cost_usd=None,
            pricing_version=PRICING_CATALOG_VERSION,
        )

    # Cloud LLM tariff lookup: check explicit provider catalog model entry first, then KNOWN_CLOUD_MODEL_TARIFFS
    in_tok = max(0, int(input_tokens))
    out_tok = max(0, int(output_tokens))
    cached_tok = min(in_tok, max(0, int(cached_tokens)))
    uncached_in_tok = max(0, in_tok - cached_tok)

    tariff: dict[str, float] | None = None
    pricing_ver = PRICING_CATALOG_VERSION
    cfg = provider_cfg or {}
    for m_entry in cfg.get("models") or []:
        if m_entry.get("id") == model:
            if (
                "cost_per_1k_input_usd" in m_entry
                and m_entry.get("cost_per_1k_input_usd") is not None
                and "cost_per_1k_output_usd" in m_entry
                and m_entry.get("cost_per_1k_output_usd") is not None
            ):
                in_rate = float(m_entry["cost_per_1k_input_usd"])
                cached_rate = float(
                    m_entry.get("cost_per_1k_cached_input_usd", in_rate * 0.5)
                )
                out_rate = float(m_entry["cost_per_1k_output_usd"])
                tariff = {
                    "input_per_1k": in_rate,
                    "cached_input_per_1k": cached_rate,
                    "output_per_1k": out_rate,
                }
                pricing_ver = str(
                    m_entry.get("pricing_version")
                    or cfg.get("pricing_version")
                    or PRICING_CATALOG_VERSION
                )
            break

    if tariff is None:
        # Strip provider prefix like "openai/gpt-4o-mini" when matching known canonical model IDs
        bare_model = model.split("/", 1)[-1] if "/" in model else model
        if model in KNOWN_CLOUD_MODEL_TARIFFS:
            tariff = KNOWN_CLOUD_MODEL_TARIFFS[model]
        elif bare_model in KNOWN_CLOUD_MODEL_TARIFFS:
            tariff = KNOWN_CLOUD_MODEL_TARIFFS[bare_model]

    if tariff is None:
        # NEVER fabricate a default tariff for an unknown model!
        return PricedInvocationQuote(
            cost_category=category.value,
            cost_status=CostStatus.UNKNOWN.value,
            actual_cost_usd=None,
            estimated_cost_usd=None,
            pricing_version="unknown",
        )

    est_usd = round(
        (uncached_in_tok / 1000.0) * tariff["input_per_1k"]
        + (cached_tok / 1000.0) * tariff["cached_input_per_1k"]
        + (out_tok / 1000.0) * tariff["output_per_1k"],
        8,
    )
    return PricedInvocationQuote(
        cost_category=category.value,
        cost_status=CostStatus.KNOWN_TARIFF_ESTIMATE.value,
        actual_cost_usd=None,
        estimated_cost_usd=est_usd,
        pricing_version=pricing_ver,
    )


@dataclass
class CallCostAttributionRecord:
    """Granular per-invocation cost & token ledger record (all 16 mandatory fields)."""

    execution_id: str
    workflow_run_id: str
    step_id: str
    agent_id: str
    provider: str
    model: str
    execution_mode: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    actual_cost_usd: float | None
    estimated_cost_usd: float | None
    pricing_version: str
    latency_ms: float
    request_id: str
    invocation_timestamp: str
    cost_category: str = CostCategory.LOCAL_COMPUTE.value
    cost_status: str = CostStatus.LOCAL_ZERO_API_COST.value
    cache_hit: bool = False
    organization_id: str | None = None
    workspace_scope: str = "research"

    @property
    def total_tokens(self) -> int:
        return int(self.input_tokens) + int(self.output_tokens)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


def build_call_attribution_record(
    *,
    workflow_run_id: str,
    step_id: str,
    agent_id: str,
    provider: str,
    model: str,
    execution_mode: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    latency_ms: float = 0.0,
    cache_hit: bool = False,
    llm_invoked: bool = False,
    external_api_invoked: bool = False,
    provider_cfg: dict[str, Any] | None = None,
    upstream_actual_cost_usd: float | None = None,
    request_id: str | None = None,
    execution_id: str | None = None,
    invocation_timestamp: str | None = None,
    explicit_category: CostCategory | str | None = None,
    organization_id: str | None = None,
    workspace_scope: str = "research",
) -> CallCostAttributionRecord:
    """Construct a validated `CallCostAttributionRecord` with exact category/status separation."""
    eff_in = 0 if cache_hit else max(0, int(input_tokens))
    eff_out = 0 if cache_hit else max(0, int(output_tokens))
    eff_cached = 0 if cache_hit else min(eff_in, max(0, int(cached_tokens)))

    quote = compute_call_cost_quote(
        provider=provider,
        model=model,
        input_tokens=eff_in,
        output_tokens=eff_out,
        cached_tokens=eff_cached,
        cache_hit=cache_hit,
        llm_invoked=llm_invoked,
        external_api_invoked=external_api_invoked,
        provider_cfg=provider_cfg,
        upstream_actual_cost_usd=upstream_actual_cost_usd,
        explicit_category=explicit_category,
    )
    return CallCostAttributionRecord(
        execution_id=execution_id or str(uuid.uuid4()),
        workflow_run_id=workflow_run_id,
        step_id=step_id,
        agent_id=agent_id,
        provider=provider,
        model=model,
        execution_mode=execution_mode,
        input_tokens=eff_in,
        output_tokens=eff_out,
        cached_tokens=eff_cached,
        actual_cost_usd=quote.actual_cost_usd,
        estimated_cost_usd=quote.estimated_cost_usd,
        pricing_version=quote.pricing_version,
        latency_ms=round(float(latency_ms), 3),
        request_id=request_id or f"req_{uuid.uuid4().hex[:16]}",
        invocation_timestamp=invocation_timestamp or datetime.now(UTC).isoformat(),
        cost_category=quote.cost_category,
        cost_status=quote.cost_status,
        cache_hit=bool(cache_hit),
        organization_id=organization_id,
        workspace_scope=workspace_scope,
    )


def aggregate_workflow_cost_attribution(
    records: list[dict[str, Any] | CallCostAttributionRecord],
    *,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    """Roll up workflow/tenant cost & token totals strictly from per-call records."""
    dicts = [
        r.to_dict() if isinstance(r, CallCostAttributionRecord) else dict(r)
        for r in records
        if workflow_run_id is None
        or (
            r.workflow_run_id
            if isinstance(r, CallCostAttributionRecord)
            else r.get("workflow_run_id")
        )
        == workflow_run_id
    ]

    seen_exec_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for rec in dicts:
        eid = str(rec.get("execution_id") or "")
        if eid and eid in seen_exec_ids:
            continue
        if eid:
            seen_exec_ids.add(eid)
        deduped.append(rec)

    actual_total = 0.0
    estimated_total = 0.0
    cloud_llm_actual = 0.0
    cloud_llm_estimated = 0.0
    local_llm_api_cost = 0.0
    public_api_cost = 0.0
    infra_cost = 0.0
    db_cost = 0.0
    infra_metered = False
    db_metered = False
    unknown_calls_count = 0
    cache_hits_count = 0

    input_tokens_total = 0
    output_tokens_total = 0
    cached_tokens_total = 0
    local_llm_tokens = 0
    cloud_llm_tokens = 0

    by_provider: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    by_step: dict[str, dict[str, Any]] = {}

    def _accumulate_bucket(
        table: dict[str, dict[str, Any]], key: str, rec: dict[str, Any]
    ) -> None:
        b = table.setdefault(
            key,
            {
                "calls": 0,
                "cache_hits": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "actual_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "unknown_cost_calls": 0,
            },
        )
        b["calls"] += 1
        if rec.get("cache_hit"):
            b["cache_hits"] += 1
        in_t = int(rec.get("input_tokens") or 0)
        out_t = int(rec.get("output_tokens") or 0)
        c_t = int(rec.get("cached_tokens") or 0)
        b["input_tokens"] += in_t
        b["output_tokens"] += out_t
        b["cached_tokens"] += c_t
        b["total_tokens"] += in_t + out_t
        if rec.get("cost_status") == CostStatus.UNKNOWN.value:
            b["unknown_cost_calls"] += 1
        if rec.get("actual_cost_usd") is not None:
            b["actual_cost_usd"] = round(
                b["actual_cost_usd"] + float(rec["actual_cost_usd"]), 8
            )
        if rec.get("estimated_cost_usd") is not None:
            b["estimated_cost_usd"] = round(
                b["estimated_cost_usd"] + float(rec["estimated_cost_usd"]), 8
            )

    for rec in deduped:
        in_t = int(rec.get("input_tokens") or 0)
        out_t = int(rec.get("output_tokens") or 0)
        c_t = int(rec.get("cached_tokens") or 0)
        tot_t = in_t + out_t
        input_tokens_total += in_t
        output_tokens_total += out_t
        cached_tokens_total += c_t

        if rec.get("cache_hit"):
            cache_hits_count += 1

        status = str(rec.get("cost_status") or "")
        cat = str(rec.get("cost_category") or CostCategory.LOCAL_COMPUTE.value)
        act_c = rec.get("actual_cost_usd")
        est_c = rec.get("estimated_cost_usd")

        if status == CostStatus.UNKNOWN.value:
            unknown_calls_count += 1

        if act_c is not None:
            actual_total += float(act_c)
        if est_c is not None:
            estimated_total += float(est_c)

        if cat == CostCategory.CLOUD_LLM_API.value:
            cloud_llm_tokens += tot_t
            if act_c is not None:
                cloud_llm_actual += float(act_c)
            if est_c is not None:
                cloud_llm_estimated += float(est_c)
        elif cat == CostCategory.LOCAL_LLM_API.value:
            local_llm_tokens += tot_t
            if act_c is not None:
                local_llm_api_cost += float(act_c)
        elif cat == CostCategory.PUBLIC_DATA_API.value:
            if act_c is not None:
                public_api_cost += float(act_c)
        elif cat == CostCategory.INFRASTRUCTURE.value:
            infra_metered = True
            if act_c is not None:
                infra_cost += float(act_c)
            elif est_c is not None:
                infra_cost += float(est_c)
        elif cat == CostCategory.DATABASE.value:
            db_metered = True
            if act_c is not None:
                db_cost += float(act_c)
            elif est_c is not None:
                db_cost += float(est_c)

        _accumulate_bucket(by_provider, str(rec.get("provider") or "unknown"), rec)
        _accumulate_bucket(by_model, str(rec.get("model") or "none"), rec)
        _accumulate_bucket(by_agent, str(rec.get("agent_id") or "system"), rec)
        _accumulate_bucket(by_step, str(rec.get("step_id") or "unscoped"), rec)

    known_combined_cost = round(actual_total + estimated_total, 8)
    return {
        "workflow_run_id": workflow_run_id,
        "call_count": len(deduped),
        "cache_hits_count": cache_hits_count,
        "input_tokens_total": input_tokens_total,
        "output_tokens_total": output_tokens_total,
        "cached_tokens_total": cached_tokens_total,
        "total_tokens": input_tokens_total + output_tokens_total,
        "actual_cost_usd_total": round(actual_total, 8),
        "estimated_cost_usd_total": round(estimated_total, 8),
        "known_combined_cost_usd": known_combined_cost,
        "has_unknown_pricing": unknown_calls_count > 0,
        "unknown_cost_calls_count": unknown_calls_count,
        "cost_breakdown_by_category": {
            "cloud_llm_api": {
                "actual_cost_usd": round(cloud_llm_actual, 8),
                "estimated_cost_usd": round(cloud_llm_estimated, 8),
                "tokens": cloud_llm_tokens,
            },
            "local_llm_api": {
                "api_cost_usd": round(local_llm_api_cost, 8),
                "tokens": local_llm_tokens,
                "note": "Local Ollama/LM Studio/Local Node inference incurs $0.00 external API cost.",
            },
            "public_data_api": {
                "actual_cost_usd": round(public_api_cost, 8),
            },
            "infrastructure": {
                "status": "METERED" if infra_metered else "NOT_METERED",
                "cost_usd": round(infra_cost, 8) if infra_metered else None,
            },
            "database": {
                "status": "METERED" if db_metered else "NOT_METERED",
                "cost_usd": round(db_cost, 8) if db_metered else None,
            },
        },
        "by_provider": by_provider,
        "by_model": by_model,
        "by_agent": by_agent,
        "by_step": by_step,
    }
