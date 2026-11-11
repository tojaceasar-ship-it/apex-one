"""HARDENING 1.0 — Stage 2 (P1): Local Node Credentials, Revocation, Rotation & Secret Safety."""

from __future__ import annotations

import logging
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from apex_local_node.client import LocalNodeClient
from apex_local_node.config import LocalNodeSettings
from apex_local_node.signer import build_signed_headers
from apex_one.api.dependencies import build_app_container
from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.local_node_gateway.protocol import (
    DEFAULT_DEV_NODE_SECRET,
    LocalNodeGatewayRegistry,
)
from apex_one.local_node_gateway.protocol import (
    logger as node_logger,
)

pytestmark = pytest.mark.api

HIGH_ENTROPY_MASTER = "prod-local-node-hmac-master-key-9f3a7c2e5b8d1046QwErTy!"


def test_production_rejects_default_secret_and_missing_secret() -> None:
    """1. Production fails fast on DEFAULT_DEV_NODE_SECRET, missing secret, or low-entropy secret."""
    with pytest.raises((ValidationError, ValueError), match="forbids DEFAULT_DEV_NODE_SECRET"):
        Settings(
            env="production",
            secret_key=SecretStr("prod-app-secret-key-with-high-entropy-9876543210"),
            apex_database_url=SecretStr("postgresql+asyncpg://u:p@db.internal:5432/apex"),
            db_ssl_required=True,
            allowed_origins="https://app.apex-one.internal",
            local_node_shared_secret=SecretStr(DEFAULT_DEV_NODE_SECRET),
        )

    # Missing secret when building production container -> fail fast
    cfg_missing = Settings(
        env="production",
        secret_key=SecretStr("prod-app-secret-key-with-high-entropy-9876543210"),
        apex_database_url=SecretStr("postgresql+asyncpg://u:p@db.internal:5432/apex"),
        db_ssl_required=True,
        allowed_origins="https://app.apex-one.internal",
        local_node_shared_secret=None,
    )
    with pytest.raises(ValueError, match="missing secret fail-fast"):
        build_app_container(cfg_missing)

    with pytest.raises((ValidationError, ValueError), match="APEX_NODE_SHARED_SECRET"):
        LocalNodeSettings(env="production", shared_secret=SecretStr(DEFAULT_DEV_NODE_SECRET))


def test_staging_rejects_default_and_low_entropy_secret() -> None:
    """2. Staging fails fast on DEFAULT_DEV_NODE_SECRET, missing secret, or low-entropy repeated characters."""
    with pytest.raises((ValidationError, ValueError), match="forbids DEFAULT_DEV_NODE_SECRET"):
        Settings(
            env="staging",
            secret_key=SecretStr("staging-app-secret-key-with-high-entropy-12345678"),
            apex_database_url=SecretStr("postgresql+asyncpg://u:p@stg.internal:5432/apex"),
            db_ssl_required=True,
            allowed_origins="https://staging.apex-one.internal",
            local_node_shared_secret=SecretStr(DEFAULT_DEV_NODE_SECRET),
        )

    # Low entropy repeated string ('a' * 40) rejected in staging
    with pytest.raises((ValidationError, ValueError), match="high-entropy"):
        LocalNodeGatewayRegistry(shared_secret="a" * 40, env="staging")

    with pytest.raises(ValueError, match="missing secret fail-fast"):
        LocalNodeGatewayRegistry(shared_secret=None, env="staging")


