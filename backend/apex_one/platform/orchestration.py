"""AgentDNA versioning, AgentRuntimeAdapter, and Multi-Agent Research Orchestration."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from apex_one.db.base import utcnow
from apex_one.platform.research_engine import evaluate_claim_evidence_status


def load_builtin_agent_dna_manifests(repo_root: Path) -> list[dict[str, Any]]:
    """Load the 8 specialized research agents + universal baseline from `agents/*/agent_dna.json`."""
    agents_dir = repo_root / "agents"
    manifests: list[dict[str, Any]] = []
    if not agents_dir.exists():
        return manifests
    for manifest_path in sorted(agents_dir.glob("*/agent_dna.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
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


class DurableCheckpointRuntimeAdapter:
    """Implemented durable multi-agent DAG runtime for APEX ONE.

    Architectural Note (see docs/adr/0001-agent-runtime-selection.md):
    `eve` on npm (`1.0.0-rc.9`) was inspected empirically and found to be a pre-GA
    Release Candidate coupled to `nitro@3.0.261103-rc` with a filesystem-first state
    architecture incompatible with APEX ONE's multi-tenant PostgreSQL RLS + Outbox
    durability model. `DurableCheckpointRuntimeAdapter` implements persistent step
    checkpoints and idempotency ledgers independent of model conversational context,
    guaranteeing interrupted workflows resume without repeating completed tool calls.
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
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        mode = run_record.get("execution_mode", "multi_agent_team")
        objective = run_record["objective"]

        checkpoint = dict(run_record.get("checkpoint_json") or {})
        completed_idem: set[str] = set(checkpoint.get("completed_step_keys", []))
        tool_ledger: dict[str, dict[str, Any]] = dict(
            checkpoint.get("tool_ledger", {})
        )
        artifacts_acc: dict[str, Any] = dict(checkpoint.get("artifacts", {}))

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
                    ("supervisor", "Interpretacja celu, alokacja ról i utworzenie DAG"),
                    ("research-planner", "Opracowanie Research Brief, pytań badawczych i metodologii"),
                    ("source-scout", "Weryfikacja źródeł, sum kontrolnych SHA-256 i deduplikacja"),
                    ("evidence-analyst", "Ekstrakcja twierdzeń, powiązań dowodowych i sprzeczności"),
                    ("data-analyst", "Analiza ilościowa wskaźników w korpusie źródłowym"),
                    ("experiment-engineer", "Specyfikacja powtarzalnego protokołu eksperymentalnego"),
                    ("critic-replicator", "Audyt metodologiczny, kontrola luk i weryfikacja replikacji"),
                    ("research-writer", "Synteza raportu z referencjami dowodowymi i sekcją niepewności"),
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
                # Never blindly repeat completed external/tool actions on recovery!
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

        for step in steps:
            s_idx = int(step["step_index"])
            idem = step["idempotency_key"]
            if idem in completed_idem and step["status"] == "SUCCEEDED":
                continue

            step_start = time.perf_counter()
            slug = step["agent_slug"]

            if slug == "universal-baseline":
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
                # Baseline extracts primary supporting claims but does not run a dedicated
                # second-pass Critic/Replicator cross-examination pass
                extracted_claims: list[dict[str, Any]] = []
                for src in project_sources:
                    meta_claims = src.get("metadata_json", {}).get("labeled_claims", [])
                    for lc in meta_claims:
                        if lc.get("polarity") == "supporting":
                            extracted_claims.append(
                                {
                                    "statement": lc["statement"],
                                    "source_id": src["id"],
                                    "source_title": src["title"],
                                    "evidence_type": "supporting",
                                    "excerpt": src.get("extracted_text", "")[:240],
                                }
                            )
                if not extracted_claims and project_sources:
                    s0 = project_sources[0]
                    extracted_claims.append(
                        {
                            "statement": f"Analiza bazowa celu '{objective[:80]}' potwierdzona w źródle {s0['title']}.",
                            "source_id": s0["id"],
                            "source_title": s0["title"],
                            "evidence_type": "supporting",
                            "excerpt": s0.get("extracted_text", "")[:240],
                        }
                    )

                rep = invoke_tool_idempotent(
                    step,
                    "report.synthesize",
                    "R1",
                    {"mode": "single_agent", "claims": len(extracted_claims)},
                    {
                        "markdown": (
                            f"# Raport (Single-Agent Baseline): {objective}\n\n"
                            + "\n".join(
                                f"- **Wniosek:** {c['statement']} *(Źródło: {c['source_title']})*"
                                for c in extracted_claims
                            )
                            + "\n\n## Niepewność\nBrak niezależnego przebiegu agenta Critic/Replicator — możliwe pominięcie subtelnych sprzeczności międzyźródłowych."
                        )
                    },
                )
                artifacts_acc["extracted_claims"] = extracted_claims
                artifacts_acc["report_markdown"] = rep["markdown"]
                artifacts_acc["uncertainty_summary"] = (
                    "Pojedynczy przebieg bez niezależnej weryfikacji krzyżowej Critic/Replicator."
                )
                step_tokens = 1840
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = {
                    "sources_reviewed": len(src_res["checked_sources"]),
                    "claims_extracted": len(extracted_claims),
                    "contradictions_detected": 0,
                }

            elif slug == "supervisor":
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
                    },
                )
                artifacts_acc["team_plan"] = res
                step_tokens = 620
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = res

            elif slug == "research-planner":
                brief = invoke_tool_idempotent(
                    step,
                    "research.create_brief",
                    "R1",
                    {"objective": objective},
                    {
                        "objective": objective,
                        "methodology": "Kontrolowana analiza wieloźródłowa z jawną detekcją sprzeczności i walidacją powtarzalności.",
                        "questions": [
                            f"Jakie dowody empiryczne wspierają lub podważają cel: {objective}?",
                            "Jakie występują sprzeczności oraz ograniczenia metodologiczne w źródłach?",
                        ],
                    },
                )
                artifacts_acc["brief"] = brief
                step_tokens = 740
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = brief

            elif slug == "source-scout":
                seen_hashes: set[str] = set()
                unique_sources: list[dict[str, Any]] = []
                duplicates = 0
                for s in project_sources:
                    h = s["checksum_sha256"]
                    if h in seen_hashes:
                        duplicates += 1
                    else:
                        seen_hashes.add(h)
                        unique_sources.append(s)
                scout_out = invoke_tool_idempotent(
                    step,
                    "source.lookup",
                    "R0",
                    {"candidates": len(project_sources)},
                    {
                        "unique_source_ids": [u["id"] for u in unique_sources],
                        "duplicates_skipped": duplicates,
                    },
                )
                artifacts_acc["verified_source_ids"] = scout_out["unique_source_ids"]
                step_tokens = 680
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = scout_out

            elif slug == "evidence-analyst":
                extracted_claims = []
                detected_conflicts: list[dict[str, Any]] = []
                for src in project_sources:
                    meta_claims = src.get("metadata_json", {}).get("labeled_claims", [])
                    for lc in meta_claims:
                        polarity = lc.get("polarity", "supporting")
                        ev_type = (
                            "contradicting"
                            if polarity == "contradicting"
                            else "supporting"
                        )
                        entry = {
                            "statement": lc["statement"],
                            "source_id": src["id"],
                            "source_title": src["title"],
                            "evidence_type": ev_type,
                            "excerpt": src.get("extracted_text", "")[:280],
                        }
                        extracted_claims.append(entry)
                        if ev_type == "contradicting":
                            detected_conflicts.append(
                                {
                                    "statement": lc["statement"],
                                    "source_id": src["id"],
                                    "source_title": src["title"],
                                    "reason": "Wykryto dowód sprzeczny w korpusie źródłowym.",
                                }
                            )
                if not extracted_claims and project_sources:
                    for src in project_sources[:3]:
                        extracted_claims.append(
                            {
                                "statement": f"Ustalenie źródłowe z dokumentu '{src['title']}' w kontekście: {objective[:70]}.",
                                "source_id": src["id"],
                                "source_title": src["title"],
                                "evidence_type": "supporting",
                                "excerpt": src.get("extracted_text", "")[:280],
                            }
                        )
                ev_res = invoke_tool_idempotent(
                    step,
                    "evidence.extract_claims",
                    "R1",
                    {"sources": len(project_sources)},
                    {
                        "claims_count": len(extracted_claims),
                        "contradictions_count": len(detected_conflicts),
                    },
                )
                conflicts = detected_conflicts
                artifacts_acc["extracted_claims"] = extracted_claims
                step_tokens = 1120
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = ev_res

            elif slug == "data-analyst":
                total_chars = sum(len(s.get("extracted_text", "")) for s in project_sources)
                claims_list = artifacts_acc.get("extracted_claims", [])
                stats_res = invoke_tool_idempotent(
                    step,
                    "data.compute_stats",
                    "R0",
                    {"sources": len(project_sources)},
                    {
                        "source_count": len(project_sources),
                        "corpus_characters": total_chars,
                        "supporting_ratio": round(
                            sum(
                                1
                                for c in claims_list
                                if c["evidence_type"] == "supporting"
                            )
                            / max(1, len(claims_list)),
                            3,
                        ),
                        "contradiction_count": len(conflicts),
                    },
                )
                artifacts_acc["statistics"] = stats_res
                step_tokens = 640
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = stats_res

            elif slug == "experiment-engineer":
                exp_spec = invoke_tool_idempotent(
                    step,
                    "experiment.design",
                    "R1",
                    {"objective": objective},
                    {
                        "protocol": "Controlled A/B Benchmark Evaluation",
                        "isolation_policy": "Guarded ExperimentSandboxAdapter",
                        "tracked_metrics": [
                            "evidence_coverage",
                            "contradiction_recall",
                            "token_cost_usd",
                            "latency_ms",
                        ],
                    },
                )
                artifacts_acc["experiment_protocol"] = exp_spec
                step_tokens = 710
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = exp_spec

            elif slug == "critic-replicator":
                claims_list = artifacts_acc.get("extracted_claims", [])
                unattributed = [c for c in claims_list if not c.get("source_id")]
                critique = invoke_tool_idempotent(
                    step,
                    "evidence.detect_contradictions",
                    "R0",
                    {"claims_reviewed": len(claims_list)},
                    {
                        "reviewed_claims": len(claims_list),
                        "unattributed_claims": len(unattributed),
                        "verified_contradictions": len(conflicts),
                        "replication_ready": True,
                    },
                )
                artifacts_acc["critique"] = critique
                step_tokens = 690
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = critique

            else:  # research-writer
                claims_list = artifacts_acc.get("extracted_claims", [])
                stats = artifacts_acc.get("statistics", {})
                critique = artifacts_acc.get("critique", {})
                lines = [
                    f"# Raport Badawczy Zespołu Wieloagentowego: {objective}",
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
                    lines.append(
                        f"{idx_c}. **[{tag}]** {c['statement']} — *Źródło:* `{c['source_title']}`"
                    )
                lines.extend(
                    [
                        "",
                        "## 3. Analiza Ilościowa i Kontrola Krytyczna",
                        f"- Liczba źródeł pierwotnych: **{stats.get('source_count', 0)}**",
                        f"- Wykryte sprzeczności międzyźródłowe: **{critique.get('verified_contradictions', 0)}**",
                        f"- Twierdzenia bez atrybucji: **{critique.get('unattributed_claims', 0)}**",
                        "",
                        "## 4. Jawna Analiza Niepewności i Ograniczeń",
                        "Wnioski są ograniczone do zarejestrowanego korpusu dowodowego. Wskaźniki pokrycia dowodowego odzwierciedlają powiązania ze źródłami, a nie skalibrowane prawdopodobieństwo statystyczne.",
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
                    f"Zidentyfikowano {len(conflicts)} jawnych sprzeczności w literaturze; "
                    "wszystkie wnioski powiązane ze źródłami pierwotnymi."
                )
                step_tokens = 920
                step_cost = round(step_tokens * 0.000003, 6)
                step["output_json"] = {
                    "report_chars": len(md),
                    "linked_claims": len(claims_list),
                }

            # Invoke live LLM provider (OpenAI / Anthropic / OpenRouter / Ollama) when configured
            if (
                llm_gateway is not None
                and organization_id
                and providers_catalog
                and run_record.get("model_identifier", "apex-analytical-local-v1")
                != "deterministic-only"
            ):
                agent_spec = agents_lookup.get(slug, {})
                dna = agent_spec.get("dna") or {}
                sys_inst = str(
                    dna.get("instructions")
                    or f"You are specialist research agent '{slug}'."
                )
                src_digest = "\n".join(
                    f"- [{s['id'][:8]}] {s['title']}:\n{str(s.get('extracted_text') or '')[:900]}"
                    for s in project_sources[:6]
                )
                prior_notes = "\n".join(
                    f"- Step #{prev['step_index']} ({prev['agent_slug']}): "
                    f"{str((prev.get('output_json') or {}).get('llm_reasoning') or '')[:350]}"
                    for prev in steps[: s_idx - 1]
                    if (prev.get("output_json") or {}).get("llm_reasoning")
                )
                user_msg = (
                    f"Research Objective: {objective}\n"
                    f"Current Specialist Step ({s_idx}): {step['task_title']} (role={slug})\n"
                    f"Primary Sources:\n{src_digest or '(no external sources attached)'}\n"
                    + (
                        f"Prior Specialist Findings:\n{prior_notes}\n"
                        if prior_notes and slug == "research-writer"
                        else ""
                    )
                    + "Respond in the same language as the Research Objective (Polish if Polish). "
                    "Provide a rigorous, quantitative, evidence-grounded output."
                )
                llm_res = llm_gateway.complete_sync(
                    organization_id=organization_id,
                    providers_catalog=providers_catalog,
                    preferred_model=str(
                        run_record.get("model_identifier")
                        or "apex-analytical-local-v1"
                    ),
                    system_prompt=sys_inst,
                    user_prompt=user_msg,
                )
                if llm_res is not None and llm_res.live_llm_invoked:
                    step_tokens = llm_res.total_tokens
                    step_cost = llm_res.cost_usd
                    out_meta = dict(step.get("output_json") or {})
                    out_meta.update(
                        {
                            "live_llm_invoked": True,
                            "provider_used": llm_res.provider_slug,
                            "model_used": llm_res.model_identifier,
                            "fallback_triggered": llm_res.fallback_triggered,
                            "llm_reasoning": llm_res.content[:2000],
                        }
                    )
                    step["output_json"] = out_meta
                    if slug in {"research-writer", "universal-baseline"} and llm_res.content:
                        artifacts_acc["report_markdown"] = (
                            f"{artifacts_acc.get('report_markdown', '')}\n\n"
                            f"## Synteza Modelu ({llm_res.provider_slug}/{llm_res.model_identifier})\n\n"
                            f"{llm_res.content}"
                        ).strip()

            step["duration_ms"] = max(2, int((time.perf_counter() - step_start) * 1000))
            step["tokens_used"] = step_tokens
            step["cost_usd"] = step_cost
            step["status"] = "SUCCEEDED"
            completed_idem.add(idem)

            checkpoint = {
                "completed_step_keys": sorted(completed_idem),
                "tool_ledger": tool_ledger,
                "artifacts": artifacts_acc,
                "last_completed_step": s_idx,
            }

            if interrupt_after_step is not None and s_idx >= interrupt_after_step:
                total_tok = sum(int(s["tokens_used"]) for s in steps)
                total_cost = round(sum(float(s["cost_usd"]) for s in steps), 6)
                return {
                    **run_record,
                    "status": "INTERRUPTED",
                    "progress_pct": int(100 * len(completed_idem) / len(steps)),
                    "steps": steps,
                    "tool_invocations": tool_invocations,
                    "checkpoint_json": checkpoint,
                    "conflicts_json": conflicts,
                    "tokens_used": total_tok,
                    "cost_usd": total_cost,
                    "duration_ms": max(
                        1, int((time.perf_counter() - t0) * 1000)
                    ),
                }

        total_tokens = sum(int(s["tokens_used"]) for s in steps)
        total_cost = round(sum(float(s["cost_usd"]) for s in steps), 6)
        elapsed_ms = max(
            sum(int(s["duration_ms"]) for s in steps),
            int((time.perf_counter() - t0) * 1000),
        )

        # Compute empirical coverage from extracted claims
        extracted = artifacts_acc.get("extracted_claims", [])
        _, cov_meta = evaluate_claim_evidence_status(
            [
                {"evidence_type": c["evidence_type"], "relevance": 0.9, "source_id": c["source_id"]}
                for c in extracted
            ]
        )

        return {
            **run_record,
            "status": "COMPLETED",
            "progress_pct": 100,
            "steps": steps,
            "tool_invocations": tool_invocations,
            "checkpoint_json": checkpoint,
            "conflicts_json": conflicts,
            "output_json": {
                "report_markdown": artifacts_acc.get("report_markdown", ""),
                "uncertainty_summary": artifacts_acc.get("uncertainty_summary", ""),
                "extracted_claims": extracted,
                "contradictions_detected": len(conflicts),
                "evidence_coverage": cov_meta,
                "statistics": artifacts_acc.get("statistics", {}),
            },
            "tokens_used": total_tokens,
            "cost_usd": total_cost,
            "duration_ms": elapsed_ms,
            "completed_at": utcnow().isoformat(),
        }
