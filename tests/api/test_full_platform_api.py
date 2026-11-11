"""End-to-end API integration tests across all 18 APEX ONE modules (Part VIII.2)."""

from __future__ import annotations

import httpx
import pytest

from apex_one.api.main import create_app
from apex_one.config import Settings

pytestmark = pytest.mark.api


async def test_seeded_comparative_demo_and_full_research_lifecycle() -> None:
    app = create_app(Settings(env="development"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login as seeded Owner
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@apex-one.local", "password": "ApexResearch!2026"},
        )
        assert login_res.status_code == 200
        assert "X-Request-ID" in login_res.headers
        token = login_res.json()["access_token"]
        org_id = login_res.json()["active_organization_id"]
        headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": org_id}

        # 2. Verify seeded Part IX Comparative Evaluation (Single-Agent vs Multi-Agent)
        evals_res = await client.get("/api/v1/evaluations?scope=demo", headers=headers)
        assert evals_res.status_code == 200
        eval_runs = evals_res.json()["runs"]
        assert len(eval_runs) >= 2
        single_eval = next(r for r in eval_runs if r["subject_type"] == "single_agent")
        multi_eval = next(r for r in eval_runs if r["subject_type"] == "multi_agent_team")
        # Genuine empirical trade-off: Multi-agent finds contradictions -> higher research_accuracy,
        # while Single-agent uses fewer tokens -> higher cost_efficiency!
        assert (
            multi_eval["summary_scores"]["research_accuracy"]
            > single_eval["summary_scores"]["research_accuracy"]
        )
        assert (
            single_eval["summary_scores"]["cost_efficiency"]
            > multi_eval["summary_scores"]["cost_efficiency"]
        )

        # 3. Verify Demo vs Live Research workspace separation
        live_projects_before = await client.get(
            "/api/v1/research/projects?scope=research", headers=headers
        )
        assert live_projects_before.json()["total"] == 0

        # 4. Create a new live Research Project, transition states, reject invalid state jump
        proj_res = await client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={
                "title": "Badanie Odporności Systemów Hybrydowych",
                "hypothesis": "Klucze idempotencji eliminują duplikację efektów ubocznych.",
                "scope_summary": "Badanie produkcyjne",
            },
        )
        assert proj_res.status_code == 201
        pid = proj_res.json()["id"]

        bad_jump = await client.post(
            f"/api/v1/research/projects/{pid}/transition",
            headers=headers,
            json={"target_state": "COMPLETED"},
        )
        assert bad_jump.status_code == 422

        ok_jump = await client.post(
            f"/api/v1/research/projects/{pid}/transition",
            headers=headers,
            json={"target_state": "PLANNING"},
        )
        assert ok_jump.status_code == 200
        assert ok_jump.json()["state"] == "PLANNING"

        # 5. Register source + verify SHA-256 duplicate detection + Memory hybrid search
        src1 = await client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "source_type": "MD",
                "title": "Artykuł o Idempotencji Outbox",
                "uri_or_filename": "outbox_study.md",
                "raw_text": "Transakcyjny wzorzec Outbox gwarantuje brak utraty zadań i pełną idempotencję.",
                "research_project_id": pid,
            },
        )
        assert src1.status_code == 201
        assert src1.json()["duplicate_detected"] is False

        src_dup = await client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "source_type": "MD",
                "title": "Duplikat Artykułu",
                "uri_or_filename": "copy.md",
                "raw_text": "Transakcyjny wzorzec Outbox gwarantuje brak utraty zadań i pełną idempotencję.",
                "research_project_id": pid,
            },
        )
        assert src_dup.json()["duplicate_detected"] is True

        mem_hits = await client.get(
            "/api/v1/memories/search?q=idempotencję+Outbox", headers=headers
        )
        assert mem_hits.status_code == 200
        assert len(mem_hits.json()["results"]) >= 1
        assert "#chunk-" in mem_hits.json()["results"][0]["source_citation"]

        # 6. AgentDNA versioning + version comparison + SSE run stream
        ag_res = await client.post(
            "/api/v1/agents",
            headers=headers,
            json={"name": "Specjalista Weryfikacji Protokołów", "max_steps": 6},
        )
        assert ag_res.status_code == 201
        ag_id = ag_res.json()["id"]

        ag_v2 = await client.put(
            f"/api/v1/agents/{ag_id}",
            headers=headers,
            json={
                "updates": {"max_steps": 12},
                "change_summary": "Zwiększono limit kroków",
            },
        )
        assert ag_v2.json()["current_version"] == 2

        diff_res = await client.get(
            f"/api/v1/agents/{ag_id}/compare?version_a=1&version_b=2", headers=headers
        )
        assert any(d["field"] == "max_steps" for d in diff_res.json()["differences"])

        run_res = await client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "objective": "Synteza dowodów o wzorcu Outbox",
                "execution_mode": "multi_agent_team",
                "research_project_id": pid,
            },
        )
        assert run_res.status_code == 201
        run_id = run_res.json()["id"]

        sse_res = await client.get(f"/api/v1/runs/{run_id}/stream", headers=headers)
        assert sse_res.status_code == 200
        assert "event: complete" in sse_res.text

        # 7. Export markdown report + reproduce experiment + human evaluation check
        md_export = await client.get(
            f"/api/v1/research/projects/{pid}/export", headers=headers
        )
        assert md_export.status_code == 200
        assert "Raport" in md_export.text

        exps = (await client.get("/api/v1/experiments", headers=headers)).json()["items"]
        first_exp_run_id = exps[0]["runs"][0]["id"]
        repro = await client.post(
            f"/api/v1/experiments/runs/{first_exp_run_id}/reproduce", headers=headers
        )
        assert repro.status_code == 201
        assert repro.json()["reproduction_verified"] is True

        human_rev = await client.post(
            f"/api/v1/evaluations/runs/{multi_eval['id']}/human-review",
            headers=headers,
            json={
                "category": "evidence_quality",
                "score": 0.95,
                "rationale": "Pełna zgodność cytowań ze źródłami pierwotnymi.",
            },
        )
        assert human_rev.status_code == 201
        assert human_rev.json()["production_agent_auto_updated"] is False

        # 8. Analytics CSV export & Extensions live health
        csv_res = await client.get("/api/v1/analytics/export.csv", headers=headers)
        assert csv_res.status_code == 200
        assert "total_tokens" in csv_res.text

        ext_res = await client.get("/api/v1/extensions", headers=headers)
        assert ext_res.status_code == 200
        assert len(ext_res.json()["items"]) >= 10
