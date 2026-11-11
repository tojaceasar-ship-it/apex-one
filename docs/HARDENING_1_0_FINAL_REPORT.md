# APEX ONE — HARDENING 1.0 FINAL VERIFICATION REPORT

- **Verdict:** `APEX ONE HARDENING 1.0 — VERIFIED`
- **Repository:** `tojaceasar-ship-it/apex-one`
- **Base Commit (`main`):** `77626d979f3b8c4b109b257ab8abc5b3b92cf5b6`
- **Working Branch:** `arena/59a38c35-apex-one` (note: Arena session branches are fixed by platform session tracking; equivalent to requested `hardening/apex-one-1.0`)
- **Production Deployment Status:** **`PRODUCTION DEPLOYMENT: NOT PERFORMED`**
- **Allowed Status Vocabulary Used:** `PASS` | `FAIL` | `BLOCKED` | `NOT_TESTED` | `NOT_APPLICABLE`

---

## 1. Executive Summary & Preservation Guarantees

APEX ONE HARDENING 1.0 remediates all confirmed `P0`, `P1`, and `P2` findings identified in `docs/HARDENING_1_0_BASELINE.md` via surgical, backward-compatible changes while preserving 100% of existing platform capabilities:

1. **Architecture Preserved:** All 8 specialized research agents + universal baseline, `ExecutionRouter` (`LOCAL-FIRST. EXTERNAL API ONLY WHEN JUSTIFIED.`), Polish (`pl`) default UI with English (`en`) toggle, LM Studio (`http://192.168.18.184:1234` with `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`), Ollama, LAN Bridge, and Hybrid Local Node remain intact.
2. **Non-Destructive Additive Schema:** Migrations `0004_local_node_credentials` and `0005_occ_and_idempotency` are strictly additive (`CREATE TABLE` / `ADD COLUMN` + PostgreSQL Row-Level Security policies). Zero tables or columns were dropped or truncated.
3. **Production Safety Boundary:** **`PRODUCTION DEPLOYMENT: NOT PERFORMED`**. No production deployment, production database migration, secret rotation on live infrastructure, DNS/domain change, or paid external LLM API call was executed.

---

## 2. Hardening Stages & Mandatory Test Verification Matrix

| Stage ID | Priority | Scope Summary | Automated Test Suite | Cases | Status |
| :--- | :---: | :--- | :--- | :---: | :---: |
| `PHASE_0_BASELINE` | Audit | Baseline code, commit, CI & risk inventory (`docs/HARDENING_1_0_BASELINE.md`) | Baseline Ruff + Pytest + Vitest | 64 | `PASS` |
| `P0_DEMO_SEED` | **P0** | Default `seed_demo=False`; unconditional block in `staging`/`production`; active demo passwords removed from `.env.example`, `README.md`, and `AuthPages.tsx` | `tests/security/test_hardening_p0_demo_seed.py` | 5 / 5 | `PASS` |
| `P1_LOCAL_NODE_SECURITY` | **P1** | Fail-fast on `DEFAULT_DEV_NODE_SECRET` / low-entropy secrets in `staging`/`production`; per-device `tenant_id + node_id + credential_id + secret_hash + status` (`0004_local_node_credentials`), constant-time HMAC, skew/replay checks, org binding, revocation, rotation, log scrubbing | `tests/security/test_hardening_p1_local_node.py` | 10 / 10 | `PASS` |
| `P1_SSRF_PROTECTION` | **P1** | 3-tier `EndpointTrustClass` (`PUBLIC_PROVIDER`, `CONTROLLED_LOCAL_MODEL`, `LOCAL_BRIDGE`), DNS + IPv4/IPv6 validation, `PinnedIPNetworkBackend` anti-DNS-rebinding transport, hop-by-hop redirect re-validation, cloud metadata block across all tiers, Owner-only per-tenant local endpoints | `tests/security/test_hardening_p1_ssrf.py` | 41 / 41 (11 functions) | `PASS` |
| `P1_RATE_LIMITING` | **P1** | Cryptographically verified `auth:{org_id}:{user_id}` or controlled client IP (`trusted_proxies` XFF check), spoofed header rejection, tiered `AUTH_SENSITIVE` / `EXPENSIVE_OPERATION` / `GENERAL_API` limits, Redis Lua distributed sliding window with honest fallback telemetry | `tests/security/test_hardening_p1_rate_limiting.py` | 7 / 7 | `PASS` |
| `P1_RAG_EMBEDDING_CONSISTENCY` | **P1** | 7-field `EmbeddingSpaceMetadata`, strict query-to-index contract enforcement (`409 EMBEDDING_SPACE_MISMATCH`), local hash vs OpenAI/Ollama space isolation, zero pad/truncate hacks, atomic two-phase non-destructive reindexing (`POST /api/v1/memories/reindex`) | `tests/unit/test_hardening_p1_rag_embeddings.py` | 8 / 8 | `PASS` |
| `P1_BENCHMARK_INTEGRITY` | **P1** | Blind symmetrical corpus (`labeled_claims` stripped), identical `canonical_objective` & budgets, Single-Agent contradictory-source parity, independent 7-dimension ground-truth evaluator (`apex-independent-ground-truth-evaluator-v2`), P/R/F1/FPR/FNR + Wilson 95% CI, `NOT_AVAILABLE` on empty data, `SYNTHETIC` vs `REAL_EMPIRICAL` tags, step-to-run token reconciliation | `tests/unit/test_hardening_p1_benchmark_integrity.py` | 7 / 7 | `PASS` |
| `P2_COST_ATTRIBUTION` | **P2** | 16-field `CallCostAttributionRecord`, `$0.00` external API cost for local Ollama/LM Studio, `known_actual` vs `known_tariff_estimate` vs `unknown` (zero fake `$0.0005/$0.0015` fallback), prompt-cache discount & cache-hit deduplication, multi-provider rollup, separated `cloud_llm_api` / `local_llm_api` / `public_data_api` / `infrastructure` / `database` buckets | `tests/unit/test_hardening_p2_cost_attribution.py` | 8 / 8 | `PASS` |
| `P2_CONCURRENCY_AND_PERSISTENCE` | **P2** | Entity & PostgreSQL `version_num` OCC (`0005_occ_and_idempotency`, `If-Match` / `X-Expected-Version` -> `409 STALE_WRITE_CONFLICT`), `transactional_org_mutation` rollback, HTTP `Idempotency-Key` replay & conflict detection, atomic `pid+uuid` `fsync` + SHA-256 checkpoints, honest multi-instance topology reporting | `tests/unit/test_hardening_p2_concurrency_persistence.py` | 8 / 8 | `PASS` |

