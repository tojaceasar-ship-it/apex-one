"""External PostgreSQL readiness probe for APEX ONE Research Edition.

Checks strictly for presence of `DATABASE_URL` / `APEX_DATABASE_URL` without
displaying secret values. When configured, verifies connectivity, PostgreSQL
version, pgvector availability, isolated schema migration, and Row-Level Security.
When unconfigured, reports honest UNCONFIGURED status without fabricating results.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from apex_one.config import check_env_var_presence_only, get_settings
from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.rls import TENANT_GUC_NAME


def _run_isolated_schema_alembic_probe(schema_name: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.attributes["target_schema"] = schema_name
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    return {"schema": schema_name, "upgrade_downgrade": "PASS"}


async def run_external_db_readiness() -> dict[str, Any]:
    presence = check_env_var_presence_only()
    settings = get_settings()

    report: dict[str, Any] = {
        "env_var_presence": {
            "DATABASE_URL": presence["DATABASE_URL"],
            "APEX_DATABASE_URL": presence["APEX_DATABASE_URL"],
        },
        "database_configured": settings.is_database_configured,
        "dsn_redacted": settings.redacted_summary()["database_dsn_redacted"],
        "overall_status": "UNCONFIGURED",
        "checks": {
            "health_check": "NOT_RUN",
            "postgres_version": None,
            "pgvector": "NOT_RUN",
            "migration_probe": "NOT_RUN",
            "rls_probe": "NOT_RUN",
        },
    }

    if not settings.is_database_configured:
        report["message"] = (
            "Neither APEX_DATABASE_URL nor DATABASE_URL is configured. "
            "See docs/POSTGRESQL_SETUP.md and docs/EXTERNAL_SERVICES_SETUP.md."
        )
        return report

    adapter = PostgresConnectionAdapter(settings)
    try:
        health = await adapter.health_check()
        if not health.connected:
            report["overall_status"] = "CONNECTION_FAILED"
            report["checks"]["health_check"] = "FAILED"
            report["error"] = health.error
            return report

        report["checks"]["health_check"] = "PASS"
        report["checks"]["postgres_version"] = health.server_version
        report["checks"]["pgvector"] = (
            f"PASS (installed={health.pgvector_installed}, version={health.pgvector_version})"
            if health.pgvector_available
            else "BLOCKED (vector extension unavailable on server)"
        )

        engine = adapter.get_engine()
        probe_suffix = uuid.uuid4().hex[:10]

        # 4. Isolated migration check
        mig_schema = f"apex_mig_probe_{probe_suffix}"
        try:
            await asyncio.to_thread(_run_isolated_schema_alembic_probe, mig_schema)
            report["checks"]["migration_probe"] = "PASS"
        except Exception as exc:
            report["checks"]["migration_probe"] = f"FAILED ({type(exc).__name__})"
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{mig_schema}" CASCADE'))

        # 5. RLS isolation check
        rls_schema = f"apex_rls_probe_{probe_suffix}"
        rls_role = f"apex_rls_probe_role_{probe_suffix}"
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{rls_schema}"'))
                await conn.execute(
                    text(
                        f'CREATE TABLE "{rls_schema}".rls_items ('
                        "id UUID PRIMARY KEY, tenant_id UUID NOT NULL, secret TEXT NOT NULL)"
                    )
                )
                await conn.execute(
                    text(f'ALTER TABLE "{rls_schema}".rls_items ENABLE ROW LEVEL SECURITY')
                )
                await conn.execute(
                    text(f'ALTER TABLE "{rls_schema}".rls_items FORCE ROW LEVEL SECURITY')
                )
                await conn.execute(
                    text(
                        f'CREATE POLICY p_probe ON "{rls_schema}".rls_items FOR ALL '
                        f"USING (tenant_id = NULLIF(current_setting('{TENANT_GUC_NAME}', true), '')::uuid) "
                        f"WITH CHECK (tenant_id = NULLIF(current_setting('{TENANT_GUC_NAME}', true), '')::uuid)"
                    )
                )

                # If current user has BYPASSRLS/SUPERUSER (common default owner in Neon),
                # drop into a transient NOBYPASSRLS role inside transaction if permitted.
                switched_role = False
                if health.is_superuser_or_bypassrls:
                    try:
                        await conn.execute(
                            text(f'CREATE ROLE "{rls_role}" NOLOGIN NOBYPASSRLS')
                        )
                        await conn.execute(
                            text(f'GRANT USAGE ON SCHEMA "{rls_schema}" TO "{rls_role}"')
                        )
                        await conn.execute(
                            text(
                                f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{rls_schema}".rls_items TO "{rls_role}"'
                            )
                        )
                        await conn.execute(text(f'SET LOCAL ROLE "{rls_role}"'))
                        switched_role = True
                    except Exception:
                        switched_role = False

                if health.is_superuser_or_bypassrls and not switched_role:
                    report["checks"]["rls_probe"] = (
                        "CONDITIONAL (connected role has BYPASSRLS/SUPERUSER and cannot "
                        "CREATE ROLE; use dedicated apex_app NOBYPASSRLS role per docs/POSTGRESQL_SETUP.md)"
                    )
                else:
                    await conn.execute(
                        text("SELECT set_config(:guc, :val, true)"),
                        {"guc": TENANT_GUC_NAME, "val": str(tenant_a)},
                    )
                    await conn.execute(
                        text(
                            f'INSERT INTO "{rls_schema}".rls_items (id, tenant_id, secret) '
                            "VALUES (:id, :tid, 'alpha')"
                        ),
                        {"id": uuid.uuid4(), "tid": tenant_a},
                    )
                    await conn.execute(
                        text("SELECT set_config(:guc, :val, true)"),
                        {"guc": TENANT_GUC_NAME, "val": str(tenant_b)},
                    )
                    visible_to_b = (
                        await conn.execute(
                            text(f'SELECT count(*) FROM "{rls_schema}".rls_items')
                        )
                    ).scalar_one()
                    report["checks"]["rls_probe"] = (
                        "PASS" if int(visible_to_b) == 0 else "FAILED_LEAK_DETECTED"
                    )
                if switched_role:
                    await conn.execute(text("RESET ROLE"))
        except Exception as exc:
            report["checks"]["rls_probe"] = f"FAILED ({type(exc).__name__})"
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{rls_schema}" CASCADE'))
                try:
                    await conn.execute(text(f'DROP ROLE IF EXISTS "{rls_role}"'))
                except Exception:
                    pass

        all_ok = (
            report["checks"]["health_check"] == "PASS"
            and str(report["checks"]["pgvector"]).startswith("PASS")
            and report["checks"]["migration_probe"] == "PASS"
            and report["checks"]["rls_probe"] == "PASS"
        )
        report["overall_status"] = "READY" if all_ok else "CONDITIONAL"
        return report
    finally:
        await adapter.dispose()


def main() -> int:
    result = asyncio.run(run_external_db_readiness())
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
