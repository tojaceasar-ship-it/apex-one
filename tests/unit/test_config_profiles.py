"""Suite A — Unit tests for configuration profiles and PostgreSQL-only invariants."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from apex_one.config import (
    Settings,
    UnsupportedDatabaseBackendError,
    normalize_asyncpg_dsn,
    normalize_psycopg_dsn,
    redact_dsn,
    validate_postgres_dsn,
)

pytestmark = pytest.mark.unit


def test_development_profile_works_without_external_services() -> None:
    cfg = Settings(
        env="development",
        apex_database_url=None,
        database_url=None,
        apex_redis_url=None,
        redis_url=None,
    )
    assert cfg.env == "development"
    assert not cfg.is_database_configured
    assert not cfg.is_redis_configured
    summary = cfg.redacted_summary()
    assert summary["database_dsn_redacted"] == "<unconfigured>"


def test_sqlite_and_file_substitutes_are_strictly_rejected() -> None:
    with pytest.raises(UnsupportedDatabaseBackendError):
        validate_postgres_dsn("sqlite+aiosqlite:///./forbidden.db")
    with pytest.raises(UnsupportedDatabaseBackendError):
        validate_postgres_dsn("file:///tmp/data.json")
    with pytest.raises(ValidationError, match="SQLite and file-based substitutes are forbidden"):
        Settings(
            env="development",
            apex_database_url=SecretStr("sqlite+aiosqlite:///./forbidden.db"),
        )


def test_production_and_staging_enforce_strict_invariants() -> None:
    # Missing strong secret -> rejected
    with pytest.raises(ValueError, match="APEX_SECRET_KEY"):
        Settings(
            env="production",
            apex_database_url=SecretStr("postgresql://u:p@db.internal:5432/apex"),
            db_ssl_required=True,
            allowed_origins="https://app.example.com",
        )

    # Missing SSL requirement -> rejected
    with pytest.raises(ValueError, match="APEX_DB_SSL_REQUIRED"):
        Settings(
            env="staging",
            secret_key=SecretStr("s" * 40),
            apex_database_url=SecretStr("postgresql://u:p@db.internal:5432/apex"),
            db_ssl_required=False,
            allowed_origins="https://staging.example.com",
        )

    # Wildcard CORS in production -> rejected
    with pytest.raises(ValueError, match="APEX_ALLOWED_ORIGINS"):
        Settings(
            env="production",
            secret_key=SecretStr("p" * 40),
            apex_database_url=SecretStr("postgresql://u:p@db.internal:5432/apex"),
            db_ssl_required=True,
            allowed_origins="*",
        )

    # Valid production config passes and redacts secrets
    prod = Settings(
        env="production",
        secret_key=SecretStr("x" * 48),
        apex_database_url=SecretStr(
            "postgresql://apex_user:UltraSecretPass123@ep-prod.neon.tech:5432/apex_prod?sslmode=require"
        ),
        db_ssl_required=True,
        allowed_origins="https://app.apex-one.example.com",
    )
    redacted = prod.redacted_summary()["database_dsn_redacted"]
    assert "UltraSecretPass123" not in str(redacted)
    assert "apex_user" not in str(redacted)
    assert "ep-prod.neon.tech" in str(redacted)


def test_neon_dsn_normalization_for_asyncpg_and_psycopg() -> None:
    neon_url = (
        "postgres://user:secret@ep-cool-sky.eu-central-1.aws.neon.tech/apex_one"
        "?sslmode=require&channel_binding=require"
    )
    async_dsn, connect_args = normalize_asyncpg_dsn(neon_url)
    assert async_dsn.startswith("postgresql+asyncpg://")
    assert "sslmode" not in async_dsn
    assert "channel_binding" not in async_dsn
    assert connect_args.get("ssl") == "require"

    sync_dsn = normalize_psycopg_dsn(neon_url, force_ssl=True)
    assert sync_dsn.startswith("postgresql+psycopg://")
    assert "sslmode=require" in sync_dsn


def test_redact_dsn_never_exposes_credentials() -> None:
    raw = "postgresql+asyncpg://admin_user:MySecret99!@db.example.org:5432/research"
    safe = redact_dsn(raw)
    assert "MySecret99!" not in safe
    assert "admin_user" not in safe
    assert safe == "postgresql+asyncpg://***:***@db.example.org:5432/research"
