"""Research Engine state machine (13 states) and structured Evidence Graph logic."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ResearchState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    ANALYZING = "ANALYZING"
    HYPOTHESIS_FORMATION = "HYPOTHESIS_FORMATION"
    EXPERIMENT_DESIGN = "EXPERIMENT_DESIGN"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REPORTING = "REPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ALLOWED_RESEARCH_TRANSITIONS: dict[ResearchState, frozenset[ResearchState]] = {
    ResearchState.CREATED: frozenset(
        {ResearchState.PLANNING, ResearchState.CANCELLED, ResearchState.FAILED}
    ),
    ResearchState.PLANNING: frozenset(
        {ResearchState.RESEARCHING, ResearchState.CANCELLED, ResearchState.FAILED}
    ),
    ResearchState.RESEARCHING: frozenset(
        {ResearchState.ANALYZING, ResearchState.PLANNING, ResearchState.CANCELLED, ResearchState.FAILED}
    ),
    ResearchState.ANALYZING: frozenset(
        {
            ResearchState.HYPOTHESIS_FORMATION,
            ResearchState.REPORTING,
            ResearchState.RESEARCHING,
            ResearchState.CANCELLED,
            ResearchState.FAILED,
        }
    ),
    ResearchState.HYPOTHESIS_FORMATION: frozenset(
        {
            ResearchState.EXPERIMENT_DESIGN,
            ResearchState.REPORTING,
            ResearchState.CANCELLED,
            ResearchState.FAILED,
        }
    ),
    ResearchState.EXPERIMENT_DESIGN: frozenset(
        {
            ResearchState.AWAITING_APPROVAL,
            ResearchState.EXECUTING,
            ResearchState.CANCELLED,
            ResearchState.FAILED,
        }
    ),
    ResearchState.AWAITING_APPROVAL: frozenset(
        {
            ResearchState.EXECUTING,
            ResearchState.EXPERIMENT_DESIGN,
            ResearchState.CANCELLED,
            ResearchState.FAILED,
        }
    ),
    ResearchState.EXECUTING: frozenset(
        {ResearchState.VERIFYING, ResearchState.FAILED, ResearchState.CANCELLED}
    ),
    ResearchState.VERIFYING: frozenset(
        {
            ResearchState.REPORTING,
            ResearchState.ANALYZING,
            ResearchState.FAILED,
            ResearchState.CANCELLED,
        }
    ),
    ResearchState.REPORTING: frozenset(
        {ResearchState.COMPLETED, ResearchState.VERIFYING, ResearchState.FAILED, ResearchState.CANCELLED}
    ),
    ResearchState.COMPLETED: frozenset({ResearchState.RESEARCHING}),  # new iteration
    ResearchState.FAILED: frozenset({ResearchState.PLANNING}),
    ResearchState.CANCELLED: frozenset({ResearchState.PLANNING}),
}

VALID_CLAIM_STATUSES: frozenset[str] = frozenset(
    {"supported", "partially_supported", "contradicted", "unresolved", "rejected"}
)


class InvalidResearchStateTransitionError(ValueError):
    """Raised when an invalid transition between ResearchStates is attempted."""


def validate_research_state_transition(
    current_state: str | ResearchState,
    target_state: str | ResearchState,
) -> ResearchState:
    cur = ResearchState(current_state)
    tgt = ResearchState(target_state)
    if cur == tgt:
        return tgt
    if tgt not in ALLOWED_RESEARCH_TRANSITIONS[cur]:
        raise InvalidResearchStateTransitionError(
            f"Invalid research state transition: {cur.value} -> {tgt.value}."
        )
    return tgt


def evaluate_claim_evidence_status(
    evidence_items: list[dict[str, Any]],
    *,
    explicit_rejection: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Derive claim status strictly from linked empirical evidence (never LLM self-confidence)."""
    if explicit_rejection:
        return "rejected", {
            "empirical_coverage_ratio": 0.0,
            "supporting_count": 0,
            "contradicting_count": 0,
            "calibration_notice": "Status set to rejected; LLM self-confidence is never treated as calibrated probability.",
        }

    supporting = [e for e in evidence_items if e.get("evidence_type") == "supporting"]
    contradicting = [
        e for e in evidence_items if e.get("evidence_type") == "contradicting"
    ]

    if not supporting and not contradicting:
        status = "unresolved"
        coverage = 0.0
    elif contradicting and not supporting:
        status = "contradicted"
        coverage = 1.0
    elif contradicting and supporting:
        status = "contradicted"
        coverage = round(
            len(supporting) / float(len(supporting) + len(contradicting)), 3
        )
    elif len(supporting) >= 1 and max(float(s.get("relevance", 0.0)) for s in supporting) >= 0.75:
        status = "supported"
        coverage = round(
            sum(float(s.get("relevance", 0.8)) for s in supporting) / len(supporting),
            3,
        )
    else:
        status = "partially_supported"
        coverage = round(
            sum(float(s.get("relevance", 0.5)) for s in supporting)
            / max(1, len(supporting)),
            3,
        )

    return status, {
        "empirical_coverage_ratio": coverage,
        "supporting_count": len(supporting),
        "contradicting_count": len(contradicting),
        "distinct_sources": len({e.get("source_id") for e in evidence_items if e.get("source_id")}),
        "calibration_notice": (
            "Empirical evidence linkage metric — NOT calibrated statistical probability "
            "and never derived from uncalibrated LLM self-confidence."
        ),
    }


