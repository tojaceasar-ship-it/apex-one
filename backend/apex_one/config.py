"""Environment-aware configuration for APEX ONE Research Edition.

Supports separate `development`, `staging`, and `production` profiles while
enforcing PostgreSQL-only persistence and strict secret redaction.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentProfile = Literal["development", "staging", "production"]
PoolMode = Literal["pooled", "nullpool"]
SandboxProviderType = Literal[
    "none", "external_http", "e2b", "modal", "rootless_container"
]

DEV_PLACEHOLDER_SECRET = "dev-only-insecure-secret-do-not-use-in-prod-0000"

ALLOWED_POSTGRES_SCHEMES = frozenset(
    {
        "postgres",
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg",
    }
)


class UnsupportedDatabaseBackendError(ValueError):
    """Raised when a non-PostgreSQL backend (e.g. SQLite or JSON) is configured."""


def redact_dsn(raw_url: str | None) -> str:
    """Return a safe, credential-free representation of a DSN for diagnostics."""
    if not raw_url or not raw_url.strip():
        return "<unconfigured>"
    try:
        parts = urlsplit(raw_url.strip())
        scheme = parts.scheme or "unknown"
        host = parts.hostname or "unknown-host"
        port = f":{parts.port}" if parts.port else ""
        db_name = parts.path.lstrip("/") or "<default>"
        return f"{scheme}://***:***@{host}{port}/{db_name}"
    except Exception:
        return "<redacted-dsn>"


def validate_postgres_dsn(raw_url: str) -> str:
    """Ensure the DSN targets PostgreSQL and never SQLite or file storage."""
    cleaned = raw_url.strip()
    parts = urlsplit(cleaned)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_POSTGRES_SCHEMES:
        raise UnsupportedDatabaseBackendError(
            f"Unsupported database scheme '{scheme}'. "
            "APEX ONE requires PostgreSQL (postgresql+asyncpg / postgresql+psycopg); "
            "SQLite and file-based substitutes are forbidden."
        )
    if not parts.hostname:
        raise UnsupportedDatabaseBackendError(
            "PostgreSQL DSN must include a valid hostname."
        )
    return cleaned


def normalize_asyncpg_dsn(raw_url: str, *, force_ssl: bool = False) -> tuple[str, dict[str, object]]:
    """Normalize a PostgreSQL DSN (including Neon/Supabase URLs) for SQLAlchemy + asyncpg."""
    validated = validate_postgres_dsn(raw_url)
    parts = urlsplit(validated)
    query_items = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode = query_items.pop("sslmode", None)
    query_items.pop("channel_binding", None)

    connect_args: dict[str, object] = {}
    if force_ssl or sslmode in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = "require"
    elif query_items.get("ssl") in {"require", "true", "1"}:
        query_items.pop("ssl", None)
        connect_args["ssl"] = "require"

    new_query = urlencode(query_items)
    normalized = urlunsplit(
        ("postgresql+asyncpg", parts.netloc, parts.path, new_query, parts.fragment)
    )
    return normalized, connect_args


def normalize_psycopg_dsn(raw_url: str, *, force_ssl: bool = False) -> str:
    """Normalize a PostgreSQL DSN for synchronous Alembic / psycopg operations."""
    validated = validate_postgres_dsn(raw_url)
    parts = urlsplit(validated)
    query_items = dict(parse_qsl(parts.query, keep_blank_values=True))
    if force_ssl and "sslmode" not in query_items:
        query_items["sslmode"] = "require"
    new_query = urlencode(query_items)
    return urlunsplit(
        ("postgresql+psycopg", parts.netloc, parts.path, new_query, parts.fragment)
    )


class Settings(BaseSettings):
    """Typed configuration supporting development, staging, and production."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: EnvironmentProfile = Field(default="development", validation_alias="APEX_ENV")
    debug: bool = Field(default=False, validation_alias="APEX_DEBUG")
    log_level: str = Field(default="INFO", validation_alias="APEX_LOG_LEVEL")
    host: str = Field(default="0.0.0.0", validation_alias="APEX_HOST")
    port: int = Field(default=8000, validation_alias="APEX_PORT")

    secret_key: SecretStr = Field(
        default=SecretStr(DEV_PLACEHOLDER_SECRET),
        validation_alias="APEX_SECRET_KEY",
    )

    # PostgreSQL configuration (APEX_DATABASE_URL takes precedence over DATABASE_URL)
    apex_database_url: SecretStr | None = Field(
        default=None, validation_alias="APEX_DATABASE_URL"
    )
    database_url: SecretStr | None = Field(
        default=None, validation_alias="DATABASE_URL"
    )
    db_pool_mode: PoolMode = Field(
        default="nullpool", validation_alias="APEX_DB_POOL_MODE"
    )
    db_pool_size: int = Field(default=10, ge=1, le=200, validation_alias="APEX_DB_POOL_SIZE")
    db_max_overflow: int = Field(
        default=10, ge=0, le=200, validation_alias="APEX_DB_MAX_OVERFLOW"
    )
    db_ssl_required: bool = Field(
        default=False, validation_alias="APEX_DB_SSL_REQUIRED"
    )
    db_echo_sql: bool = Field(default=False, validation_alias="APEX_DB_ECHO_SQL")
    embedding_dimensions: int = Field(
        default=1536, ge=64, le=4096, validation_alias="APEX_EMBEDDING_DIMENSIONS"
    )

    # Optional Redis configuration
    apex_redis_url: SecretStr | None = Field(
        default=None, validation_alias="APEX_REDIS_URL"
    )
    redis_url: SecretStr | None = Field(default=None, validation_alias="REDIS_URL")
    redis_required: bool = Field(
        default=False, validation_alias="APEX_REDIS_REQUIRED"
    )

    # Worker & Outbox configuration
    worker_poll_interval_seconds: float = Field(
        default=2.0, gt=0.05, validation_alias="APEX_WORKER_POLL_INTERVAL_SECONDS"
    )
    worker_lease_seconds: int = Field(
        default=60, ge=10, le=3600, validation_alias="APEX_WORKER_LEASE_SECONDS"
    )
    worker_heartbeat_seconds: int = Field(
        default=15, ge=2, le=600, validation_alias="APEX_WORKER_HEARTBEAT_SECONDS"
    )
    worker_max_attempts: int = Field(
        default=5, ge=1, le=50, validation_alias="APEX_WORKER_MAX_ATTEMPTS"
    )

    # Experiment Sandbox configuration
    sandbox_provider: SandboxProviderType = Field(
        default="none", validation_alias="APEX_SANDBOX_PROVIDER"
    )
    sandbox_endpoint: str | None = Field(
        default=None, validation_alias="APEX_SANDBOX_ENDPOINT"
    )
    sandbox_api_key: SecretStr | None = Field(
        default=None, validation_alias="APEX_SANDBOX_API_KEY"
    )
    sandbox_isolation_attested: bool = Field(
        default=False, validation_alias="APEX_SANDBOX_ISOLATION_ATTESTED"
    )

    # Hybrid Local Node Gateway & CORS
    arena_isolated_mode: bool = Field(
        default=True, validation_alias="APEX_ARENA_ISOLATED_MODE"
    )
    local_node_shared_secret: SecretStr | None = Field(
        default=None, validation_alias="APEX_LOCAL_NODE_SHARED_SECRET"
    )
    allowed_origins: str = Field(
        default="*", validation_alias="APEX_ALLOWED_ORIGINS"
    )

    @property
    def effective_database_secret(self) -> SecretStr | None:
        if self.apex_database_url and self.apex_database_url.get_secret_value().strip():
            return self.apex_database_url
        if self.database_url and self.database_url.get_secret_value().strip():
            return self.database_url
        return None

    @property
    def is_database_configured(self) -> bool:
        return self.effective_database_secret is not None

    @property
    def database_source_env_var(self) -> str | None:
        if self.apex_database_url and self.apex_database_url.get_secret_value().strip():
            return "APEX_DATABASE_URL"
        if self.database_url and self.database_url.get_secret_value().strip():
            return "DATABASE_URL"
        return None

    @property
    def effective_redis_secret(self) -> SecretStr | None:
        if self.apex_redis_url and self.apex_redis_url.get_secret_value().strip():
            return self.apex_redis_url
        if self.redis_url and self.redis_url.get_secret_value().strip():
            return self.redis_url
        return None

    @property
    def is_redis_configured(self) -> bool:
        return self.effective_redis_secret is not None

    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    def redacted_summary(self) -> dict[str, object]:
        """Safe diagnostic snapshot that never exposes secret values."""
        return {
            "environment": self.env,
            "database_configured": self.is_database_configured,
            "database_env_source": self.database_source_env_var,
            "database_dsn_redacted": redact_dsn(
                self.effective_database_secret.get_secret_value()
                if self.effective_database_secret
                else None
            ),
            "db_pool_mode": self.db_pool_mode,
            "db_ssl_required": self.db_ssl_required,
            "redis_configured": self.is_redis_configured,
            "redis_required": self.redis_required,
            "sandbox_provider": self.sandbox_provider,
            "sandbox_isolation_attested": self.sandbox_isolation_attested,
            "arena_isolated_mode": self.arena_isolated_mode,
        }

    @model_validator(mode="after")
    def _validate_environment_constraints(self) -> Settings:
        # Validate PostgreSQL scheme whenever a DSN is supplied
        if self.effective_database_secret is not None:
            validate_postgres_dsn(self.effective_database_secret.get_secret_value())

        # Enforce strict production & staging invariants
        if self.env in {"staging", "production"}:
            raw_secret = self.secret_key.get_secret_value()
            if raw_secret == DEV_PLACEHOLDER_SECRET or len(raw_secret) < 32:
                raise ValueError(
                    f"Environment '{self.env}' requires APEX_SECRET_KEY of at least 32 characters."
                )
            if not self.is_database_configured:
                raise ValueError(
                    f"Environment '{self.env}' requires PostgreSQL (APEX_DATABASE_URL or DATABASE_URL)."
                )
            if not self.db_ssl_required:
                raise ValueError(
                    f"Environment '{self.env}' requires APEX_DB_SSL_REQUIRED=true."
                )
            if "*" in self.cors_origins_list:
                raise ValueError(
                    f"Environment '{self.env}' forbids wildcard '*' in APEX_ALLOWED_ORIGINS."
                )

        if self.redis_required and not self.is_redis_configured:
            raise ValueError(
                "APEX_REDIS_REQUIRED=true, but neither APEX_REDIS_URL nor REDIS_URL is set."
            )

        if self.sandbox_provider == "none" and self.sandbox_isolation_attested:
            raise ValueError(
                "APEX_SANDBOX_ISOLATION_ATTESTED cannot be true when APEX_SANDBOX_PROVIDER='none'."
            )
        return self


def check_env_var_presence_only() -> dict[str, bool]:
    """Check strictly for presence of sensitive env vars without reading/returning values."""
    keys = (
        "DATABASE_URL",
        "APEX_DATABASE_URL",
        "REDIS_URL",
        "APEX_REDIS_URL",
        "APEX_SANDBOX_API_KEY",
        "APEX_LOCAL_NODE_SHARED_SECRET",
    )
    return {key: bool(os.environ.get(key, "").strip()) for key in keys}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
