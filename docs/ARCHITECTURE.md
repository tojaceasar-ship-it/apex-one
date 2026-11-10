# Architektura Systemu APEX ONE 1.0 (`ARCHITECTURE.md`)

## 1. Topologia Warstwowa

```text
┌────────────────────────────────────────────────────────────────────────────┐
│  APEX WORKBENCH (apps/web — React 19, TypeScript 5.9, Tailwind v4, Vite 6) │
│  19 sekcji nawigacyjnych · 23 widoki · PL (domyślny) / EN · SSE Client     │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │ HTTPS / REST / SSE / WebSocket (/api/v1)
┌───────────────────────────────────▼────────────────────────────────────────┐
│  APEX CORE & API (backend/apex_one — FastAPI, Pydantic v2, Argon2id, RBAC) │
│  - Platform Service: Research, Evidence, Agents, Memory, Eval, Governance  │
│  - DurableCheckpointRuntimeAdapter (ADR 0001) + Tool Idempotency Ledger    │
│  - Guarded ExperimentSandboxAdapter + Hybrid Local Node Gateway            │
└───────────┬───────────────────────┬──────────────────────────┬─────────────┘
            │                       │                          │
┌───────────▼───────────┐ ┌─────────▼──────────┐ ┌─────────────▼─────────────┐
│ PostgreSQL 16         │ │ Opcjonalny Redis   │ │ APEX HYBRID LOCAL NODE    │
│ + pgvector (1536-d)   │ │ Cache / Rate Limit │ │ (local_node/ Python CLI)  │
│ + Row-Level Security  │ │ (Graceful Fallback)│ │ Osobne prawa R / W / X    │
│ + Transactional Outbox│ └────────────────────┘ └───────────────────────────┘
└───────────────────────┘
```

## 2. Trwałość Stanu i Wzorzec Transactional Outbox

- Docelową produkcyjną bazą danych jest wyłącznie **PostgreSQL 16+ z rozszerzeniem `pgvector` oraz Row-Level Security (RLS)** (nigdy SQLite ani pliki JSON).
- W środowisku chmurowym Arena (pozbawionym lokalnego demona PostgreSQL oraz zewnętrznego `DATABASE_URL`) warstwa deweloperska uruchamia wielodostępny kontener pamięciowy z identycznymi regułami autoryzacji `organization_id`, podczas gdy pełny łańcuch migracji Alembic (`0001` + `0002`) generuje kompletny schemat PostgreSQL + RLS + wyzwalacze `audit_events`.
- Zadania w tle wykorzystują tabelę `outbox_jobs` z blokadami `SELECT ... FOR UPDATE SKIP LOCKED`, wykładniczym backoffem, kluczami idempotencji i odzyskiwaniem wygasłych dzierżaw (`stale lease recovery`).

## 3. Abstrakcja Runtime'u Agentów (`AgentRuntimeAdapter`)

Zgodnie z [ADR 0001](adr/0001-agent-runtime-selection.md) każdy przebieg wieloagentowy zapisuje punkty kontrolne (`checkpoint_json`) oraz rejestr wywołań narzędzi (`tool_ledger`) niezależnie od kontekstu konwersacyjnego modelu, gwarantując bezpieczne wznawianie po awarii bez duplikowania efektów ubocznych.