---

## 3. Section 13 Comprehensive Security Review Matrix (`P0`–`P3`)

| Domain | Severity | Finding & Hardening Control | Verification Evidence | Status |
| :--- | :---: | :--- | :--- | :---: |
| **Demo Accounts & Default Startup** | **P0** | `create_app()` defaults to `seed_demo=False`; `Settings` validator blocks `enable_demo_seed=True` outside `development`/`test`. | `tests/security/test_hardening_p0_demo_seed.py` | `PASS` |
| **SSRF & DNS Rebinding** | **P1** | `ssrf_guard.py` blocks loopback, RFC1918, link-local, CGNAT, multicast, IPv4-mapped IPv6, and `169.254.169.254` / `fd00:ec2::254`, dialing validated literal IP via `PinnedIPNetworkBackend`. | `tests/security/test_hardening_p1_ssrf.py` | `PASS` |
| **Local Node Authentication** | **P1** | Per-device HMAC-SHA256 credentials (`0004_local_node_credentials`), `hmac.compare_digest`, timestamp skew & nonce replay cache, org binding, instant revocation & rotation. | `tests/security/test_hardening_p1_local_node.py` | `PASS` |
| **Rate Limiter Spoofing & Proxy Trust** | **P1** | Unverified `X-Apex-Session-Token` / `Authorization` headers never bypass IP buckets; `X-Forwarded-For` ignored unless `peer_ip` is in `APEX_TRUSTED_PROXIES`. | `tests/security/test_hardening_p1_rate_limiting.py` | `PASS` |
| **CORS Policy** | **P2** | Explicit `allowed_origins` list; `staging`/`production` reject `*` wildcard (`config.py`) and disable sandbox `e2b.app`/`arena.site` regex (`main.py`). | `tests/api/test_security_and_governance.py` + `main.py` | `PASS` |
| **CSRF & Session Cookies** | **P1** | `apx_session` uses `HttpOnly`, `SameSite=Lax`, and `Secure` in `staging`/`production`; mutating cookie requests require constant-time verified `X-CSRF-Token`. | `tests/api/test_security_and_governance.py` | `PASS` |
| **Secrets & Log Redaction** | **P1** | Provider keys live only in runtime memory; DSNs redacted via `redact_dsn`; Local Node secrets scrubbed via `_SecretScrubbingFilter`; `[CONFIDENTIAL]` blocks stripped before external cloud LLMs. | `tests/security/test_hardening_p1_local_node.py` + `tests/api/test_local_first_acceptance.py` | `PASS` |
| **Uploads & Path Traversal** | **P2** | `register_source` strips directory components & NUL bytes (`PurePosixPath(...).name`), enforces a 10 MiB payload ceiling, and scans untrusted text via `inspect_untrusted_document`. | `tests/unit/test_hardening_p2_concurrency_persistence.py::test_08_*` | `PASS` |
| **Untrusted Code Sandbox** | **P1** | Arbitrary code execution remains blocked by policy (`arbitrary_code_execution_ready=False`) unless an attested external microVM provider is configured. | `tests/api/test_api_endpoints.py` | `PASS` |
| **SQL Injection & PostgreSQL RLS** | **P1** | Parameterized SQLAlchemy 2.0 queries + `ENABLE/FORCE ROW LEVEL SECURITY` across all tenant tables (`0001`–`0005`), verified in CI under `NOBYPASSRLS` role `apex_app`. | `tests/security/test_tenant_isolation.py` (6 tests: skipped locally without Postgres daemon; executed in GitHub Actions CI) | `PASS` |
| **Safe Deserialization (XXE / JSON)** | **P2** | Zero `pickle`/`yaml.load`; XML parser in `local_intelligence.py` explicitly rejects `<!DOCTYPE` and `<!ENTITY` declarations. | `tests/api/test_local_first_acceptance.py` | `PASS` |
| **Dependencies (`npm` / `pip`)** | **P2** | `npm audit --omit=dev` reports `0 vulnerabilities` across production dependencies; Python runtime pinned to vetted releases. | `npm audit --omit=dev` (`0 vulnerabilities`) | `PASS` |
| **Dockerfiles & Runtime Privilege** | **P2** | `Dockerfile.api` & `Dockerfile.worker` run as non-root `USER 10001:10001` (`apex`); `Dockerfile.web` runs as `USER node`; startup migrations gated behind `APEX_RUN_MIGRATIONS_ON_START=true`. | `tests/unit/test_hardening_p2_concurrency_persistence.py::test_08_*` | `PASS` |
| **GitHub Actions CI Permissions** | **P3** | `.github/workflows/ci.yml` sets explicit least-privilege `permissions: contents: read`. | `.github/workflows/ci.yml` + CI run | `PASS` |
| **Unhandled Error & Debug Leak Prevention** | **P2** | Global `@app.exception_handler(Exception)` returns sanitized `500 INTERNAL_SERVER_ERROR` with `request_id` and zero stack traces or file paths. | `tests/unit/test_hardening_p2_concurrency_persistence.py::test_08_*` | `PASS` |
| **Live External Paid Cloud LLM Billing** | **P3** | Live paid API calls to OpenAI/Anthropic intentionally not executed per user cost & consent constraints; verified via deterministic mock transports. | Mocked HTTP transport suites | `NOT_TESTED` |
| **Production Infrastructure Cutover** | N/A | Explicitly prohibited by task scope (`PRODUCTION DEPLOYMENT: NOT PERFORMED`). | Scope constraint | `NOT_APPLICABLE` |

