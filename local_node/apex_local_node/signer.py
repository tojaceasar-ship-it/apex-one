"""HMAC-SHA256 wire signing and replay protection for Hybrid Local Node v1.0."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any


def canonicalize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_envelope(
    *,
    secret: str,
    timestamp: int,
    nonce: str,
    payload: dict[str, Any],
) -> str:
    canonical = canonicalize_payload(payload)
    signing_input = f"{timestamp}.{nonce}.{canonical}".encode()
    return hmac.new(
        secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()


def verify_envelope_signature(
    *,
    secret: str,
    timestamp: int,
    nonce: str,
    payload: dict[str, Any],
    signature: str,
    now_epoch: int | None = None,
    max_skew_seconds: int = 300,
) -> bool:
    if not nonce or len(nonce) < 8:
        return False
    ref_time = int(time.time()) if now_epoch is None else now_epoch
    if abs(ref_time - int(timestamp)) > max_skew_seconds:
        return False
    expected = sign_envelope(
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
        payload=payload,
    )
    return hmac.compare_digest(expected, signature)


def build_signed_headers(secret: str, payload: dict[str, Any]) -> dict[str, str]:
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = sign_envelope(secret=secret, timestamp=ts, nonce=nonce, payload=payload)
    return {
        "X-Apex-Node-Timestamp": str(ts),
        "X-Apex-Node-Nonce": nonce,
        "X-Apex-Node-Signature": sig,
    }
