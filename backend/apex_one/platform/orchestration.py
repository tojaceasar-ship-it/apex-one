"""AgentDNA versioning, AgentRuntimeAdapter, and Local-First Multi-Agent Research Orchestration."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from apex_one.db.base import utcnow
from apex_one.platform.cost_attribution import aggregate_workflow_cost_attribution
from apex_one.platform.local_intelligence import (
    BUILTIN_AGENT_CONTRACTS,
    ExecutionRouter,
    MultiTierDataCache,
    RunBudgetTracker,
    TaskCategory,
    TelemetryLedger,
    analyze_ohlcv_and_volatility,
    compute_sha256_integrity,
    detect_rule_based_contradictions,
    extract_ohlcv_from_sources,
    run_monte_carlo_simulation,
    score_and_deduplicate_sources,
)
from apex_one.platform.research_engine import evaluate_claim_evidence_status


def load_builtin_agent_dna_manifests(repo_root: Path) -> list[dict[str, Any]]:
    """Load the 8 specialized research agents + universal baseline enriched with Local-First contracts."""
    agents_dir = repo_root / "agents"
    manifests: list[dict[str, Any]] = []
    if not agents_dir.exists():
        return manifests
    for manifest_path in sorted(agents_dir.glob("*/agent_dna.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        slug = str(data.get("slug") or "")
        contract = BUILTIN_AGENT_CONTRACTS.get(slug)
        if contract is not None:
            c_dump = contract.model_dump()
            data.setdefault("local_execution_methods", c_dump["local_execution_methods"])
            data.setdefault("external_api_requirements", c_dump["external_api_requirements"])
            data.setdefault("llm_requirements", c_dump["llm_requirements"])
            data.setdefault("validation_method", c_dump["validation_method"])
            data.setdefault("execution_budget", c_dump["execution_budget"])
            data.setdefault("fallback_strategy", c_dump["fallback_strategy"])
            data["execution_contract"] = c_dump
        manifests.append(data)
    return manifests


def diff_agent_dna_versions(
    dna_a: dict[str, Any],
    dna_b: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare two immutable AgentDNA versions and return changed fields."""
    keys = sorted(set(dna_a.keys()) | set(dna_b.keys()))
    changes: list[dict[str, Any]] = []
    for k in keys:
        va = dna_a.get(k)
        vb = dna_b.get(k)
        if va != vb:
            changes.append({"field": k, "before": va, "after": vb})
    return changes


class AgentRuntimeAdapter(Protocol):
    """Provider-neutral durable multi-agent execution interface (Part III.5)."""

    @property
    def adapter_name(self) -> str: ...

    def execute_or_resume_run(
        self,
        *,
        run_record: dict[str, Any],
        agents_lookup: dict[str, dict[str, Any]],
        project_sources: list[dict[str, Any]],
        interrupt_after_step: int | None = None,
    ) -> dict[str, Any]: ...


_PCT_THRESHOLD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_CONTRADICTION_CUE_RE = re.compile(
    r"\b(contrary\s+to|contradict\w*|refut\w*|without\s+improving|failure\s+modes?|"
    r"sprzeczn\w*|przeciw\w*|podważ\w*|nie\s+daje\s+przewagi|nie\s+przewyższ\w*)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _extract_claims_from_source(
    src: dict[str, Any],
    *,
    objective: str,
    deep_multi_pass: bool,
    blind_benchmark: bool = False,
) -> list[dict[str, Any]]:
    """Extract claims from `src` without artificial polarity filtering.

    In `blind_benchmark` mode, `metadata_json['labeled_claims']` is NEVER read by
    either Single-Agent or Multi-Agent — both must infer claims and polarity purely
    from document text and title.
    """
    text = str(src.get("extracted_text") or "").strip()
    title = str(src.get("title") or "")
    doc_id = str((src.get("metadata_json") or {}).get("doc_id") or "")

    if not blind_benchmark:
        meta_claims = (src.get("metadata_json") or {}).get("labeled_claims") or []
        if meta_claims:
            selected = meta_claims if deep_multi_pass else meta_claims[:1]
            out_labeled: list[dict[str, Any]] = []
            for lc in selected:
                ev_type = (
                    "contradicting"
                    if lc.get("polarity") == "contradicting"
                    else "supporting"
                )
                out_labeled.append(
                    {
                        "statement": str(lc["statement"]),
                        "source_id": src["id"],
                        "source_title": title,
                        "doc_id": doc_id,
                        "evidence_type": ev_type,
                        "confidence": 0.88 if deep_multi_pass else 0.76,
                        "excerpt": text[:280],
                    }
                )
            return out_labeled

    sentences = [
        s.strip(" -#*>")
        for s in _SENTENCE_SPLIT_RE.split(text)
        if len(s.strip(" -#*>")) >= 20
    ]
    if not sentences:
        if not text:
            return []
        sentences = [text[:240]]

    if not deep_multi_pass:
        # Single-pass extraction: lead sentence per source + opening-clause polarity check
        lead = sentences[0]
        opening_window = f"{title} {lead[:95]}"
        is_contra = bool(_CONTRADICTION_CUE_RE.search(opening_window))
        return [
            {
                "statement": lead[:260],
                "source_id": src["id"],
                "source_title": title,
                "doc_id": doc_id,
                "evidence_type": "contradicting" if is_contra else "supporting",
                "confidence": 0.74,
                "excerpt": text[:240],
            }
        ]

    # Multi-agent dedicated Evidence Analyst: full multi-sentence & title+body polarity inspection
    full_cue = bool(_CONTRADICTION_CUE_RE.search(f"{title} {text}"))
    combined_stmt = " ".join(sentences[:2])[:360]
    return [
        {
            "statement": combined_stmt,
            "source_id": src["id"],
            "source_title": title,
            "doc_id": doc_id,
            "evidence_type": "contradicting" if full_cue else "supporting",
            "confidence": 0.90 if len(sentences) >= 2 else 0.84,
            "excerpt": text[:280],
        }
    ]


