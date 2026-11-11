"""HARDENING 1.0 — Stage 4 (P1): Anti-Spoofing, Multi-Tier & Distributed Rate Limiter Tests.

Covers all 7 mandatory verification scenarios:
1. Random `X-Apex-Session-Token` rotation does NOT bypass rate limiting
2. Random `Authorization` header rotation does NOT bypass rate limiting
3. Authenticated user is rate-limited by verified `user_id + tenant_id` (even across multiple valid session tokens)
4. Unauthenticated client is rate-limited by controlled IP (`X-Forwarded-For` ignored unless peer is in `trusted_proxies`)
5. Expensive LLM / orchestration endpoints enforce stricter limits than general API routes
6. Login & registration endpoints enforce brute-force / abuse rate limits (`auth_sensitive`)
7. Distributed Redis-backed mode shares state across instances and reports honest fallback telemetry on Redis failure
"""

from __future__ import annotations

import httpx
import pytest

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.security import SlidingWindowRateLimiter


class _InMemorySharedRedisMock:
    """Deterministic async mock emulating Redis ZSET sliding-window semantics across instances."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.zsets: dict[str, list[tuple[int, str]]] = {}

    @property
    def is_enabled(self) -> bool:
        return True

    async def check_sliding_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now_ms: int,
        member_id: str,
    ) -> tuple[bool, int]:
        if self.fail:
            raise ConnectionError("Simulated Redis cluster connection timeout")
        cutoff = now_ms - int(window_seconds * 1000)
        entries = [
            (ts, mem)
            for ts, mem in self.zsets.get(key, [])
            if ts > cutoff
        ]
        if len(entries) >= limit:
            self.zsets[key] = entries
            return False, 0
        entries.append((now_ms, member_id))
        self.zsets[key] = entries
        return True, max(0, limit - len(entries))


async def test_01_random_x_apex_session_token_does_not_bypass_rate_limit() -> None:
    """1. Rotating forged `X-Apex-Session-Token` values on every request does NOT bypass rate limit."""
    app = create_app(Settings(env="test", enable_demo_seed=False), max_rpm=4)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for idx in range(4):
            resp = await client.get(
                "/api/v1/health",
                headers={"X-Apex-Session-Token": f"apxsess_forged_token_{idx}"},
            )
            assert resp.status_code == 200

        blocked = await client.get(
            "/api/v1/health",
            headers={"X-Apex-Session-Token": "apxsess_forged_token_999"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"

    snap = app.state.container.rate_limiter.telemetry_snapshot()
    assert snap["spoofed_auth_headers_ignored"] == 5
    assert snap["blocked_requests"] >= 1


async def test_02_random_authorization_bearer_header_does_not_bypass_rate_limit() -> None:
    """2. Rotating forged `Authorization: Bearer ...` headers does NOT bypass rate limit."""
    app = create_app(Settings(env="test", enable_demo_seed=False), max_rpm=3)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.25", 52000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for idx in range(3):
            resp = await client.get(
                "/api/v1/health",
                headers={"Authorization": f"Bearer fake_jwt_or_key_{idx}"},
            )
            assert resp.status_code == 200

        blocked = await client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer fake_jwt_or_key_bypass_attempt"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_03_authenticated_user_rate_limited_by_verified_user_and_tenant_across_tokens() -> None:
    """3. Verified `user_id + tenant_id` shares one bucket even across multiple distinct valid tokens/IPs."""
    app = create_app(
        Settings(env="test", enable_demo_seed=False),
        max_rpm=6,
        auth_max_rpm=10,
    )
    svc = app.state.container.platform
    user = svc.register_user(
        email="verified-rl@example.org",
        password="VerifiedRateLimitPass!2026",
        display_name="RL User",
        organization_name="RL Tenant",
    )
    login_1 = svc.login(
        email="verified-rl@example.org", password="VerifiedRateLimitPass!2026"
    )
    login_2 = svc.login(
        email="verified-rl@example.org", password="VerifiedRateLimitPass!2026"
    )
    oid = login_1["active_organization_id"]
    tok_1 = login_1["access_token"]
    tok_2 = login_2["access_token"]
    assert tok_1 != tok_2

    # Issue 3 requests from IP #1 with tok_1 and 3 requests from IP #2 with tok_2 -> hits 6 RPM ceiling!
    t1 = httpx.ASGITransport(app=app, client=("198.51.100.11", 40001))
    t2 = httpx.ASGITransport(app=app, client=("198.51.100.22", 40002))
    async with (
        httpx.AsyncClient(transport=t1, base_url="http://test") as c1,
        httpx.AsyncClient(transport=t2, base_url="http://test") as c2,
    ):
        for _ in range(3):
            r1 = await c1.get(
                "/api/v1/auth/me",
                headers={
                    "X-Apex-Session-Token": tok_1,
                    "X-Organization-Id": oid,
                },
            )
            assert r1.status_code == 200
        for _ in range(3):
            r2 = await c2.get(
                "/api/v1/auth/me",
                headers={
                    "X-Apex-Session-Token": tok_2,
                    "X-Organization-Id": oid,
                },
            )
            assert r2.status_code == 200

        # 7th request for the same user+tenant is blocked regardless of token or source IP
        blocked = await c2.get(
            "/api/v1/auth/me",
            headers={
                "X-Apex-Session-Token": tok_2,
                "X-Organization-Id": oid,
            },
        )
        assert blocked.status_code == 429

        # Meanwhile a DIFFERENT user in a different tenant is completely unaffected
        other_user = svc.register_user(
            email="other-rl@example.org",
            password="OtherRateLimitPass!2026",
            display_name="Other User",
            organization_name="Other Tenant",
        )
        assert other_user["id"] != user["id"]
        other_login = svc.login(
            email="other-rl@example.org", password="OtherRateLimitPass!2026"
        )
        ok_other = await c2.get(
            "/api/v1/auth/me",
            headers={
                "X-Apex-Session-Token": other_login["access_token"],
                "X-Organization-Id": other_login["active_organization_id"],
            },
        )
        assert ok_other.status_code == 200


async def test_04_unauthenticated_client_ip_and_trusted_proxy_xff_enforcement() -> None:
    """4. Untrusted `X-Forwarded-For` spoofing is ignored; configured `trusted_proxies` extracts real client IP."""
    # Case A: No trusted proxies -> rotating X-Forwarded-For does NOT bypass peer IP bucket
    untrusted_app = create_app(Settings(env="test", trusted_proxies=""), max_rpm=3)
    t_untrusted = httpx.ASGITransport(app=untrusted_app, client=("203.0.113.77", 44100))
    async with httpx.AsyncClient(transport=t_untrusted, base_url="http://test") as client:
        for i in range(3):
            ok = await client.get(
                "/api/v1/health",
                headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
            )
            assert ok.status_code == 200
        blocked = await client.get(
            "/api/v1/health",
            headers={"X-Forwarded-For": "198.51.100.250"},
        )
        assert blocked.status_code == 429
    assert untrusted_app.state.container.rate_limiter.untrusted_xff_ignored == 4

    # Case B: Peer `10.10.0.5` IS in `trusted_proxies="10.10.0.0/16"` -> separate client IPs behind LB get distinct buckets
    proxied_app = create_app(
        Settings(env="test", trusted_proxies="10.10.0.0/16"), max_rpm=2
    )
    t_lb = httpx.ASGITransport(app=proxied_app, client=("10.10.0.5", 44200))
    async with httpx.AsyncClient(transport=t_lb, base_url="http://test") as lb_client:
        assert (
            await lb_client.get(
                "/api/v1/health", headers={"X-Forwarded-For": "198.51.100.50"}
            )
        ).status_code == 200
        assert (
            await lb_client.get(
                "/api/v1/health", headers={"X-Forwarded-For": "198.51.100.50"}
            )
        ).status_code == 200
        # Client 198.51.100.50 hits limit:
        assert (
            await lb_client.get(
                "/api/v1/health", headers={"X-Forwarded-For": "198.51.100.50"}
            )
        ).status_code == 429
        # Distinct client 198.51.100.99 behind the same trusted LB still succeeds:
        assert (
            await lb_client.get(
                "/api/v1/health", headers={"X-Forwarded-For": "198.51.100.99"}
            )
        ).status_code == 200


async def test_05_expensive_llm_endpoints_have_stricter_limits_than_general_routes() -> None:
    """5. Expensive LLM / orchestration endpoints enforce stricter ceiling (`expensive_max_rpm`) than general routes."""
    app = create_app(
        Settings(env="test", enable_demo_seed=False),
        max_rpm=25,
        expensive_max_rpm=2,
    )
    svc = app.state.container.platform
    svc.register_user(
        email="expensive-rl@example.org",
        password="ExpensiveRateLimitPass!2026",
        display_name="Expensive Tester",
        organization_name="Expensive Org",
    )
    login = svc.login(
        email="expensive-rl@example.org", password="ExpensiveRateLimitPass!2026"
    )
    headers = {
        "X-Apex-Session-Token": login["access_token"],
        "X-Organization-Id": login["active_organization_id"],
    }

    transport = httpx.ASGITransport(app=app, client=("198.51.100.88", 45000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(2):
            r = await client.post(
                "/api/v1/models/test-inference",
                headers=headers,
                json={
                    "model_identifier": "apex-analytical-local-v1",
                    "prompt": "Quick check",
                },
            )
            assert r.status_code == 200

        blocked_exp = await client.post(
            "/api/v1/models/test-inference",
            headers=headers,
            json={
                "model_identifier": "apex-analytical-local-v1",
                "prompt": "Third expensive call",
            },
        )
        assert blocked_exp.status_code == 429
        assert blocked_exp.json()["error"]["tier"] == "expensive_operation"

        # General read routes remain available under `max_rpm=25`
        general_ok = await client.get("/api/v1/auth/me", headers=headers)
        assert general_ok.status_code == 200


async def test_06_login_and_registration_enforce_auth_sensitive_rate_limits() -> None:
    """6. `/api/v1/auth/login` and `/api/v1/auth/register` enforce dedicated brute-force protection."""
    app = create_app(
        Settings(env="test", enable_demo_seed=False),
        max_rpm=30,
        auth_max_rpm=3,
    )
    transport = httpx.ASGITransport(app=app, client=("203.0.113.199", 46000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 2 failed login attempts + 1 register attempt = 3 auth_sensitive calls from IP
        for _ in range(2):
            r_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "nobody@example.org", "password": "wrong-password-123"},
            )
            assert r_login.status_code in {401, 403}

        r_reg = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "first-reg@example.org",
                "password": "ValidRegisterPassword!2026",
                "display_name": "First Reg",
                "organization_name": "Reg Org",
            },
        )
        assert r_reg.status_code == 201

        # 4th auth attempt from same IP is throttled with 429 `auth_sensitive`
        throttled = await client.post(
            "/api/v1/auth/login",
            json={"email": "first-reg@example.org", "password": "ValidRegisterPassword!2026"},
        )
        assert throttled.status_code == 429
        assert throttled.json()["error"]["tier"] == "auth_sensitive"


@pytest.mark.asyncio
async def test_07_distributed_redis_mode_and_honest_degraded_fallback_telemetry() -> None:
    """7. Redis-backed limiters share quota across instances and report honest fallback when Redis fails."""
    shared_redis = _InMemorySharedRedisMock()
    worker_a = SlidingWindowRateLimiter(
        max_requests=3, window_seconds=60, cache_adapter=shared_redis
    )
    worker_b = SlidingWindowRateLimiter(
        max_requests=3, window_seconds=60, cache_adapter=shared_redis
    )
    assert worker_a.backend_mode == "redis_distributed"
    assert worker_a.telemetry_snapshot()["distributed_active"] is True

    # 2 requests hit Worker A, 1 request hits Worker B -> 4th request on Worker B is blocked by shared state!
    for limiter in (worker_a, worker_a, worker_b):
        allowed, _rem, _tier = await limiter.check_request_async(
            principal_key="auth:org-1:user-1",
            client_ip="198.51.100.5",
            method="GET",
            path="/api/v1/projects",
        )
        assert allowed is True

    allowed_4th, _rem4, _tier4 = await worker_b.check_request_async(
        principal_key="auth:org-1:user-1",
        client_ip="198.51.100.5",
        method="GET",
        path="/api/v1/projects",
    )
    assert allowed_4th is False

    # Now simulate Redis outage: limiter must NOT fail open and must honestly report degraded fallback!
    shared_redis.fail = True
    worker_degraded = SlidingWindowRateLimiter(
        max_requests=2, window_seconds=60, cache_adapter=shared_redis
    )
    ok1, _, _ = await worker_degraded.check_request_async(
        principal_key="auth:org-2:user-9",
        client_ip="198.51.100.9",
        method="GET",
        path="/api/v1/projects",
    )
    ok2, _, _ = await worker_degraded.check_request_async(
        principal_key="auth:org-2:user-9",
        client_ip="198.51.100.9",
        method="GET",
        path="/api/v1/projects",
    )
    blocked3, _, _ = await worker_degraded.check_request_async(
        principal_key="auth:org-2:user-9",
        client_ip="198.51.100.9",
        method="GET",
        path="/api/v1/projects",
    )
    assert (ok1, ok2, blocked3) == (True, True, False)

    snap = worker_degraded.telemetry_snapshot()
    assert snap["backend_mode"] == "degraded_in_memory_fallback"
    assert snap["distributed_active"] is False
    assert snap["degraded_to_local"] is True
    assert snap["redis_fallback_events"] == 3
    assert "ConnectionError" in (snap["last_redis_error"] or "")