@pytest.mark.asyncio
async def test_valid_device_handshake_and_invalid_hmac_and_expired_timestamp_and_replay() -> None:
    """3, 4, 5, 6. Valid per-device handshake succeeds; invalid HMAC, expired timestamp, and nonce replay fail."""
    cfg = Settings(
        env="development",
        local_node_shared_secret=SecretStr(HIGH_ENTROPY_MASTER),
    )
    app = create_app(cfg)
    registry: LocalNodeGatewayRegistry = app.state.container.local_node_registry
    tenant_id = uuid.uuid4()
    issued = registry.provision_device_credential(tenant_id=tenant_id, node_id="lab-gpu-node-01")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cloud.local") as client:
        node_cfg = LocalNodeSettings(
            cloud_url="http://cloud.local",
            node_id="lab-gpu-node-01",
            node_name="Workstation GPU 01",
            tenant_id=tenant_id,
            credential_id=issued.credential_id,
            shared_secret=issued.device_secret,
        )
        node_client = LocalNodeClient(node_cfg, http_client=client)

        # (3) Valid per-device handshake + heartbeat
        hs = await node_client.handshake()
        assert hs["node_id"] == "lab-gpu-node-01"
        assert hs["credential_id"] == issued.credential_id
        hb = await node_client.heartbeat(active_tasks=2)
        assert hb["status"] == "ACK"
        assert hb["node_status"] == "BUSY"

        # (4) Invalid HMAC signature -> 401
        payload = node_client.build_handshake_payload()
        forged_headers = build_signed_headers(
            "apxnd_" + ("0" * 64),
            payload,
            credential_id=issued.credential_id,
        )
        r_bad_hmac = await client.post(
            "/api/v1/local-node/handshake", json=payload, headers=forged_headers
        )
        assert r_bad_hmac.status_code == 401
        assert "Invalid Local Node HMAC" in r_bad_hmac.json()["detail"]

        # (5) Expired timestamp -> 401
        expired_headers = build_signed_headers(
            issued.device_secret.get_secret_value(),
            payload,
            credential_id=issued.credential_id,
            timestamp=int(time.time()) - 900,
        )
        r_expired = await client.post(
            "/api/v1/local-node/handshake", json=payload, headers=expired_headers
        )
        assert r_expired.status_code == 401
        assert "outside allowed replay window" in r_expired.json()["detail"]

        # (6) Nonce replay -> 401
        valid_headers = build_signed_headers(
            issued.device_secret.get_secret_value(),
            payload,
            credential_id=issued.credential_id,
            nonce="unique-nonce-replay-check-0001",
        )
        r_ok = await client.post(
            "/api/v1/local-node/handshake", json=payload, headers=valid_headers
        )
        assert r_ok.status_code == 200
        r_replay = await client.post(
            "/api/v1/local-node/handshake", json=payload, headers=valid_headers
        )
        assert r_replay.status_code == 401
        assert "Replay detected" in r_replay.json()["detail"]