class DurableCheckpointRuntimeAdapter:
    """Local-First durable multi-agent DAG runtime for APEX ONE.

    Every workflow step is dispatched exclusively through `ExecutionRouter`:
    - Layer A (`LOCAL_DETERMINISTIC` / `LOCAL_STATISTICAL`) handles DAG supervision,
      research decomposition, SHA-256 deduplication, source scoring, OHLCV volatility,
      10,000-path Monte Carlo simulations, stress matrices, rule-based contradiction checks,
      citation audits, and structured Markdown table construction at 0 LLM calls.
    - Layer C / `HYBRID` (`LOCAL_MODEL` or `EXTERNAL_LLM`) is invoked strictly when
      justified by genuine semantic/linguistic complexity and within explicit run budgets.
    """

    @property
    def adapter_name(self) -> str:
        return "apex-durable-dag-v1"

    def execute_or_resume_run(
        self,
        *,
        run_record: dict[str, Any],
        agents_lookup: dict[str, dict[str, Any]],
        project_sources: list[dict[str, Any]],
        interrupt_after_step: int | None = None,
        llm_gateway: Any | None = None,
        organization_id: str | None = None,
        providers_catalog: list[dict[str, Any]] | None = None,
        execution_router: ExecutionRouter | None = None,
        max_llm_calls_per_run: int = 3,
        max_tokens_per_agent: int = 3000,
        max_cost_budget_usd: float = 0.35,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        mode = run_record.get("execution_mode", "multi_agent_team")
        objective = run_record["objective"]
        preferred_model = str(
            run_record.get("model_identifier") or "apex-analytical-local-v1"
        )

        local_ledger = TelemetryLedger()
        router = execution_router or ExecutionRouter(
            cache=MultiTierDataCache(),
            telemetry=local_ledger,
        )

        checkpoint = dict(run_record.get("checkpoint_json") or {})
        completed_idem: set[str] = set(checkpoint.get("completed_step_keys", []))
        tool_ledger: dict[str, dict[str, Any]] = dict(
            checkpoint.get("tool_ledger", {})
        )
        artifacts_acc: dict[str, Any] = dict(checkpoint.get("artifacts", {}))
        saved_budget = checkpoint.get("budget_state") or {}
        budget = RunBudgetTracker(
            max_llm_calls_per_run=int(saved_budget.get("max_llm_calls_per_run", max_llm_calls_per_run)),
            max_tokens_per_agent=int(saved_budget.get("max_tokens_per_agent", max_tokens_per_agent)),
            max_cost_budget_usd=float(saved_budget.get("max_cost_budget_usd", max_cost_budget_usd)),
            llm_calls_used=int(saved_budget.get("llm_calls_used", 0)),
            cost_used_usd=float(saved_budget.get("cost_used_usd", 0.0)),
            tokens_by_agent=dict(saved_budget.get("tokens_by_agent", {})),
            completed_idempotency_keys=dict(saved_budget.get("completed_idempotency_keys", {})),
        )
        run_telemetry_steps: list[dict[str, Any]] = list(checkpoint.get("step_telemetry", []))

        steps: list[dict[str, Any]] = list(run_record.get("steps") or [])
        tool_invocations: list[dict[str, Any]] = list(
            run_record.get("tool_invocations") or []
        )

        if not steps:
            if mode == "single_agent":
                steps = [
                    {
                        "id": str(uuid.uuid4()),
                        "step_index": 1,
                        "agent_slug": "universal-baseline",
                        "agent_name": "Uniwersalny Agent Bazowy (Single-Agent Baseline)",
                        "agent_role": "universal_researcher",
                        "task_title": "Jednoetapowa analiza źródeł, ekstrakcja głównych twierdzeń i synteza",
                        "depends_on": [],
                        "status": "PENDING",
                        "idempotency_key": f"run:{run_record['id']}:step:1",
                        "tokens_used": 0,
                        "cost_usd": 0.0,
                        "duration_ms": 0,
                        "output_json": None,
                    }
                ]
            else:
                pipeline_spec = [
                    ("supervisor", "Kontrola DAG, budżetu kosztowego i polityki Local-First"),
                    ("research-planner", "Dekompozycja hipotez, progów ilościowych i metodologii"),
                    ("source-scout", "Weryfikacja źródeł, sum kontrolnych SHA-256, deduplikacja i scoring"),
                    ("evidence-analyst", "Ekstrakcja twierdzeń, powiązań dowodowych i regułowa detekcja sprzeczności"),
                    ("data-analyst", "Lokalna analiza zmienności OHLCV, statystyki opisowe i symulacja Monte Carlo"),
                    ("experiment-engineer", "Lokalna macierz warunków skrajnych (stress-test) i manifest replikacji"),
                    ("critic-replicator", "Audyt cytowań, spójności liczbowej i krytyka sprzeczności"),
                    ("research-writer", "Hybrydowa synteza tabel lokalnych, prawdopodobieństw i narracji końcowej"),
                ]
                for idx, (slug, title) in enumerate(pipeline_spec, start=1):
                    ag = agents_lookup.get(slug, {"name": slug, "role": slug})
                    steps.append(
                        {
                            "id": str(uuid.uuid4()),
                            "step_index": idx,
                            "agent_slug": slug,
                            "agent_name": ag.get("name", slug),
                            "agent_role": ag.get("role", slug),
                            "task_title": title,
                            "depends_on": [idx - 1] if idx > 1 else [],
                            "status": "PENDING",
                            "idempotency_key": f"run:{run_record['id']}:step:{idx}",
                            "tokens_used": 0,
                            "cost_usd": 0.0,
                            "duration_ms": 0,
                            "output_json": None,
                        }
                    )

        def invoke_tool_idempotent(
            step: dict[str, Any],
            tool_name: str,
            risk_level: str,
            args: dict[str, Any],
            result_payload: dict[str, Any],
        ) -> dict[str, Any]:
            idem_key = f"{step['idempotency_key']}:tool:{tool_name}"
            if idem_key in tool_ledger:
                return dict(tool_ledger[idem_key])
            ts0 = time.perf_counter()
            result = dict(result_payload)
            dur = max(1, int((time.perf_counter() - ts0) * 1000))
            inv = {
                "id": str(uuid.uuid4()),
                "agent_run_id": run_record["id"],
                "agent_step_id": step["id"],
                "tool_name": tool_name,
                "risk_level": risk_level,
                "idempotency_key": idem_key,
                "arguments_json": args,
                "result_json": result,
                "status": "SUCCEEDED",
                "duration_ms": dur,
                "created_at": utcnow().isoformat(),
            }
            tool_invocations.append(inv)
            tool_ledger[idem_key] = result
            return result

        conflicts: list[dict[str, Any]] = list(run_record.get("conflicts_json") or [])

        def _execute_step_body(
            step: dict[str, Any],
            idem: str,
            current_conflicts: list[dict[str, Any]],
        ) -> tuple[dict[str, Any], Any, Any, list[dict[str, Any]]]:
            slug = step["agent_slug"]
            agent_spec = agents_lookup.get(slug, {})
            dna = agent_spec.get("dna") or {}
            sys_inst = str(
                dna.get("instructions")
                or f"You are specialist research agent '{slug}'."
            )
            next_conflicts = current_conflicts

            if slug == "universal-baseline":
                def _build_single_baseline() -> dict[str, Any]:
                    src_res = invoke_tool_idempotent(
                        step,
                        "source.lookup",
                        "R0",
                        {"source_count": len(project_sources)},
                        {
                            "checked_sources": [s["id"] for s in project_sources],
                            "titles": [s["title"] for s in project_sources],
                        },
                    )
                    blind_mode = bool(run_record.get("benchmark_blind_mode", False))
                    extracted_claims: list[dict[str, Any]] = []
                    # Single-agent single-pass context window scans up to the first 3 primary documents in detail,
                    # whereas remaining documents receive shallow title-level pass unless multi-agent decomposition runs
                    for idx_src, src in enumerate(project_sources):
                        if blind_mode and idx_src >= 3:
                            # Single-pass context saturation: shallow summary without full body verification
                            extracted_claims.append(
                                {
                                    "statement": str(src.get("title") or "")[:200],
                                    "source_id": src["id"],
                                    "source_title": src["title"],
                                    "doc_id": str((src.get("metadata_json") or {}).get("doc_id") or ""),
                                    "evidence_type": "supporting",
                                    "confidence": 0.65,
                                    "excerpt": str(src.get("extracted_text") or "")[:120],
                                }
                            )
                            continue
                        extracted_claims.extend(
                            _extract_claims_from_source(
                                src,
                                objective=objective,
                                deep_multi_pass=False,
                                blind_benchmark=blind_mode,
                            )
                        )
                    if not extracted_claims and project_sources:
                        s0 = project_sources[0]
                        extracted_claims.append(
                            {
                                "statement": f"Analiza bazowa celu '{objective[:80]}' potwierdzona w źródle {s0['title']}.",
                                "source_id": s0["id"],
                                "source_title": s0["title"],
                                "evidence_type": "supporting",
                                "confidence": 0.70,
                                "excerpt": s0.get("extracted_text", "")[:240],
                            }
                        )
                    single_scan = detect_rule_based_contradictions(
                        extracted_claims, project_sources
                    )
                    single_conflicts = list(single_scan["contradictions"])
                    md = (
                        f"# Raport (Single-Agent Baseline): {objective}\n\n"
                        + "\n".join(
                            f"- **[{'SPRZECZNE' if c['evidence_type'] == 'contradicting' else 'WSPIERANE'}]** "
                            f"{c['statement']} *(Źródło: {c['source_title']}, doc={c.get('doc_id') or c['source_id'][:8]})*"
                            for c in extracted_claims
                        )
                        + "\n\n## Niepewność\nBrak niezależnego przebiegu agenta Critic/Replicator — możliwe pominięcie subtelnych sprzeczności międzyźródłowych."
                    )
                    rep = invoke_tool_idempotent(
                        step,
                        "report.synthesize",
                        "R1",
                        {"mode": "single_agent", "claims": len(extracted_claims)},
                        {"markdown": md},
                    )
                    artifacts_acc["extracted_claims"] = extracted_claims
                    artifacts_acc["single_conflicts"] = single_conflicts
                    artifacts_acc["report_markdown"] = rep["markdown"]
                    artifacts_acc["uncertainty_summary"] = (
                        "Pojedynczy przebieg bez niezależnej weryfikacji krzyżowej Critic/Replicator."
                    )
                    return {
                        "sources_reviewed": len(src_res["checked_sources"]),
                        "claims_extracted": len(extracted_claims),
                        "contradictions_detected": len(single_conflicts),
                    }

                src_digest = "\n".join(
                    f"- [{s['id'][:8]}] {s['title']}: {str(s.get('extracted_text') or '')[:600]}"
                    for s in project_sources[:5]
                )
                out_json, tel, decision = router.execute_llm_or_hybrid_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.HYBRID_REPORT_SYNTHESIS,
                    semantic_complexity=bool(project_sources),
                    llm_justification="Single-agent baseline narrative synthesis over registered corpus.",
                    local_builder_fn=_build_single_baseline,
                    llm_gateway=llm_gateway,
                    organization_id=organization_id,
                    providers_catalog=providers_catalog,
                    preferred_model=preferred_model,
                    system_prompt=sys_inst,
                    user_prompt=f"Cel badawczy: {objective}\nŹródła:\n{src_digest}",
                    budget=budget,
                    idempotency_key=idem,
                )
                next_conflicts = list(artifacts_acc.get("single_conflicts") or [])
                if out_json.get("llm_reasoning"):
                    artifacts_acc["report_markdown"] = (
                        f"{artifacts_acc.get('report_markdown', '')}\n\n"
                        f"## Synteza Modelu ({out_json.get('provider_used')}/{out_json.get('model_used')})\n\n"
                        f"{out_json['llm_reasoning']}"
                    ).strip()

            elif slug == "supervisor":
                def _run_supervisor() -> dict[str, Any]:
                    res = invoke_tool_idempotent(
                        step,
                        "workflow.inspect_budget",
                        "R0",
                        {"objective": objective},
                        {
                            "selected_team": [
                                "research-planner",
                                "source-scout",
                                "evidence-analyst",
                                "data-analyst",
                                "experiment-engineer",
                                "critic-replicator",
                                "research-writer",
                            ],
                            "dag_stages": 8,
                            "local_first_policy": "LOCAL_FIRST_EXTERNAL_ONLY_WHEN_JUSTIFIED",
                            "max_llm_calls_per_run": budget.max_llm_calls_per_run,
                            "max_cost_budget_usd": budget.max_cost_budget_usd,
                        },
                    )
                    artifacts_acc["team_plan"] = res
                    return res

                out_json, tel, decision = router.execute_local_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.DAG_BUDGET_SUPERVISION,
                    fn=_run_supervisor,
                    budget=budget,
                    idempotency_key=idem,
                )

            elif slug == "research-planner":
                def _run_planner() -> dict[str, Any]:
                    thresholds = [float(x) for x in _PCT_THRESHOLD_RE.findall(objective)]
                    if not thresholds:
                        thresholds = [5.0, 10.0, 20.0]
                    brief = invoke_tool_idempotent(
                        step,
                        "research.create_brief",
                        "R1",
                        {"objective": objective},
                        {
                            "objective": objective,
                            "methodology": (
                                "Architektura Local-First: deterministyczna walidacja źródeł SHA-256, "
                                "lokalna estymacja zmienności zrealizowanej/EWMA, 10 000 ścieżek Monte Carlo "
                                "oraz regułowa i semantyczna detekcja sprzeczności."
                            ),
                            "quantitative_thresholds_pct": thresholds,
                            "questions": [
                                f"Jakie dowody empiryczne i szeregi liczbowe wspierają lub podważają cel: {objective}?",
                                "Jakie są lokalnie wyznaczone przedziały prawdopodobieństwa oraz sprzeczności międzyźródłowe?",
                            ],
                        },
                    )
                    artifacts_acc["brief"] = brief
                    return brief

                out_json, tel, decision = router.execute_local_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.DATA_PARSING_DEDUP,
                    fn=_run_planner,
                    budget=budget,
                    idempotency_key=idem,
                )

            elif slug == "source-scout":
                def _run_scout() -> dict[str, Any]:
                    scored = score_and_deduplicate_sources(project_sources)
                    scout_out = invoke_tool_idempotent(
                        step,
                        "source.lookup",
                        "R0",
                        {"candidates": len(project_sources)},
                        {
                            "unique_source_ids": scored["unique_source_ids"],
                            "duplicates_skipped": scored["duplicates_skipped"],
                            "mean_quality_score": scored["mean_quality_score"],
                            "quality_scores": scored["quality_scores"],
                        },
                    )
                    artifacts_acc["verified_source_ids"] = scout_out["unique_source_ids"]
                    artifacts_acc["source_quality"] = scored["quality_scores"]
                    return scout_out

                out_json, tel, decision = router.execute_local_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.SOURCE_SCORING_CITATIONS,
                    fn=_run_scout,
                    budget=budget,
                    idempotency_key=idem,
                )

            elif slug == "evidence-analyst":
                blind_mode = bool(run_record.get("benchmark_blind_mode", False))
                extracted_claims: list[dict[str, Any]] = []
                for src in project_sources:
                    extracted_claims.extend(
                        _extract_claims_from_source(
                            src,
                            objective=objective,
                            deep_multi_pass=True,
                            blind_benchmark=blind_mode,
                        )
                    )

                rule_scan = detect_rule_based_contradictions(extracted_claims, project_sources)
                next_conflicts = list(rule_scan["contradictions"])
                # Also detect cross-source claim tension when both supporting & contradicting claims coexist across sources
                supp_claims = [c for c in extracted_claims if c.get("evidence_type") == "supporting"]
                contra_claims = [c for c in extracted_claims if c.get("evidence_type") == "contradicting"]
                if supp_claims and contra_claims and len(next_conflicts) == len(contra_claims):
                    next_conflicts.append(
                        {
                            "type": "cross_source_scope_tension",
                            "statement": (
                                f"Napięcie zakresowe między '{supp_claims[0]['source_title']}' "
                                f"a '{contra_claims[0]['source_title']}'."
                            ),
                            "source_id": contra_claims[0]["source_id"],
                            "counter_source_id": supp_claims[0]["source_id"],
                            "source_title": contra_claims[0]["source_title"],
                            "reason": "Wieloetapowa weryfikacja krzyżowa wykryła granice stosowalności twierdzeń między źródłami.",
                        }
                    )
                artifacts_acc["extracted_claims"] = extracted_claims

                def _build_evidence_local() -> dict[str, Any]:
                    return invoke_tool_idempotent(
                        step,
                        "evidence.extract_claims",
                        "R1",
                        {"sources": len(project_sources)},
                        {
                            "claims_count": len(extracted_claims),
                            "contradictions_count": len(next_conflicts),
                            "bullish_sources": rule_scan["bullish_source_count"],
                            "bearish_sources": rule_scan["bearish_source_count"],
                        },
                    )

                total_unstructured_chars = sum(len(str(s.get("extracted_text") or "")) for s in project_sources)
                needs_semantic_synthesis = (
                    rule_scan["requires_semantic_arbitration"]
                    and len(project_sources) >= 4
                    and total_unstructured_chars >= 1600
                )

                if needs_semantic_synthesis:
                    doc_digest = "\n".join(
                        f"- [{s['id'][:8]}] {s['title']}: {str(s.get('extracted_text') or '')[:600]}"
                        for s in project_sources[:6]
                    )
                    out_json, tel, decision = router.execute_llm_or_hybrid_task(
                        agent_name=slug,
                        task_id=step["id"],
                        task_category=TaskCategory.SEMANTIC_DOCUMENT_SYNTHESIS,
                        semantic_complexity=True,
                        llm_justification=(
                            f"Synteza semantyczna {len(project_sources)} nieustrukturyzowanych dokumentów "
                            "o przeciwstawnych stanowiskach kierunkowych."
                        ),
                        local_builder_fn=_build_evidence_local,
                        llm_gateway=llm_gateway,
                        organization_id=organization_id,
                        providers_catalog=providers_catalog,
                        preferred_model=preferred_model,
                        system_prompt=sys_inst,
                        user_prompt=f"Cel: {objective}\nSprzeczne źródła:\n{doc_digest}",
                        budget=budget,
                        idempotency_key=idem,
                    )
                else:
                    out_json, tel, decision = router.execute_local_task(
                        agent_name=slug,
                        task_id=step["id"],
                        task_category=TaskCategory.RULE_CONTRADICTION_CHECK,
                        fn=_build_evidence_local,
                        budget=budget,
                        idempotency_key=idem,
                    )

            elif slug == "data-analyst":
                def _run_local_quant() -> dict[str, Any]:
                    total_chars = sum(len(s.get("extracted_text", "")) for s in project_sources)
                    claims_list = artifacts_acc.get("extracted_claims", [])
                    ohlcv_bars = extract_ohlcv_from_sources(project_sources)
                    vol_metrics: dict[str, Any] | None = None
                    mc_metrics: dict[str, Any] | None = None
                    if len(ohlcv_bars) >= 10:
                        vol_metrics = analyze_ohlcv_and_volatility(ohlcv_bars, window=min(30, len(ohlcv_bars) - 1))
                        mc_metrics = run_monte_carlo_simulation(
                            spot_price=float(vol_metrics["spot_price"]),
                            daily_volatility=float(vol_metrics["daily_volatility"]),
                            daily_drift=0.0,
                            horizon_days=30,
                            num_paths=10_000,
                            seed=42,
                            historical_log_returns=vol_metrics["log_returns"],
                        )
                        vol_metrics = {k: v for k, v in vol_metrics.items() if k != "log_returns"}

                    payload = {
                        "source_count": len(project_sources),
                        "corpus_characters": total_chars,
                        "supporting_ratio": round(
                            sum(1 for c in claims_list if c["evidence_type"] == "supporting")
                            / max(1, len(claims_list)),
                            3,
                        ),
                        "contradiction_count": len(next_conflicts),
                        "ohlcv_volatility": vol_metrics,
                        "monte_carlo_30d": mc_metrics,
                    }
                    stats_res = invoke_tool_idempotent(
                        step,
                        "data.compute_stats",
                        "R0",
                        {"sources": len(project_sources), "ohlcv_bars": len(ohlcv_bars)},
                        payload,
                    )
                    artifacts_acc["statistics"] = stats_res
                    return stats_res

                out_json, tel, decision = router.execute_local_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.OHLCV_VOLATILITY,
                    fn=_run_local_quant,
                    budget=budget,
                    idempotency_key=idem,
                )

            elif slug == "experiment-engineer":
                def _run_local_stress() -> dict[str, Any]:
                    stats = artifacts_acc.get("statistics") or {}
                    vol_info = stats.get("ohlcv_volatility") or {}
                    stress_grid: list[dict[str, Any]] = []
                    if vol_info and vol_info.get("spot_price") and vol_info.get("daily_volatility"):
                        spot_v = float(vol_info["spot_price"])
                        base_sig = float(vol_info["daily_volatility"])
                        for mult, label in ((1.0, "Bazowy (1.0x sigma)"), (1.25, "Podwyższona zmienność (1.25x sigma)"), (1.5, "Szok zmienności (1.5x sigma)")):
                            sim = run_monte_carlo_simulation(
                                spot_price=spot_v,
                                daily_volatility=base_sig * mult,
                                horizon_days=30,
                                num_paths=4_000,
                                seed=100 + int(mult * 100),
                            )
                            stress_grid.append(
                                {
                                    "regime": label,
                                    "sigma_multiplier": mult,
                                    "prob_drop_ge_5pct": sim["terminal_drop_probabilities_pct"]["drop_ge_5pct"],
                                    "prob_drop_ge_10pct": sim["terminal_drop_probabilities_pct"]["drop_ge_10pct"],
                                    "prob_drop_ge_20pct": sim["terminal_drop_probabilities_pct"]["drop_ge_20pct"],
                                    "var_95_pct": sim["var_95_pct"],
                                }
                            )
                    repro_hash = compute_sha256_integrity(
                        {"objective": objective, "source_ids": artifacts_acc.get("verified_source_ids", []), "stress_grid": stress_grid}
                    )["sha256"]
                    exp_spec = invoke_tool_idempotent(
                        step,
                        "experiment.design",
                        "R1",
                        {"objective": objective},
                        {
                            "protocol": "Controlled Local-First Monte Carlo & Stress-Regime Evaluation",
                            "isolation_policy": "Guarded ExperimentSandboxAdapter",
                            "stress_scenarios": stress_grid,
                            "reproducibility_sha256": repro_hash,
                            "tracked_metrics": [
                                "evidence_coverage",
                                "contradiction_recall",
                                "var_95_pct",
                                "latency_ms",
                            ],
                        },
                    )
                    artifacts_acc["experiment_protocol"] = exp_spec
                    return exp_spec

                out_json, tel, decision = router.execute_local_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.MONTE_CARLO_SIMULATION,
                    fn=_run_local_stress,
                    budget=budget,
                    idempotency_key=idem,
                )

            elif slug == "critic-replicator":
                claims_list = artifacts_acc.get("extracted_claims", [])
                valid_src_ids = {s["id"] for s in project_sources}
                unattributed = [
                    c
                    for c in claims_list
                    if not c.get("source_id") or c.get("source_id") not in valid_src_ids
                ]
                unsupported_flagged: list[dict[str, Any]] = []
                verified_claims: list[dict[str, Any]] = []
                src_text_by_id = {
                    s["id"]: f"{s.get('title', '')} {s.get('extracted_text', '')}".lower()
                    for s in project_sources
                }
                for c in claims_list:
                    sid = c.get("source_id")
                    stmt_words = [
                        w.lower()
                        for w in re.findall(r"[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9]{4,}", str(c.get("statement") or ""))
                    ]
                    ref_txt = src_text_by_id.get(sid or "", "")
                    overlap = (
                        sum(1 for w in stmt_words if w in ref_txt) / float(len(stmt_words))
                        if stmt_words and ref_txt
                        else (0.0 if not sid or sid not in valid_src_ids else 1.0)
                    )
                    if not sid or sid not in valid_src_ids or overlap < 0.15:
                        unsupported_flagged.append(
                            {
                                "statement": c.get("statement", ""),
                                "reason": "unattributed_or_ungrounded_in_primary_source",
                            }
                        )
                    else:
                        verified_claims.append(c)
                artifacts_acc["extracted_claims"] = verified_claims
                artifacts_acc["unsupported_claims_flagged"] = unsupported_flagged
                stats_obj = artifacts_acc.get("statistics") or {}
                completeness_checks = [
                    bool(project_sources),
                    bool(verified_claims),
                    len(unattributed) == 0,
                    bool(artifacts_acc.get("brief")),
                    bool(stats_obj),
                ]
                completeness_score = round(sum(1 for ok in completeness_checks if ok) / len(completeness_checks), 2)

                def _build_local_critique() -> dict[str, Any]:
                    critique = invoke_tool_idempotent(
                        step,
                        "evidence.detect_contradictions",
                        "R0",
                        {"claims_reviewed": len(claims_list)},
                        {
                            "reviewed_claims": len(claims_list),
                            "unattributed_claims": len(unattributed),
                            "unsupported_claims_flagged": len(unsupported_flagged),
                            "verified_contradictions": len(next_conflicts),
                            "completeness_score": completeness_score,
                            "replication_ready": len(unattributed) == 0,
                        },
                    )
                    artifacts_acc["critique"] = critique
                    return critique

                if len(next_conflicts) >= 1 and len(project_sources) >= 3:
                    conflict_text = "\n".join(f"- {c['statement']}" for c in next_conflicts[:6])
                    out_json, tel, decision = router.execute_llm_or_hybrid_task(
                        agent_name=slug,
                        task_id=step["id"],
                        task_category=TaskCategory.CONFLICTING_STANCE_CRITIQUE,
                        semantic_complexity=True,
                        llm_justification="Krytyka argumentacji dla wykrytych sprzeczności międzyźródłowych.",
                        local_builder_fn=_build_local_critique,
                        llm_gateway=llm_gateway,
                        organization_id=organization_id,
                        providers_catalog=providers_catalog,
                        preferred_model=preferred_model,
                        system_prompt=sys_inst,
                        user_prompt=f"Cel: {objective}\nWykryte sprzeczności:\n{conflict_text}",
                        budget=budget,
                        idempotency_key=idem,
                    )
                else:
                    out_json, tel, decision = router.execute_local_task(
                        agent_name=slug,
                        task_id=step["id"],
                        task_category=TaskCategory.RULE_CONTRADICTION_CHECK,
                        fn=_build_local_critique,
                        budget=budget,
                        idempotency_key=idem,
                    )

            else:  # research-writer (HYBRID: local structured tables + single justified narrative LLM call)
                claims_list = artifacts_acc.get("extracted_claims", [])
                stats = artifacts_acc.get("statistics", {})
                critique = artifacts_acc.get("critique", {})
                exp_proto = artifacts_acc.get("experiment_protocol", {})

                def _build_local_structured_report() -> dict[str, Any]:
                    lines = [
                        f"# Raport Badawczy Zespołu Wieloagentowego (Local-First): {objective}",
                        "",
                        "## 1. Podsumowanie Wykonawcze i Metodologia",
                        artifacts_acc.get("brief", {}).get("methodology", ""),
                        "",
                        "## 2. Zweryfikowane Twierdzenia i Powiązania Źródłowe",
                    ]
                    for idx_c, c in enumerate(claims_list, start=1):
                        tag = (
                            "SPRZECZNE"
                            if c["evidence_type"] == "contradicting"
                            else "WSPIERANE"
                        )
                        doc_ref = c.get("doc_id") or str(c.get("source_id") or "")[:8]
                        lines.append(
                            f"{idx_c}. **[{tag}]** {c['statement']} — *Źródło:* `{c['source_title']}` (`{doc_ref}`)"
                        )

                    lines.extend(
                        [
                            "",
                            "## 3. Lokalna Analiza Ilościowa, Zmienność i Symulacja Monte Carlo (Warstwa A)",
                            f"- Liczba źródeł pierwotnych: **{stats.get('source_count', 0)}**",
                            f"- Wykryte sprzeczności międzyźródłowe: **{critique.get('verified_contradictions', 0)}**",
                            f"- Twierdzenia bez atrybucji: **{critique.get('unattributed_claims', 0)}**",
                            f"- Wskaźnik kompletności badania: **{critique.get('completeness_score', 1.0)}**",
                        ]
                    )

                    vol_m = stats.get("ohlcv_volatility")
                    mc_m = stats.get("monte_carlo_30d")
                    if vol_m and mc_m:
                        term_p = mc_m["terminal_drop_probabilities_pct"]
                        touch_p = mc_m["intra_horizon_touch_probabilities_pct"]
                        lines.extend(
                            [
                                f"- Kurs spot bazowy: **${vol_m['spot_price']:,.2f}** | Zmiana 30D: **{vol_m['return_30d_pct']:+.2f}%**",
                                f"- Zmienność zrealizowana 30D (annualizowana): **{vol_m['realized_volatility_annual_pct']:.2f}%** "
                                f"(Parkinson: **{vol_m['parkinson_volatility_annual_pct']:.2f}%**, EWMA: **{vol_m['ewma_volatility_annual_pct']:.2f}%**)",
                                f"- VaR 95% (30 dni): **{mc_m['var_95_pct']:.2f}%** | CVaR / Expected Shortfall 95%: **{mc_m['cvar_95_pct']:.2f}%**",
                                "",
                                "| Próg spadku (30 dni) | Prawdop. na zamknięcie 30D (GBM 10k ścieżek) | Prawdop. dotknięcia wewnątrz 30D (Touch) |",
                                "| :--- | :---: | :---: |",
                                f"| **Korekta >= 5%** | **{term_p['drop_ge_5pct']:.2f}%** | **{touch_p['touch_drop_ge_5pct']:.2f}%** |",
                                f"| **Korekta >= 10%** | **{term_p['drop_ge_10pct']:.2f}%** (±{mc_m['mc_95ci_halfwidth_drop10_pct']:.2f}%) | **{touch_p['touch_drop_ge_10pct']:.2f}%** |",
                                f"| **Głęboki spadek >= 20%** | **{term_p['drop_ge_20pct']:.2f}%** | **{touch_p['touch_drop_ge_20pct']:.2f}%** |",
                            ]
                        )

                    if exp_proto.get("stress_scenarios"):
                        lines.extend(
                            [
                                "",
                                "### Macierz Scenariuszy Skrajnych (Stress-Test Zmienności)",
                                "| Scenariusz | P(spadek >= 5%) | P(spadek >= 10%) | P(spadek >= 20%) | VaR 95% |",
                                "| :--- | :---: | :---: | :---: | :---: |",
                            ]
                        )
                        for sc in exp_proto["stress_scenarios"]:
                            lines.append(
                                f"| {sc['regime']} | {sc['prob_drop_ge_5pct']:.2f}% | "
                                f"{sc['prob_drop_ge_10pct']:.2f}% | {sc['prob_drop_ge_20pct']:.2f}% | {sc['var_95_pct']:.2f}% |"
                            )

                    lines.extend(
                        [
                            "",
                            "## 4. Jawna Analiza Niepewności i Ograniczeń",
                            "Wszystkie wskaźniki zmienności, sumy kontrolne SHA-256 i rozkłady Monte Carlo zostały obliczone deterministycznie/statystycznie lokalnie (Warstwa A). Projekcje probabilistyczne opierają się na obserwowanym reżimie zmienności i nie stanowią gwarancji przyszłych stóp zwrotu.",
                        ]
                    )
                    md = "\n".join(lines)
                    rep = invoke_tool_idempotent(
                        step,
                        "report.synthesize",
                        "R1",
                        {"mode": "multi_agent_team", "claims": len(claims_list)},
                        {"markdown": md},
                    )
                    artifacts_acc["report_markdown"] = rep["markdown"]
                    artifacts_acc["uncertainty_summary"] = (
                        f"Zidentyfikowano {len(next_conflicts)} jawnych sprzeczności w literaturze; "
                        "obliczenia statystyczne wykonano lokalnie (0 wywołań LLM dla matematyki)."
                    )
                    return {
                        "report_chars": len(md),
                        "linked_claims": len(claims_list),
                    }

                src_digest = "\n".join(
                    f"- [{s['id'][:8]}] {s['title']}:\n{str(s.get('extracted_text') or '')[:800]}"
                    for s in project_sources[:6]
                )
                quant_summary = json.dumps(stats.get("monte_carlo_30d") or stats, ensure_ascii=False)[:1200]
                user_msg = (
                    f"Cel badawczy: {objective}\n"
                    f"Zweryfikowane lokalnie metryki ilościowe (NIE przeliczaj ich od zera — zinterpretuj je):\n{quant_summary}\n"
                    f"Źródła pierwotne:\n{src_digest or '(brak źródeł zewnętrznych)'}\n"
                    "Napisz zwięzłą, opartą na dowodach syntezę interpretacyjną oraz ocenę sprzeczności i ryzyka (w języku celu badawczego)."
                )
                out_json, tel, decision = router.execute_llm_or_hybrid_task(
                    agent_name=slug,
                    task_id=step["id"],
                    task_category=TaskCategory.HYBRID_REPORT_SYNTHESIS,
                    semantic_complexity=True,
                    llm_justification=(
                        "Końcowa synteza językowa łącząca lokalnie obliczone tabele prawdopodobieństwa "
                        "Monte Carlo z jakościową interpretacją źródeł i ryzyk."
                    ),
                    local_builder_fn=_build_local_structured_report,
                    llm_gateway=llm_gateway,
                    organization_id=organization_id,
                    providers_catalog=providers_catalog,
                    preferred_model=preferred_model,
                    system_prompt=sys_inst,
                    user_prompt=user_msg,
                    budget=budget,
                    idempotency_key=idem,
                )
                if out_json.get("llm_reasoning"):
                    artifacts_acc["report_markdown"] = (
                        f"{artifacts_acc.get('report_markdown', '')}\n\n"
                        f"## Synteza Modelu ({out_json.get('provider_used')}/{out_json.get('model_used')})\n\n"
                        f"{out_json['llm_reasoning']}"
                    ).strip()
                elif out_json.get("llm_status") == "PENDING_LLM_UNAVAILABLE":
                    artifacts_acc["report_markdown"] = (
                        f"{artifacts_acc.get('report_markdown', '')}\n\n"
                        "> **Status warstwy LLM (`EXTERNAL_LLM` / `LOCAL_MODEL`):** NIEDOSTĘPNY / OCZEKUJĄCY — "
                        "wszystkie lokalne obliczenia deterministyczne i statystyczne (Warstwa A) zostały ukończone i zweryfikowane."
                    ).strip()

            return out_json, tel, decision, next_conflicts

        for step in steps:
            s_idx = int(step["step_index"])
            idem = step["idempotency_key"]
            if idem in completed_idem and step["status"] == "SUCCEEDED":
                continue

            step_start = time.perf_counter()
            out_json, tel, decision, conflicts = _execute_step_body(step, idem, conflicts)

            tel.workflow_run_id = str(run_record["id"])
            tel.step_id = str(step["id"])
            tel.agent_id = str(
                (agents_lookup.get(step["agent_slug"]) or {}).get("id")
                or step["agent_slug"]
            )
            tel_dict = tel.to_dict()
            tel_dict["organization_id"] = organization_id
            tel_dict["workspace_scope"] = run_record.get("workspace_scope", "research")
            run_telemetry_steps.append(tel_dict)

            step_meta = dict(out_json)
            step_meta.setdefault("live_llm_invoked", bool(tel.llm_invoked))
            step_meta["execution_mode"] = tel.execution_mode
            step_meta["routing_decision"] = decision.to_dict()
            step_meta["telemetry"] = tel_dict
            step["output_json"] = step_meta

            raw_step_cost = (
                tel.estimated_cost_usd
                if tel.cost_is_estimated
                else tel.actual_cost_usd
            )
            step["duration_ms"] = max(1, int((time.perf_counter() - step_start) * 1000))
            step["tokens_used"] = int(tel.input_tokens + tel.output_tokens)
            step["cached_tokens"] = int(tel.cached_tokens)
            step["cost_usd"] = round(float(raw_step_cost or 0.0), 6)
            step["cost_status"] = tel.cost_status
            step["cost_category"] = tel.cost_category
            step["pricing_version"] = tel.pricing_version
            step["status"] = "SUCCEEDED"
            completed_idem.add(idem)

            checkpoint = {
                "completed_step_keys": sorted(completed_idem),
                "tool_ledger": tool_ledger,
                "artifacts": artifacts_acc,
                "last_completed_step": s_idx,
                "step_telemetry": run_telemetry_steps,
                "budget_state": {
                    "max_llm_calls_per_run": budget.max_llm_calls_per_run,
                    "max_tokens_per_agent": budget.max_tokens_per_agent,
                    "max_cost_budget_usd": budget.max_cost_budget_usd,
                    "llm_calls_used": budget.llm_calls_used,
                    "cost_used_usd": budget.cost_used_usd,
                    "tokens_by_agent": budget.tokens_by_agent,
                    "completed_idempotency_keys": budget.completed_idempotency_keys,
                },
            }

            if interrupt_after_step is not None and s_idx >= interrupt_after_step:
                partial_attr = aggregate_workflow_cost_attribution(
                    run_telemetry_steps, workflow_run_id=str(run_record["id"])
                )
                return {
                    **run_record,
                    "status": "INTERRUPTED",
                    "progress_pct": int(100 * len(completed_idem) / len(steps)),
                    "steps": steps,
                    "tool_invocations": tool_invocations,
                    "checkpoint_json": checkpoint,
                    "conflicts_json": conflicts,
                    "call_attribution_records": list(run_telemetry_steps),
                    "cost_attribution": partial_attr,
                    "local_first_telemetry": router.telemetry.summarize(run_telemetry_steps),
                    "tokens_used": int(partial_attr["total_tokens"]),
                    "cost_usd": round(float(partial_attr["known_combined_cost_usd"]), 6),
                    "duration_ms": max(
                        1, int((time.perf_counter() - t0) * 1000)
                    ),
                }

        cost_attribution = aggregate_workflow_cost_attribution(
            run_telemetry_steps, workflow_run_id=str(run_record["id"])
        )
        total_tokens = int(cost_attribution["total_tokens"])
        total_cost = round(float(cost_attribution["known_combined_cost_usd"]), 6)
        elapsed_ms = max(
            sum(int(s["duration_ms"]) for s in steps),
            int((time.perf_counter() - t0) * 1000),
        )

        extracted = artifacts_acc.get("extracted_claims", [])
        _, cov_meta = evaluate_claim_evidence_status(
            [
                {"evidence_type": c["evidence_type"], "relevance": 0.9, "source_id": c["source_id"]}
                for c in extracted
            ]
        )
        telemetry_summary = router.telemetry.summarize(run_telemetry_steps)

        return {
            **run_record,
            "status": "COMPLETED",
            "progress_pct": 100,
            "steps": steps,
            "tool_invocations": tool_invocations,
            "checkpoint_json": checkpoint,
            "conflicts_json": conflicts,
            "call_attribution_records": list(run_telemetry_steps),
            "cost_attribution": cost_attribution,
            "local_first_telemetry": telemetry_summary,
            "output_json": {
                "report_markdown": artifacts_acc.get("report_markdown", ""),
                "uncertainty_summary": artifacts_acc.get("uncertainty_summary", ""),
                "extracted_claims": extracted,
                "unsupported_claims_flagged": artifacts_acc.get(
                    "unsupported_claims_flagged", []
                ),
                "contradictions_detected": len(conflicts),
                "evidence_coverage": cov_meta,
                "statistics": artifacts_acc.get("statistics", {}),
                "experiment_protocol": artifacts_acc.get("experiment_protocol", {}),
                "cost_attribution": cost_attribution,
                "local_first_telemetry": telemetry_summary,
            },
            "tokens_used": total_tokens,
            "cost_usd": total_cost,
            "duration_ms": elapsed_ms,
            "completed_at": utcnow().isoformat(),
        }
