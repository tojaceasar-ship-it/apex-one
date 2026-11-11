"""Optional Redis adapter for APEX ONE Research Edition.

Redis is strictly an optional acceleration layer (ephemeral caching / pub-sub).
Durable state and background job queuing always rely on PostgreSQL Outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as aioredis

from apex_one.config import Settings, redact_dsn


@dataclass(frozen=True)
class CacheHealthReport:
    enabled: bool
    configured: bool
    connected: bool
    dsn_redacted: str
    mode: str
    error: str | None = None


_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local cutoff = tonumber(ARGV[1])
local now_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local window_ms = tonumber(ARGV[4])
local member = ARGV[5]

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
    return {0, 0}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, math.max(0, limit - (count + 1))}
"""


class CacheAdapter(Protocol):
    @property
    def is_enabled(self) -> bool: ...

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> bool: ...

    async def delete(self, key: str) -> bool: ...

    async def publish(self, channel: str, message: str) -> int: ...

    async def check_sliding_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now_ms: int,
        member_id: str,
    ) -> tuple[bool, int]: ...

    async def health_check(self) -> CacheHealthReport: ...

    async def close(self) -> None: ...


class DisabledCacheAdapter:
    """No-op adapter used when optional Redis is not configured."""

    @property
    def is_enabled(self) -> bool:
        return False

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> bool:
        return False

    async def delete(self, key: str) -> bool:
        return False

    async def publish(self, channel: str, message: str) -> int:
        return 0

    async def check_sliding_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now_ms: int,
        member_id: str,
    ) -> tuple[bool, int]:
        raise RuntimeError("Redis rate-limit backend is not enabled.")

    async def health_check(self) -> CacheHealthReport:
        return CacheHealthReport(
            enabled=False,
            configured=False,
            connected=False,
            dsn_redacted="<unconfigured>",
            mode="disabled_optional",
        )

    async def close(self) -> None:
        return None


class RedisCacheAdapter:
    """Async Redis adapter used when APEX_REDIS_URL or REDIS_URL is configured."""

    def __init__(self, redis_url: str) -> None:
        self._raw_url = redis_url
        self._redacted = redact_dsn(redis_url)
        self._client: aioredis.Redis | None = None

    @property
    def is_enabled(self) -> bool:
        return True

    def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                self._raw_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3.0,
                socket_timeout=3.0,
            )
        return self._client

    async def get(self, key: str) -> str | None:
        client = self._get_client()
        val = await client.get(key)
        return str(val) if val is not None else None

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> bool:
        client = self._get_client()
        res = await client.set(key, value, ex=ttl_seconds)
        return bool(res)

    async def delete(self, key: str) -> bool:
        client = self._get_client()
        deleted = await client.delete(key)
        return int(deleted) > 0

    async def publish(self, channel: str, message: str) -> int:
        client = self._get_client()
        count = await client.publish(channel, message)
        return int(count)

    async def check_sliding_window(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now_ms: int,
        member_id: str,
    ) -> tuple[bool, int]:
        client = self._get_client()
        window_ms = max(1000, int(window_seconds * 1000))
        cutoff = now_ms - window_ms
        res = await client.eval(  # type: ignore[misc]
            _SLIDING_WINDOW_LUA,
            1,
            f"apex:rl:{key}",
            str(cutoff),
            str(now_ms),
            str(limit),
            str(window_ms),
            member_id,
        )
        allowed = bool(int(res[0]))
        remaining = int(res[1])
        return allowed, remaining

    async def health_check(self) -> CacheHealthReport:
        try:
            client = self._get_client()
            pong = await client.ping()
            return CacheHealthReport(
                enabled=True,
                configured=True,
                connected=bool(pong),
                dsn_redacted=self._redacted,
                mode="redis_async",
            )
        except Exception as exc:
            msg = str(exc)
            if self._raw_url in msg:
                msg = msg.replace(self._raw_url, "<REDACTED_REDIS_URL>")
            return CacheHealthReport(
                enabled=True,
                configured=True,
                connected=False,
                dsn_redacted=self._redacted,
                mode="redis_async",
                error=f"{type(exc).__name__}: {msg}",
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def build_cache_adapter(settings: Settings) -> CacheAdapter:
    secret = settings.effective_redis_secret
    if secret is None:
        return DisabledCacheAdapter()
    return RedisCacheAdapter(secret.get_secret_value())
