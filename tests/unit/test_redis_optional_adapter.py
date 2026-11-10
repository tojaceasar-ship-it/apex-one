"""Suite A — Unit tests for optional Redis adapter."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from apex_one.cache.redis_adapter import (
    DisabledCacheAdapter,
    RedisCacheAdapter,
    build_cache_adapter,
)
from apex_one.config import Settings

pytestmark = pytest.mark.unit


async def test_disabled_cache_adapter_graceful_fallback() -> None:
    cfg = Settings(env="development", apex_redis_url=None, redis_url=None)
    adapter = build_cache_adapter(cfg)
    assert isinstance(adapter, DisabledCacheAdapter)
    assert adapter.is_enabled is False
    assert await adapter.get("missing") is None
    assert await adapter.set("k", "v") is False
    report = await adapter.health_check()
    assert report.mode == "disabled_optional"
    assert report.configured is False


async def test_redis_adapter_redacts_secret_dsn() -> None:
    cfg = Settings(
        env="development",
        apex_redis_url=SecretStr("rediss://default:SuperSecretRedisToken@cache.internal:6379/0"),
    )
    adapter = build_cache_adapter(cfg)
    assert isinstance(adapter, RedisCacheAdapter)
    assert adapter.is_enabled is True
    # Health check against unreachable host fails cleanly without leaking password
    report = await adapter.health_check()
    assert report.configured is True
    assert report.connected is False
    assert "SuperSecretRedisToken" not in report.dsn_redacted
    assert "SuperSecretRedisToken" not in (report.error or "")
    await adapter.close()
