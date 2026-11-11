"""HARDENING 1.0 — Stage 1 (P0): Safe Application Initialization & Demo Seed Isolation.

Test-only scope: synthetic test passwords below are strictly confined to automated
verification fixtures and are never enabled by default or allowed outside dev/test.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from apex_one.api.dependencies import build_app_container
from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.workspace_service import ApexPlatformService

pytestmark = pytest.mark.unit


def _prod_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "production",
        "secret_key": SecretStr("prod-hardened-secret-key-minimum-32-chars-998877"),
        "apex_database_url": SecretStr(
            "postgresql+asyncpg://apex_app:test_pass_only@db.example.internal:5432/apex_prod"
        ),
        "db_ssl_required": True,
        "allowed_origins": "https://research.apex-one.internal",
        "local_node_shared_secret": SecretStr(
            "prod-local-node-hmac-secret-high-entropy-48b-xyz987"
        ),
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_production_never_seeds_demo_accounts() -> None:
    """1. Production startup never creates demo organizations or demo accounts."""
    cfg = _prod_settings()
    app = create_app(cfg)
    svc: ApexPlatformService = app.state.container.platform

    assert svc.demo_seeded is False
    assert "admin@apex-one.local" not in svc.users_by_email
    assert "researcher@apex-one.local" not in svc.users_by_email
    assert "evaluator@apex-one.local" not in svc.users_by_email
    assert "viewer@apex-one.local" not in svc.users_by_email
    assert len(svc.users) == 0
    assert len(svc.organizations) == 0


def test_default_app_start_does_not_seed_demo() -> None:
    """2. Default create_app() / ApexPlatformService() start clean with zero demo accounts."""
    svc_default = ApexPlatformService()
    assert svc_default.demo_seeded is False
    assert len(svc_default.users) == 0
    assert len(svc_default.organizations) == 0

    app_default = create_app(Settings(env="development"))
    assert app_default.state.container.platform.demo_seeded is False
    assert "admin@apex-one.local" not in app_default.state.container.platform.users_by_email

    # Real user/organization registration continues to work identically
    owner = app_default.state.container.platform.register_user(
        email="pi@university-lab.eu",
        password="RealOrgStrongPass!2026",
        display_name="Principal Investigator",
        organization_name="University Quantum Lab",
    )
    assert owner["email"] == "pi@university-lab.eu"
    assert len(app_default.state.container.platform.organizations) == 1


def test_demo_seed_requires_explicit_development_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """3. Demo seed activates ONLY when explicitly opted in under development or test."""
    monkeypatch.setenv("APEX_DEV_DEMO_PASSWORD", "DevTestFixtureOnly!2026")

    # Explicit opt-in via Settings(env="development", enable_demo_seed=True)
    dev_cfg = Settings(env="development", enable_demo_seed=True)
    dev_app = create_app(dev_cfg)
    assert dev_app.state.container.platform.demo_seeded is True
    assert "admin@apex-one.local" in dev_app.state.container.platform.users_by_email

    # Explicit opt-in via Settings(env="test", enable_demo_seed=True)
    test_cfg = Settings(env="test", enable_demo_seed=True)
    test_container = build_app_container(test_cfg)
    assert test_container.platform.demo_seeded is True

    # Accidental env flags like APEX_DEBUG=true in development still do NOT enable demo seed
    debug_only_cfg = Settings(env="development", debug=True)
    debug_app = create_app(debug_only_cfg)
    assert debug_app.state.container.platform.demo_seeded is False
    assert len(debug_app.state.container.platform.users) == 0


def test_production_rejects_demo_seed_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """4. Production & staging fail fast on any attempt (config, kwarg, or direct service) to enable demo seed."""
    with pytest.raises((ValidationError, ValueError), match="forbids demo seeding"):
        _prod_settings(enable_demo_seed=True)

    with pytest.raises((ValidationError, ValueError), match="forbids demo seeding"):
        Settings(
            env="staging",
            enable_demo_seed=True,
            secret_key=SecretStr("staging-secret-key-at-least-32-characters-long-01"),
            apex_database_url=SecretStr(
                "postgresql+asyncpg://apex_app:pass@staging-db.internal:5432/apex_stg"
            ),
            db_ssl_required=True,
            allowed_origins="https://staging.apex-one.internal",
        )

    prod_cfg = _prod_settings()
    with pytest.raises(ValueError, match="strictly forbidden"):
        create_app(prod_cfg, seed_demo=True)

    with pytest.raises(ValueError, match="strictly forbidden"):
        build_app_container(prod_cfg, seed_demo=True)

    with pytest.raises(ValueError, match="forbidden"):
        ApexPlatformService(seed_demo=True, env="production")

    # Even if APEX_ENV=production is in os.environ and env kwarg omitted, seed_demo=True fails fast
    monkeypatch.setenv("APEX_ENV", "production")
    with pytest.raises(ValueError, match="forbidden"):
        ApexPlatformService(seed_demo=True)


@pytest.mark.asyncio
async def test_demo_accounts_are_not_exposed_in_production() -> None:
    """5. Login attempts with legacy demo accounts return 401 and environment status reports demo_seed_active=False."""
    cfg = _prod_settings()
    app = create_app(cfg)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="https://research.apex-one.internal") as client:
        status_resp = await client.get("/api/v1/environment/status")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["environment_profile"] == "production"
        assert body["demo_seed_active"] is False

        for demo_email in (
            "admin@apex-one.local",
            "researcher@apex-one.local",
            "evaluator@apex-one.local",
            "viewer@apex-one.local",
        ):
            login_resp = await client.post(
                "/api/v1/auth/login",
                json={"email": demo_email, "password": "ApexResearch!2026"},
            )
            assert login_resp.status_code == 401