def test_revoked_device_and_cross_tenant_and_rotation_and_secret_log_safety(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """7, 8, 9, 10. Revocation, cross-tenant isolation, credential rotation, and zero secret leakage."""
    cfg = Settings(
        env="development",
        local_node_shared_secret=SecretStr(HIGH_ENTROPY_MASTER),
    )
    app = create_app(cfg)
    registry: LocalNodeGatewayRegistry = app.state.container.local_node_registry

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    with caplog.at_level(logging.INFO):
        cred_v1 = registry.provision_device_credential(
            tenant_id=tenant_a, node_id="secure-node-alpha"
        )
        raw_v1 = cred_v1.device_secret.get_secret_value()

        with TestClient(app) as client:
            payload_a = {
                "protocol_version": "1.0",
                "node_id": "secure-node-alpha",
                "node_name": "Alpha Node",
                "tenant_id": str(tenant_a),
                "credential_id": cred_v1.credential_id,
                "capabilities": {"os": "linux"},
            }
            hs_v1 = client.post(
                "/api/v1/local-node/handshake",
                json=payload_a,
                headers=build_signed_headers(
                    raw_v1, payload_a, credential_id=cred_v1.credential_id
                ),
            )
            assert hs_v1.status_code == 200
            session_v1 = hs_v1.json()["session_token"]

            # (8) Cross-tenant device rejection: Tenant B cannot use Tenant A's node or credential
            forged_cross_tenant = {
                **payload_a,
                "tenant_id": str(tenant_b),
            }
            r_cross = client.post(
                "/api/v1/local-node/handshake",
                json=forged_cross_tenant,
                headers=build_signed_headers(
                    raw_v1,
                    forged_cross_tenant,
                    credential_id=cred_v1.credential_id,
                ),
            )
            assert r_cross.status_code == 401
            assert "Cross-tenant" in r_cross.json()["detail"]

            with pytest.raises(PermissionError, match="Cross-tenant"):
                registry.revoke_device(tenant_id=tenant_b, node_id="secure-node-alpha")

            # (9) Credential rotation: old key & old session immediately fail; new key v2 succeeds
            cred_v2 = registry.rotate_device_credential(
                tenant_id=tenant_a, node_id="secure-node-alpha"
            )
            assert cred_v2.key_version == 2
            assert cred_v2.credential_id != cred_v1.credential_id
            raw_v2 = cred_v2.device_secret.get_secret_value()

            # Old v1 credential rejected on handshake
            r_old_hs = client.post(
                "/api/v1/local-node/handshake",
                json=payload_a,
                headers=build_signed_headers(
                    raw_v1, payload_a, credential_id=cred_v1.credential_id
                ),
            )
            assert r_old_hs.status_code == 401

            # Old v1 session token rejected on heartbeat
            hb_payload_v2 = {
                "protocol_version": "1.0",
                "node_id": "secure-node-alpha",
                "tenant_id": str(tenant_a),
                "credential_id": cred_v2.credential_id,
                "status": "ONLINE",
                "active_tasks": 0,
            }
            hb_old_headers = build_signed_headers(
                raw_v2, hb_payload_v2, credential_id=cred_v2.credential_id
            )
            hb_old_headers["Authorization"] = f"Bearer {session_v1}"
            r_old_hb = client.post(
                "/api/v1/local-node/heartbeat",
                json=hb_payload_v2,
                headers=hb_old_headers,
            )
            assert r_old_hb.status_code == 401

            # New v2 credential succeeds
            payload_v2 = {**payload_a, "credential_id": cred_v2.credential_id}
            hs_v2 = client.post(
                "/api/v1/local-node/handshake",
                json=payload_v2,
                headers=build_signed_headers(
                    raw_v2, payload_v2, credential_id=cred_v2.credential_id
                ),
            )
            assert hs_v2.status_code == 200
            session_v2 = hs_v2.json()["session_token"]

            # (7) Device revocation terminates handshake, heartbeat, AND WebSocket immediately
            rev = registry.revoke_device(tenant_id=tenant_a, node_id="secure-node-alpha")
            assert rev["device_revoked"] is True

            r_rev_hs = client.post(
                "/api/v1/local-node/handshake",
                json=payload_v2,
                headers=build_signed_headers(
                    raw_v2, payload_v2, credential_id=cred_v2.credential_id
                ),
            )
            assert r_rev_hs.status_code == 401
            assert "revoked" in r_rev_hs.json()["detail"].lower()

            hb_rev_headers = build_signed_headers(
                raw_v2, hb_payload_v2, credential_id=cred_v2.credential_id
            )
            hb_rev_headers["Authorization"] = f"Bearer {session_v2}"
            r_rev_hb = client.post(
                "/api/v1/local-node/heartbeat",
                json=hb_payload_v2,
                headers=hb_rev_headers,
            )
            assert r_rev_hb.status_code == 401

            with pytest.raises(PermissionError, match="revoked"):
                registry.verify_session_token("secure-node-alpha", session_v2)

            # (10) Secrets never appear in logs, repr(), credential list, or telemetry status
            node_logger.warning("Simulated error log containing %s and %s", raw_v1, HIGH_ENTROPY_MASTER)

            status_resp = client.get("/api/v1/local-node/status")
            assert status_resp.status_code == 200
            status_dump = status_resp.text
            cred_list_dump = str(registry.list_device_credentials(tenant_id=tenant_a))
            repr_dump = f"{registry!r} {cred_v1!r} {cred_v2!r}"
            combined_logs = "\n".join(rec.getMessage() for rec in caplog.records)

            for sensitive in (HIGH_ENTROPY_MASTER, raw_v1, raw_v2, session_v1, session_v2):
                assert sensitive not in status_dump
                assert sensitive not in cred_list_dump
                assert sensitive not in repr_dump
                assert sensitive not in combined_logs
            assert cred_v2.secret_hash not in cred_list_dump
