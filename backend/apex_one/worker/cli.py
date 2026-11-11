"""Standalone Docker-free CLI entrypoint for the APEX ONE Outbox Worker.

Usage:
  .venv/bin/python -m apex_one.worker.cli --dry-check
  .venv/bin/python -m apex_one.worker.cli --mode once
  .venv/bin/python -m apex_one.worker.cli --mode loop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys

from apex_one.config import get_settings
from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.outbox.models import RetryPolicy
from apex_one.outbox.queue import PostgresOutboxQueue
from apex_one.worker.runtime import JobExecutionContext, WorkerRuntime


async def _default_research_workflow_handler(
    ctx: JobExecutionContext,
) -> dict[str, object]:
    await ctx.heartbeat_and_check_cancelled()
    return {
        "status": "processed",
        "event_type": ctx.job.event_type,
        "workflow_id": ctx.job.payload.get("workflow_id"),
    }


def build_production_worker() -> tuple[WorkerRuntime, PostgresConnectionAdapter]:
    settings = get_settings()
    db_adapter = PostgresConnectionAdapter(settings)
    outbox = PostgresOutboxQueue(db_adapter)
    runtime = WorkerRuntime(
        queue=outbox,
        lease_seconds=settings.worker_lease_seconds,
        heartbeat_interval_seconds=float(settings.worker_heartbeat_seconds),
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        retry_policy=RetryPolicy(max_attempts=settings.worker_max_attempts),
    )
    runtime.register_handler(
        "workflow.literature_review.execute", _default_research_workflow_handler
    )
    runtime.register_handler(
        "workflow.hypothesis_evaluation.execute", _default_research_workflow_handler
    )
    return runtime, db_adapter


async def _async_main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.dry_check:
        print(
            json.dumps(
                {
                    "worker_ready": True,
                    "docker_required": False,
                    "database_configured": settings.is_database_configured,
                    "mode": args.mode,
                    "poll_interval_seconds": settings.worker_poll_interval_seconds,
                    "lease_seconds": settings.worker_lease_seconds,
                },
                indent=2,
            )
        )
        return 0

    if not settings.is_database_configured:
        print(
            json.dumps(
                {
                    "status": "STANDBY_NO_DATABASE",
                    "message": (
                        "APEX_DATABASE_URL / DATABASE_URL is not configured. "
                        "Worker requires PostgreSQL Outbox to claim jobs."
                    ),
                }
            ),
            file=sys.stderr,
        )
        return 2

    runtime, db_adapter = build_production_worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:
            pass

    try:
        if args.mode == "once":
            summary = await runtime.run_once(batch_size=args.batch_size)
            print(json.dumps(summary.__dict__, indent=2))
            return 0
        await runtime.run_loop(batch_size=args.batch_size)
        return 0
    finally:
        await db_adapter.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="APEX ONE Background Outbox Worker")
    parser.add_argument(
        "--mode",
        choices=("once", "loop"),
        default="once",
        help="Run a single recovery/claim tick or continuous polling loop.",
    )
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--dry-check",
        action="store_true",
        help="Validate worker configuration and exit without connecting.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
