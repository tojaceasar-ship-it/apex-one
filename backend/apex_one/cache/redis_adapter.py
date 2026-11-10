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


class CacheAdapter(Protocol):
    @property
    def is_enabled(self) -> bool: ...

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> bool: ...

    async def delete(self, key: str) -> bool: ...

    async def publish(self, channel: str, message: str) -> int: ...

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
