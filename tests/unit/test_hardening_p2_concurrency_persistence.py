"""HARDENING 1.0 — Stage 8 (P2) & Section 13: Concurrency, Persistence, Idempotency & Security Hardening Tests.

Covers all 8 mandatory Stage 8 verification scenarios + Section 13 checks:
1. Monotonic entity versioning & stale-write rejection (`StaleWriteConflictError`)
2. Multi-thread concurrent race with `expected_version=1` — exactly 1 winner, 0 lost updates
3. HTTP `If-Match` / `X-Expected-Version` / `expected_version` -> `409 STALE_WRITE_CONFLICT`
4. Atomic `transactional_org_mutation` rollback on mid-transaction failure (including audit chain)
5. HTTP `Idempotency-Key` exact replay (`X-Idempotent-Replay: true`) & payload-mismatch `409 IDEMPOTENCY_KEY_CONFLICT`
6. Concurrent atomic checkpoint writes (`pid + uuid` temp files, `fsync`, SHA-256 integrity envelope) & tamper rejection
7. Honest multi-instance state architecture reporting (`/api/v1/environment/status["persistence_and_concurrency"]`)
8. Section 13 hardening: path-traversal filename sanitization, 10 MiB upload cap, sanitized 500 error responses, non-root Dockerfiles & least-privilege GitHub Actions permissions
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.concurrency import StaleWriteConflictError
from apex_one.platform.workspace_service import ApexPlatformService


def _bootstrap_clean_service() -> tuple[ApexPlatformService, str, str]:
    svc = ApexPlatformService(seed_demo=False)
    svc.register_user(
        email="occ-tester@example.org",
        password="OccTesterStrongPassword!2026",
        display_name="OCC Tester",
        organization_name="OCC Lab",
    )
    login = svc.login(
        email="occ-tester@example.org", password="OccTesterStrongPassword!2026"
    )
    return svc, login["user"]["id"], login["active_organization_id"]


def test_01_entity_versioning_and_stale_write_rejection() -> None:
    """1. Mutable domain entities increment `version` monotonically and reject stale `expected_version`."""
    svc, uid, oid = _bootstrap_clean_service()
    proj = svc.create_research_project(
        user_id=uid,
        organization_id=oid,
        title="OCC State Machine Study",
        hypothesis="Optimistic concurrency prevents lost state transitions.",
    )
    assert proj["version"] == 1

    updated = svc.transition_research_project_state(
        user_id=uid,
        organization_id=oid,
        project_id=proj["id"],
        target_state="PLANNING",
        expected_version=1,
    )
    assert updated["version"] == 2
    assert updated["state"] == "PLANNING"

    # Reusing stale expected_version=1 MUST raise StaleWriteConflictError without mutating state!
    with pytest.raises(StaleWriteConflictError) as exc_info:
        svc.transition_research_project_state(
            user_id=uid,
            organization_id=oid,
            project_id=proj["id"],
            target_state="RESEARCHING",
            expected_version=1,
        )
    assert exc_info.value.expected_version == 1
    assert exc_info.value.actual_version == 2
    assert svc.projects[proj["id"]]["state"] == "PLANNING"


def test_02_concurrent_threads_race_on_same_expected_version_allows_single_winner() -> None:
    """2. 8 concurrent threads updating the same AgentDNA with `expected_version=1` produce 1 winner and 7 conflicts."""
    svc, uid, oid = _bootstrap_clean_service()
    agent = next(iter(svc.agents.values()))
    assert agent["current_version"] == 1

    def _compete(worker_idx: int) -> str:
        try:
            svc.update_agent_creates_new_version(
                user_id=uid,
                organization_id=oid,
                agent_id=agent["id"],
                updates={"description": f"Updated by worker {worker_idx}"},
                expected_version=1,
            )
            return "WON"
        except StaleWriteConflictError:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(_compete, range(8)))

    assert outcomes.count("WON") == 1
    assert outcomes.count("CONFLICT") == 7
    assert svc.agents[agent["id"]]["current_version"] == 2


def test_03_http_if_match_and_expected_version_return_409_on_stale_write() -> None:
    """3. HTTP `If-Match` / `X-Expected-Version` headers enforce OCC and return `409 STALE_WRITE_CONFLICT`."""
    settings = Settings(env="test", enable_demo_seed=False)
    app = create_app(settings=settings, seed_demo=False)

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "http-occ@example.org",
                "password": "HttpOccStrongPassword!2026",
                "display_name": "HTTP OCC",
                "organization_name": "HTTP OCC Org",
            },
        )
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "http-occ@example.org",
                "password": "HttpOccStrongPassword!2026",
            },
        ).json()
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
            "X-CSRF-Token": login["csrf_token"],
            "X-Organization-Id": login["active_organization_id"],
        }

        proj = client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={"title": "HTTP If-Match Test", "hypothesis": "H1"},
        ).json()

        ok_resp = client.post(
            f"/api/v1/research/projects/{proj['id']}/transition",
            headers={**headers, "If-Match": 'W/"1"'},
            json={"target_state": "PLANNING"},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["version"] == 2

        stale_resp = client.post(
            f"/api/v1/research/projects/{proj['id']}/transition",
            headers={**headers, "If-Match": 'W/"1"'},
            json={"target_state": "RESEARCHING"},
        )
        assert stale_resp.status_code == 409
        assert stale_resp.json()["detail"]["code"] == "STALE_WRITE_CONFLICT"


def test_04_transactional_org_mutation_rolls_back_partial_writes_and_audit_log() -> None:
    """4. `transactional_org_mutation` restores collections and audit chain if an error occurs mid-transaction."""
    svc, uid, oid = _bootstrap_clean_service()
    before_projects = len(svc.projects)
    before_audit_len = len(svc.audit.get_chronological_entries(oid))

    with pytest.raises(RuntimeError, match="simulated mid-tx failure"):
        with svc.transactional_org_mutation(oid):
            svc.create_research_project(
                user_id=uid,
                organization_id=oid,
                title="Should Roll Back Completely",
                hypothesis="Temporary",
            )
            assert len(svc.projects) == before_projects + 1
            raise RuntimeError("simulated mid-tx failure")

    assert len(svc.projects) == before_projects
    assert len(svc.audit.get_chronological_entries(oid)) == before_audit_len
    assert svc.audit.verify_chain(oid)["valid"] is True


def test_05_http_idempotency_key_replays_exact_match_and_rejects_payload_mismatch() -> None:
    """5. `Idempotency-Key` prevents duplicate creation on retry and rejects payload reuse with `409`."""
    settings = Settings(env="test", enable_demo_seed=False)
    app = create_app(settings=settings, seed_demo=False)

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "idem-user@example.org",
                "password": "IdemUserStrongPassword!2026",
                "display_name": "Idem User",
                "organization_name": "Idem Org",
            },
        )
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "idem-user@example.org",
                "password": "IdemUserStrongPassword!2026",
            },
        ).json()
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
            "X-CSRF-Token": login["csrf_token"],
            "X-Organization-Id": login["active_organization_id"],
            "Idempotency-Key": "idem-create-proj-001",
        }

        first = client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={"title": "Idempotent Project Alpha", "hypothesis": "Exact once"},
        )
        assert first.status_code == 201
        assert first.headers.get("X-Idempotent-Replay") == "false"

        replay = client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={"title": "Idempotent Project Alpha", "hypothesis": "Exact once"},
        )
        assert replay.status_code == 201
        assert replay.headers.get("X-Idempotent-Replay") == "true"
        assert replay.json()["id"] == first.json()["id"]

        # Confirm only ONE project exists in the organization!
        listed = client.get("/api/v1/research/projects", headers=headers).json()
        assert listed["total"] == 1

        # Reusing the same Idempotency-Key with a DIFFERENT payload MUST fail with 409!
        conflict = client.post(
            "/api/v1/research/projects",
            headers=headers,
            json={"title": "Different Payload Attack", "hypothesis": "Tampered"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_06_concurrent_atomic_checkpoint_writes_and_corruption_detection(
    tmp_path: Path,
) -> None:
    """6. Concurrent `save_local_checkpoint` calls leave zero temp files and detect tampered JSON envelopes."""
    svc, uid, oid = _bootstrap_clean_service()
    svc.create_research_project(
        user_id=uid,
        organization_id=oid,
        title="Durable Checkpoint Study",
        hypothesis="Atomic replace + fsync + SHA-256",
    )
    ckpt_file = tmp_path / "apex_state.json"

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: svc.save_local_checkpoint(ckpt_file), range(12)))

    # Zero leftover `.tmp` files in directory
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []

    restored = ApexPlatformService(seed_demo=False)
    assert restored.load_local_checkpoint(ckpt_file) is True
    assert len(restored.projects) == 1

    # Tamper with inner data without updating `_checkpoint_sha256` -> rejected cleanly!
    envelope = json.loads(ckpt_file.read_text(encoding="utf-8"))
    envelope["data"]["default_org_id"] = "tampered-org-id"
    ckpt_file.write_text(json.dumps(envelope), encoding="utf-8")

    untouched = ApexPlatformService(seed_demo=False)
    assert untouched.load_local_checkpoint(ckpt_file) is False
    assert untouched.default_org_id == ""


def test_07_environment_status_honestly_reports_single_vs_multi_instance_persistence() -> None:
    """7. `/api/v1/environment/status` honestly reports single-instance checkpoint vs PostgreSQL multi-instance OCC."""
    settings = Settings(env="test", enable_demo_seed=False, database_url=None)
    app = create_app(settings=settings, seed_demo=False)

    with TestClient(app) as client:
        status = client.get("/api/v1/environment/status").json()
        pc = status["persistence_and_concurrency"]
        assert pc["authoritative_store"] == "single_instance_local_checkpoint"
        assert pc["multi_instance_safe"] is False
        assert pc["optimistic_concurrency_control"] is True
        assert pc["idempotency_ledger_enabled"] is True
        assert pc["atomic_fsync_checkpointing"] is True


def test_08_section_13_path_traversal_upload_cap_error_redaction_and_infra_manifests() -> None:
    """8. Verifies filename traversal sanitization, 10 MiB cap, sanitized 500 errors, non-root Dockerfiles & CI permissions."""
    settings = Settings(env="test", enable_demo_seed=False)
    app = create_app(settings=settings, seed_demo=False)

    @app.get("/api/v1/_test_internal_crash")
    async def _crash() -> dict[str, str]:
        raise RuntimeError(
            "Sensitive internal path /srv/secrets/db_master.pem exploded"
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        # Unhandled 500 error MUST NOT leak stack traces, exception message, or file paths!
        err_resp = client.get("/api/v1/_test_internal_crash")
        assert err_resp.status_code == 500
        raw_err = err_resp.text
        assert "/srv/secrets" not in raw_err
        assert "Traceback" not in raw_err
        assert err_resp.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"

        # Path traversal filename sanitization & 10 MiB cap on `/api/v1/sources`:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "sec13@example.org",
                "password": "Sec13StrongPassword!2026",
                "display_name": "Sec 13",
                "organization_name": "Sec 13 Org",
            },
        )
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "sec13@example.org",
                "password": "Sec13StrongPassword!2026",
            },
        ).json()
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
            "X-CSRF-Token": login["csrf_token"],
            "X-Organization-Id": login["active_organization_id"],
        }
        src_resp = client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "source_type": "TXT",
                "title": "Traversal Attempt",
                "uri_or_filename": "../../../../etc/shadow\x00.txt",
                "raw_text": "Clean empirical note.",
            },
        )
        assert src_resp.status_code == 201
        saved_name = src_resp.json()["uri_or_filename"]
        assert ".." not in saved_name
        assert "/" not in saved_name
        assert "\x00" not in saved_name
        assert saved_name == "shadow.txt"

        # Oversized >10 MiB upload rejected with 400
        huge_resp = client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "source_type": "TXT",
                "title": "Oversized Upload",
                "uri_or_filename": "huge.txt",
                "raw_text": "A" * (10 * 1024 * 1024 + 128),
            },
        )
        assert huge_resp.status_code == 400

    # Verify non-root Dockerfiles & gated migrations & least-privilege GitHub Actions permissions:
    repo_root = Path(__file__).resolve().parents[2]
    df_api = (repo_root / "infra/docker/Dockerfile.api").read_text(encoding="utf-8")
    df_worker = (repo_root / "infra/docker/Dockerfile.worker").read_text(
        encoding="utf-8"
    )
    df_web = (repo_root / "infra/docker/Dockerfile.web").read_text(encoding="utf-8")
    ci_yml = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "USER 10001:10001" in df_api
    assert 'APEX_RUN_MIGRATIONS_ON_START' in df_api
    assert "USER 10001:10001" in df_worker
    assert "USER node" in df_web
    assert "permissions:\n  contents: read" in ci_yml
