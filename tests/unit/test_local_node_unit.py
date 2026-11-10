"""Suite A — Unit tests for the standalone Hybrid Local Node Python application."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from apex_local_node.capabilities import discover_node_capabilities
from apex_local_node.config import LocalNodeSettings
from apex_local_node.signer import sign_envelope, verify_envelope_signature

pytestmark = pytest.mark.unit


def test_local_node_capability_discovery() -> None:
    caps = discover_node_capabilities()
    assert caps["os"] == "linux"
    assert caps["cpu_count"] >= 1
    assert caps["local_fs_confined"] is True


def test_local_node_hmac_signature_and_skew_verification() -> None:
    secret = "shared-hmac-secret-at-least-32-bytes-long"
    payload = {"node_id": "node-01", "status": "ONLINE"}
    now = int(time.time())
    nonce = "nonce-abcdef123456"

    sig = sign_envelope(secret=secret, timestamp=now, nonce=nonce, payload=payload)
    assert verify_envelope_signature(
        secret=secret,
        timestamp=now,
        nonce=nonce,
        payload=payload,
        signature=sig,
        now_epoch=now,
    )

    # Tampered payload fails
    assert not verify_envelope_signature(
        secret=secret,
        timestamp=now,
        nonce=nonce,
        payload={"node_id": "node-01", "status": "COMPROMISED"},
        signature=sig,
        now_epoch=now,
    )

    # Expired timestamp fails
    assert not verify_envelope_signature(
        secret=secret,
        timestamp=now - 900,
        nonce=nonce,
        payload=payload,
        signature=sig,
        now_epoch=now,
    )


def test_local_node_workspace_boundary_blocks_escape(tmp_path: Path) -> None:
    settings = LocalNodeSettings(workspace_root=tmp_path)
    inside = settings.resolve_within_workspace("experiments/run1.json")
    assert str(inside).startswith(str(tmp_path.resolve()))

    with pytest.raises(PermissionError, match="escapes Local Node workspace"):
        settings.resolve_within_workspace("../../etc/shadow")