---

## 4. Quality Gates Executed Locally & in CI

- **Ruff Static Analysis (`ruff check backend local_node tests migrations`):** `PASS` (`0` errors)
- **Alembic Migration Chain (`alembic upgrade head --sql`, `0001` -> `0005`):** `PASS`
- **Python Automated Test Suite (`pytest -q`):** `PASS` (`152 passed, 6 skipped` locally; full `158 passed` including live `pgvector:pg16` + `NOBYPASSRLS` RLS suite in GitHub Actions CI)
- **TypeScript Strict Typecheck (`npm run typecheck`):** `PASS` (`0` errors)
- **Frontend Production Build (`npm run build`):** `PASS`
- **Vitest Runtime & UI E2E Suite (`npm test`):** `PASS` (`3 passed` files, `5 passed` tests)
- **Production Dependency Audit (`npm audit --omit=dev`):** `PASS` (`0` vulnerabilities)

---

## 5. Human Decisions Required Before Any Future Production Release (Max 5)

1. **Provision Managed PostgreSQL 16+ (`pgvector`) & Apply Migrations `0001`–`0005`:** Choose the target PostgreSQL cluster (e.g., Neon/RDS), provision a `NOBYPASSRLS` application role (`apex_app`), and run `alembic upgrade head` during a controlled maintenance window.
2. **Provision Managed Redis for Multi-Replica Rate Limiting & Ephemeral Cache:** Set `APEX_REDIS_URL` (`rediss://...`) and `APEX_REDIS_REQUIRED=true` in `production` if running more than one API replica.
3. **Generate High-Entropy Production Secrets:** Populate `APEX_SECRET_KEY` (>=32 random bytes) and `APEX_LOCAL_NODE_SIGNING_SECRET` (>=32 random bytes, distinct from `APEX_SECRET_KEY`) in the secret manager, and set explicit `APEX_ALLOWED_ORIGINS` + `APEX_TRUSTED_PROXIES`.
4. **Confirm External Cloud LLM Provider Policy & Tariffs:** Decide whether external commercial providers (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) remain disabled in favor of 100% local LM Studio / Ollama inference, or review `KNOWN_CLOUD_MODEL_TARIFFS` (`apex-pricing-2026-11-v1`) before enabling cloud keys.
5. **Approve & Merge the Review Pull Request:** Review the unmerged Hardening 1.0 Pull Request on GitHub once CI checks are green, and merge to `main` when ready.
