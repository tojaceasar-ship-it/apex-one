"""Authentication, Argon2id hashing, RBAC, rate limiting, and prompt-injection defense."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

logger = logging.getLogger(__name__)

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


class RateLimitTier(StrEnum):
    AUTH_SENSITIVE = "auth_sensitive"
    EXPENSIVE_OPERATION = "expensive_operation"
    GENERAL_API = "general_api"


def classify_rate_limit_tier(method: str, path: str) -> RateLimitTier:
    """Classify an HTTP request into general, auth-sensitive, or expensive LLM/execution tier."""
    m = method.upper()
    norm = path.rstrip("/") or "/"
    if m == "POST" and norm in {"/api/v1/auth/login", "/api/v1/auth/register"}:
        return RateLimitTier.AUTH_SENSITIVE
    if m == "POST" and (
        norm in {
            "/api/v1/runs",
            "/api/v1/runs/async",
            "/api/v1/models/test-inference",
            "/api/v1/sources/fetch-url",
            "/api/v1/connectors/discover",
            "/api/v1/local-intelligence/execute",
        }
        or (norm.startswith("/api/v1/evaluations/suites/") and norm.endswith("/run"))
        or (norm.startswith("/api/v1/experiments/") and norm.endswith("/runs"))
    ):
        return RateLimitTier.EXPENSIVE_OPERATION
    return RateLimitTier.GENERAL_API


def _ip_matches_trusted_proxies(
    peer_ip: str,
    trusted_proxies: list[str] | tuple[str, ...],
) -> bool:
    if not peer_ip or not trusted_proxies:
        return False
    try:
        ip_obj = ipaddress.ip_address(peer_ip.strip())
    except ValueError:
        return peer_ip.strip() in trusted_proxies

    for entry in trusted_proxies:
        cleaned = entry.strip()
        if not cleaned:
            continue
        try:
            if "/" in cleaned:
                if ip_obj in ipaddress.ip_network(cleaned, strict=False):
                    return True
            else:
                if ip_obj == ipaddress.ip_address(cleaned):
                    return True
        except ValueError:
            if peer_ip.strip() == cleaned:
                return True
    return False


def extract_controlled_client_ip(
    *,
    peer_ip: str | None,
    x_forwarded_for: str | None = None,
    trusted_proxies: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, bool]:
    """Resolve client IP without trusting spoofable `X-Forwarded-For` from untrusted peers.

    Returns `(resolved_ip, untrusted_xff_ignored)`.
    """
    direct_ip = (peer_ip or "unknown").strip() or "unknown"
    proxies = list(trusted_proxies or [])
    raw_xff = (x_forwarded_for or "").strip()

    if not raw_xff:
        return direct_ip, False

    if not _ip_matches_trusted_proxies(direct_ip, proxies):
        # Peer is NOT a configured trusted reverse proxy: ignore X-Forwarded-For completely!
        return direct_ip, True

    # Walk right-to-left through X-Forwarded-For hops and return the right-most non-trusted valid IP
    hops = [h.strip() for h in raw_xff.split(",") if h.strip()]
    valid_hops: list[str] = []
    for hop in hops:
        try:
            ipaddress.ip_address(hop)
            valid_hops.append(hop)
        except ValueError:
            continue

    if not valid_hops:
        return direct_ip, True

    for hop in reversed(valid_hops):
        if not _ip_matches_trusted_proxies(hop, proxies):
            return hop, False
    return valid_hops[0], False


class SlidingWindowRateLimiter:
    """Hardened multi-tier rate limiter supporting Redis distributed state and honest local fallback."""

    def __init__(
        self,
        *,
        max_requests: int = 240,
        window_seconds: int = 60,
        auth_max_requests: int | None = None,
        expensive_max_requests: int | None = None,
        cache_adapter: Any = None,
    ) -> None:
        self._max_requests = max(1, int(max_requests))
        self._window = max(1, int(window_seconds))
        self._auth_max_requests = min(
            self._max_requests,
            max(1, int(auth_max_requests if auth_max_requests is not None else 30)),
        )
        self._expensive_max_requests = min(
            self._max_requests,
            max(
                1,
                int(
                    expensive_max_requests
                    if expensive_max_requests is not None
                    else 60
                ),
            ),
        )
        self._cache_adapter = cache_adapter
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

        # Honest operational telemetry
        self._degraded_to_local: bool = False
        self._last_redis_error: str | None = None
        self.redis_fallback_events: int = 0
        self.spoofed_auth_headers_ignored: int = 0
        self.untrusted_xff_ignored: int = 0
        self.blocked_requests: int = 0
        self.blocked_by_tier: dict[str, int] = defaultdict(int)

    @property
    def backend_mode(self) -> str:
        if self._cache_adapter is not None and getattr(
            self._cache_adapter, "is_enabled", False
        ):
            if self._degraded_to_local:
                return "degraded_in_memory_fallback"
            return "redis_distributed"
        return "in_memory_single_instance"

    def record_spoofed_auth_ignored(self) -> None:
        self.spoofed_auth_headers_ignored += 1

    def record_untrusted_xff_ignored(self) -> None:
        self.untrusted_xff_ignored += 1

    def check(
        self,
        key: str,
        *,
        now: float | None = None,
        limit: int | None = None,
    ) -> tuple[bool, int]:
        effective_limit = self._max_requests if limit is None else max(1, int(limit))
        ts = time.monotonic() if now is None else now
        cutoff = ts - self._window
        bucket = self._buckets[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= effective_limit:
            self.blocked_requests += 1
            return False, 0
        bucket.append(ts)
        return True, max(0, effective_limit - len(bucket))

    async def _check_single_bucket_async(
        self,
        bucket_key: str,
        *,
        limit: int,
    ) -> tuple[bool, int]:
        if self._cache_adapter is not None and getattr(
            self._cache_adapter, "is_enabled", False
        ):
            try:
                now_ms = int(time.time() * 1000)
                member_id = f"{now_ms}:{secrets.token_hex(4)}"
                allowed, remaining = await self._cache_adapter.check_sliding_window(
                    bucket_key,
                    limit=limit,
                    window_seconds=self._window,
                    now_ms=now_ms,
                    member_id=member_id,
                )
                self._degraded_to_local = False
                if not allowed:
                    self.blocked_requests += 1
                return allowed, remaining
            except Exception as exc:
                self._degraded_to_local = True
                self.redis_fallback_events += 1
                self._last_redis_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Distributed Redis rate limiter unavailable (%s); enforcing explicit single-instance in-memory fallback.",
                    type(exc).__name__,
                )
        return self.check(bucket_key, limit=limit)

    async def check_request_async(
        self,
        *,
        principal_key: str,
        client_ip: str,
        method: str,
        path: str,
    ) -> tuple[bool, int, RateLimitTier]:
        """Enforce tiered rate limits across general API, login/register, and expensive LLM routes."""
        tier = classify_rate_limit_tier(method, path)

        if tier == RateLimitTier.AUTH_SENSITIVE:
            allowed_auth, rem_auth = await self._check_single_bucket_async(
                f"tier:auth:ip:{client_ip}",
                limit=self._auth_max_requests,
            )
            if not allowed_auth:
                self.blocked_by_tier[tier.value] += 1
                return False, 0, tier
            allowed_gen, rem_gen = await self._check_single_bucket_async(
                f"tier:general:{principal_key}",
                limit=self._max_requests,
            )
            if not allowed_gen:
                self.blocked_by_tier[RateLimitTier.GENERAL_API.value] += 1
                return False, 0, RateLimitTier.GENERAL_API
            return True, min(rem_auth, rem_gen), tier

        if tier == RateLimitTier.EXPENSIVE_OPERATION:
            allowed_exp, rem_exp = await self._check_single_bucket_async(
                f"tier:expensive:{principal_key}",
                limit=self._expensive_max_requests,
            )
            if not allowed_exp:
                self.blocked_by_tier[tier.value] += 1
                return False, 0, tier
            allowed_gen, rem_gen = await self._check_single_bucket_async(
                f"tier:general:{principal_key}",
                limit=self._max_requests,
            )
            if not allowed_gen:
                self.blocked_by_tier[RateLimitTier.GENERAL_API.value] += 1
                return False, 0, RateLimitTier.GENERAL_API
            return True, min(rem_exp, rem_gen), tier

        allowed, rem = await self._check_single_bucket_async(
            f"tier:general:{principal_key}",
            limit=self._max_requests,
        )
        if not allowed:
            self.blocked_by_tier[tier.value] += 1
        return allowed, rem, tier

    def telemetry_snapshot(self) -> dict[str, Any]:
        return {
            "backend_mode": self.backend_mode,
            "distributed_active": self.backend_mode == "redis_distributed",
            "degraded_to_local": self._degraded_to_local,
            "redis_fallback_events": self.redis_fallback_events,
            "last_redis_error": self._last_redis_error,
            "window_seconds": self._window,
            "limits": {
                "general_rpm": self._max_requests,
                "auth_sensitive_rpm": self._auth_max_requests,
                "expensive_operation_rpm": self._expensive_max_requests,
            },
            "spoofed_auth_headers_ignored": self.spoofed_auth_headers_ignored,
            "untrusted_xff_ignored": self.untrusted_xff_ignored,
            "blocked_requests": self.blocked_requests,
            "blocked_by_tier": dict(self.blocked_by_tier),
        }
