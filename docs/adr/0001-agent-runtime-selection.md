# ADR 0001: Multi-Agent Runtime Selection & `AgentRuntimeAdapter` Abstraction

- **Status:** Accepted
- **Date:** 2026-11-10
- **Component:** `backend/apex_one/platform/orchestration.py` & `packages/agent-runtime/`

## Context

Part III.5 of the APEX ONE specification requires evaluating candidate multi-agent runtime libraries — specifically including **Eve** (`https://github.com/vercel/eve`) — against five engineering criteria before selecting the primary orchestration runtime, and wrapping execution behind a clean `AgentRuntimeAdapter` interface.

## Empirical Evaluation of `eve`

During Phase 1 repository verification, `eve` was inspected directly on the npm registry (`npm view eve --json`):

1. **Maturity & Stability:** `eve` is currently at **`1.0.0-rc.9`** (pre-1.0 Release Candidate), coupled to `nitro@3.0.261103-rc` and peer dependency `vite@^8.3.1`.
2. **Persistence Model:** `eve` is architected as a TypeScript/Nitro filesystem-first agent server rather than a Python SQLAlchemy 2.0 + PostgreSQL Row-Level Security (`organization_id`) + Transactional Outbox (`FOR UPDATE SKIP LOCKED`) engine.
3. **Security & Governance Integration:** APEX ONE requires R0–R3 parameter-bound SHA-256 approvals, organizational Emergency Stop interrupts, per-step idempotency ledgers (`idempotency_key`), and strict cross-tenant isolation enforced inside FastAPI + PostgreSQL.
4. **Hybrid Execution Compatibility:** APEX ONE coordinates cloud DAG runs alongside signed, capability-bounded Python Local Nodes (`apex-local-node`).

| Criterion | `eve@1.0.0-rc.9` | `DurableCheckpointRuntimeAdapter` (APEX ONE) |
| :--- | :--- | :--- |
| GA Stability | Pre-GA RC (`1.0.0-rc.9` + Nitro RC) | Stable Python 3.11 + TypeScript 5.9 contracts |
| Multi-Tenant PostgreSQL RLS | Not native (filesystem-centric) | Native `organization_id` & `tenant_id` isolation |
| Context-Independent Checkpoints | Partial | Full step & tool ledger persisted outside LLM context |
| Idempotent Recovery (No Duplicate Tools) | Requires custom wrapper | Built-in `tool_ledger` keyed by `run:id:step:k:tool:name` |
| R0–R3 Governance & Emergency Stop | External | Integrated pre-flight & step-boundary enforcement |

## Decision

1. Define the provider-neutral **`AgentRuntimeAdapter`** protocol (`backend/apex_one/platform/orchestration.py` and `@apex-one/agent-runtime`).
2. Select **`DurableCheckpointRuntimeAdapter` (`apex-durable-dag-v1`)** as the primary orchestration engine for APEX ONE 1.0.
3. Retain clean adapter boundaries so an external `EveRuntimeBridgeAdapter` can be plugged in once `eve` reaches post-1.0 GA stability if TypeScript-side worker delegation is desired.

## Consequences

- Multi-agent workflows persist step state and `tool_ledger` checkpoints independently of model conversation context.
- Interrupted 8-agent runs resume cleanly (`POST /api/v1/runs/{run_id}/resume`) with zero duplicate tool invocations, verified by `tests/unit/test_recovery_and_resumption.py`.
