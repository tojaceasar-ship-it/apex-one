"""Authentication, Argon2id hashing, RBAC, rate limiting, and prompt-injection defense."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

RoleName = Literal[
    "Owner",
    "Administrator",
    "Researcher",
    "Operator",
    "Evaluator",
    "Viewer",
]

ROLE_PERMISSIONS: dict[RoleName, frozenset[str]] = {
    "Owner": frozenset(
        {
            "read:all",
            "org:manage",
            "users:manage",
            "agents:write",
            "agents:run",
            "research:write",
            "sources:write",
            "memory:write",
            "experiments:write",
            "experiments:run",
            "evaluations:write",
            "evaluations:human_review",
            "models:manage",
            "nodes:manage",
            "workflows:write",
            "governance:approve_r2",
            "governance:approve_r3",
            "governance:emergency_stop",
            "extensions:manage",
        }
    ),
    "Administrator": frozenset(
        {
            "read:all",
            "org:manage",
            "users:manage",
            "agents:write",
            "agents:run",
            "research:write",
            "sources:write",
            "memory:write",
            "experiments:write",
            "experiments:run",
            "evaluations:write",
            "evaluations:human_review",
            "models:manage",
            "nodes:manage",
            "workflows:write",
            "governance:approve_r2",
            "governance:approve_r3",
            "governance:emergency_stop",
            "extensions:manage",
        }
    ),
    "Researcher": frozenset(
        {
            "read:all",
            "agents:write",
            "agents:run",
            "research:write",
            "sources:write",
            "memory:write",
            "experiments:write",
            "experiments:run",
            "evaluations:write",
            "workflows:write",
        }
    ),
    "Operator": frozenset(
        {
            "read:all",
            "agents:run",
            "experiments:run",
            "nodes:manage",
            "workflows:write",
            "governance:approve_r2",
        }
    ),
    "Evaluator": frozenset(
        {
            "read:all",
            "evaluations:write",
            "evaluations:human_review",
        }
    ),
    "Viewer": frozenset({"read:all"}),
}

_password_hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)


def hash_password(plain_password: str) -> str:
    if len(plain_password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bool(_password_hasher.verify(password_hash, plain_password))
    except (VerifyMismatchError, Exception):
        return False


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def generate_secure_token(prefix: str = "apx") -> tuple[str, str]:
    raw = f"{prefix}_{secrets.token_urlsafe(32)}"
    return raw, sha256_hex(raw)


def role_has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, frozenset())  # type: ignore[arg-type]
    return permission in perms


INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions",
            re.IGNORECASE,
        ),
    ),
    (
        "privilege_escalation_prompt",
        re.compile(
            r"(grant\s+role\s+owner|bypass\s+approval|disable\s+row\s+level\s+security|alter\s+authorization)",
            re.IGNORECASE,
        ),
    ),
    (
        "secret_exfiltration_prompt",
        re.compile(
            r"(print|exfiltrate|reveal|dump)\s+(all\s+)?(env|secrets?|database_url|api_key)",
            re.IGNORECASE,
        ),
    ),
    (
        "active_script_payload",
        re.compile(r"<\s*script\b|javascript\s*:", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class ContentSafetyReport:
    safe: bool
    flags: list[str]
    sanitized_text: str


def inspect_untrusted_document(text: str) -> ContentSafetyReport:
    """Detect prompt-injection and active-script patterns in untrusted sources.

    Untrusted source documents are neutralized (script tags stripped) and flagged
    so orchestration agents treat them strictly as quoted evidence, never instructions.
    """
    flags: list[str] = []
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(name)

    # Strip active HTML <script> tags if present in imported text
    sanitized = re.sub(
        r"<\s*script[^>]*>.*?<\s*/\s*script\s*>",
        "[STRIPPED_SCRIPT_TAG]",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return ContentSafetyReport(
        safe=len(flags) == 0,
        flags=flags,
        sanitized_text=sanitized,
    )


class SlidingWindowRateLimiter:
    """In-process sliding window rate limiter keyed by client/session identifier."""

    def __init__(self, *, max_requests: int = 240, window_seconds: int = 60) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        ts = time.monotonic() if now is None else now
        cutoff = ts - self._window
        bucket = self._buckets[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max_requests:
            return False, 0
        bucket.append(ts)
        return True, max(0, self._max_requests - len(bucket))
