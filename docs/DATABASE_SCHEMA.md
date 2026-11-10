# Schemat Bazy Danych PostgreSQL i RLS (`DATABASE_SCHEMA.md`)

## 1. Łańcuch Migracji Alembic

- `migrations/versions/0001_apex_one_initial_schema.py` — rdzeń wielodostępny (`tenants`, `research_projects`, `workflow_runs`, `outbox_jobs`, `knowledge_chunks` z `VECTOR(1536)` HNSW).
- `migrations/versions/0002_apex_one_complete_platform.py` — pełny schemat 7 domen platformy APEX ONE 1.0 (łącznie **57 tabel SQLAlchemy**).

Weryfikacja kompilacji SQL offline:
```bash
.venv/bin/alembic upgrade head --sql
```

## 2. Tabele Domenowe (Part VI)

### Domena 1 — Identity & Access
`users`, `organizations`, `roles`, `permissions`, `memberships`, `invitations`, `sessions`, `devices`, `api_credentials`.

### Domena 2 — Research & Evidence
`research_projects` (z walidowaną kolumną `state` i `workspace_scope`), `research_questions`, `research_briefs`, `research_plans`, `hypotheses`, `sources` (indeks SHA-256), `source_snapshots`, `claims`, `evidence`, `research_reports`.

### Domena 3 — Agents & Orchestration
`agents`, `agent_versions` (`UNIQUE(agent_id, version)` — historia `AgentDNA`), `agent_capabilities`, `agent_teams`, `agent_runs`, `agent_steps`, `tool_definitions`, `tool_invocations` (`UNIQUE(idempotency_key)`).

### Domena 4 — Experiments & Datasets
`datasets`, `dataset_versions` (`UNIQUE(dataset_id, version)` + `checksum_sha256`), `experiments`, `experiment_runs`, `experiment_metrics`, `artifacts`.

### Domena 5 — Shared Memory & Vectors
`conversations`, `messages`, `documents`, `memory_sources`, `memories` (7 typów pamięci), `memory_chunks` (`embedding VECTOR(1536)` + indeks HNSW `vector_cosine_ops`).

### Domena 6 — Evaluation & Benchmarks
`evaluation_suites`, `evaluation_cases`, `evaluation_runs`, `evaluation_scores` (automatyczne oraz eksperckie `is_human_review`), `agent_regressions`.

### Domena 7 — Workflows, Governance & Audit
`workflows`, `approvals` (`parameters_sha256`), `audit_events` (chronione wyzwalaczem `trg_audit_events_immutable` blokującym `UPDATE` i `DELETE`), `budget_limits`, `usage_records`, `model_providers`, `extensions`.

## 3. Izolacja Wielodostępna Row-Level Security (RLS)

Wszystkie tabele domenowe posiadają włączone `ENABLE ROW LEVEL SECURITY` oraz `FORCE ROW LEVEL SECURITY` z polityką sprawdzającą zmienną sesyjną PostgreSQL:
- `current_setting('app.current_organization_id', true)::uuid` (tabele platformowe `0002`),
- `current_setting('app.current_tenant_id', true)::uuid` (tabele rdzeniowe `0001`).
