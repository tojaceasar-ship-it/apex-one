"""Suite B — Communication protocol tests between standalone Local Node and Cloud Gateway."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from apex_local_node.client import LocalNodeClient, LocalNodeProtocolError
from apex_local_node.config import LocalNodeSettings
from apex_local_node.signer import build_signed_headers
from apex_one.api.main import create_app
from apex_one.config import Settings

pytestmark = pytest.mark.api


async def test_local_node_http_handshake_heartbeat_and_replay_protection() -> None:
    shared_secret = "protocol-test-hmac-secret-minimum-32-bytes!"
    cloud_cfg = Settings(
        env="development",
        arena_isolated_mode=True,
        local_node_shared_secret=SecretStr(shared_secret),
    )
    app = create_app(settings=cloud_cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://cloud.local"
    ) as http_client:
        node_cfg = LocalNodeSettings(
            cloud_url="http://cloud.local",
            node_id="arena-loopback-node-01",
            node_name="arena-protocol-test",
            tenant_id=uuid.uuid4(),
            shared_secret=SecretStr(shared_secret),
        )
        node_client = LocalNodeClient(node_cfg, http_client=http_client)

        hs = await node_client.handshake()
        assert hs["protocol_version"] == "1.0"
        assert hs["node_id"] == "arena-loopback-node-01"
        # Arena isolated mode never claims connection to user's physical computer
        assert hs["user_machine_connected"] is False

        hb = await node_client.heartbeat(active_tasks=1)
        assert hb["status"] == "ACK"
        assert hb["node_status"] == "BUSY"

        # Replay attack using identical signed headers must be rejected with 401
        payload = node_client.build_handshake_payload()
        replay_headers = build_signed_headers(shared_secret, payload)
        r_first = await http_client.post(
            "/api/v1/local-node/handshake",
            json=payload,
            headers=replay_headers,
        )
        assert r_first.status_code == 200
        r_replay = await http_client.post(
            "/api/v1/local-node/handshake",
            json=payload,
            headers=replay_headers,
        )
        assert r_replay.status_code == 401
        assert "Replay detected" in r_replay.json()["detail"]

        # Invalid secret must fail handshake
        bad_node = LocalNodeClient(
            LocalNodeSettings(
                cloud_url="http://cloud.local",
                shared_secret=SecretStr("wrong-secret-0000000000000000000000"),
            ),
            http_client=http_client,
        )
        with pytest.raises(LocalNodeProtocolError):
            await bad_node.handshake()


def test_local_node_websocket_jsonrpc_session() -> None:
    shared_secret = "ws-protocol-hmac-secret-minimum-32-bytes!!"
    cloud_cfg = Settings(
        env="development",
        arena_isolated_mode=True,
        local_node_shared_secret=SecretStr(shared_secret),
    )
    app = create_app(settings=cloud_cfg)

    with TestClient(app) as client:
        payload = {
            "protocol_version": "1.0",
            "node_id": "ws-node-01",
            "node_name": "ws-loopback",
            "tenant_id": str(uuid.uuid4()),
            "capabilities": {"os": "linux"},
        }
        headers = build_signed_headers(shared_secret, payload)
        r_hs = client.post(
            "/api/v1/local-node/handshake", json=payload, headers=headers
        )
        assert r_hs.status_code == 200
        token = r_hs.json()["session_token"]

        with client.websocket_connect(
            f"/api/v1/local-node/ws/ws-node-01?token={token}"
        ) as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "node.ping"})
            pong = ws.receive_json()
            assert pong["result"]["pong"] is True
            assert pong["result"]["user_machine_connected"] is False

            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "node.disconnect"})
            disc = ws.receive_json()
            assert disc["result"]["disconnected"] is True