def build_evidence_graph_topology(
    *,
    claims: list[dict[str, Any]],
    evidence_list: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construct graph nodes, edges, contradiction list, and missing-evidence alerts."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    source_map = {s["id"]: s for s in sources}
    used_source_ids: set[str] = set()

    missing_evidence_alerts: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []

    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for ev in evidence_list:
        evidence_by_claim.setdefault(ev["claim_id"], []).append(ev)

    for claim in claims:
        cid = claim["id"]
        nodes.append(
            {
                "id": cid,
                "kind": "claim",
                "label": claim["statement"][:96],
                "full_text": claim["statement"],
                "status": claim["status"],
                "coverage": claim.get("confidence_assessment", {}).get(
                    "empirical_coverage_ratio", 0.0
                ),
            }
        )
        linked = evidence_by_claim.get(cid, [])
        if not linked:
            missing_evidence_alerts.append(
                {
                    "claim_id": cid,
                    "statement": claim["statement"],
                    "severity": "high",
                    "reason": "Twierdzenie nie posiada żadnego przypisanego dowodu źródłowego (unresolved).",
                }
            )
        for ev in linked:
            sid = ev["source_id"]
            used_source_ids.add(sid)
            edges.append(
                {
                    "id": ev["id"],
                    "from": sid,
                    "to": cid,
                    "evidence_type": ev["evidence_type"],
                    "relevance": ev["relevance"],
                    "source_location": ev["source_location"],
                    "supporting_text": ev["supporting_text"],
                }
            )
            if ev["evidence_type"] == "contradicting":
                src = source_map.get(sid, {})
                contradictions.append(
                    {
                        "evidence_id": ev["id"],
                        "claim_id": cid,
                        "claim_statement": claim["statement"],
                        "source_id": sid,
                        "source_title": src.get("title", sid),
                        "supporting_text": ev["supporting_text"],
                    }
                )

    for sid in used_source_ids:
        src = source_map.get(sid)
        if src:
            nodes.append(
                {
                    "id": sid,
                    "kind": "source",
                    "label": src["title"][:80],
                    "full_text": src["title"],
                    "source_type": src["source_type"],
                    "verification_status": src["verification_status"],
                }
            )

    supported_count = sum(1 for c in claims if c["status"] == "supported")
    coverage_pct = (
        round(
            100.0
            * sum(1 for c in claims if evidence_by_claim.get(c["id"]))
            / len(claims),
            1,
        )
        if claims
        else 0.0
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "contradictions": contradictions,
        "missing_evidence_alerts": missing_evidence_alerts,
        "summary": {
            "total_claims": len(claims),
            "supported_claims": supported_count,
            "contradicted_claims": sum(1 for c in claims if c["status"] == "contradicted"),
            "unresolved_claims": sum(1 for c in claims if c["status"] == "unresolved"),
            "evidence_coverage_pct": coverage_pct,
        },
    }
