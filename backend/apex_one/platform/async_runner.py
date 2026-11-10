"""Asynchronous Background Multi-Agent Run Coordinator & Live SSE Event Bus.

Executes 8-agent research DAG runs step-by-step in background `asyncio.Task`s:
- Never blocks the FastAPI HTTP request/response cycle during long LLM runs.
- Checkpoints every completed step (`checkpoint_json` + `tool_ledger`) and flushes
  to PostgreSQL (`PostgresPlatformPersistence`) after each step.
- Streams real-time SSE events (`run.started`, `step.completed`, `run.completed`,
  `run.cancelled`, `run.failed`) to live connected Workbench clients.
- Honors mid-flight cancellation (`/cancel`) and organization `Emergency Stop`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from apex_one.db.base import utcnow

if TYPE_CHECKING:
    from apex_one.db.platform_persistence import PostgresPlatformPersistence
    from apex_one.platform.workspace_service import ApexPlatformService


class AsyncRunCoordinator:
    """Manages background DAG execution tasks and live per-run SSE subscriber queues."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        self._event_history: dict[str, list[dict[str, Any]]] = {}

    def publish_event(self, run_id: str, payload: dict[str, Any]) -> None:
        history = self._event_history.setdefault(run_id, [])
        history.append(payload)
        for q in list(self._subscribers.get(run_id, set())):
            q.put_nowait(payload)

    def close_stream(self, run_id: str) -> None:
        for q in list(self._subscribers.get(run_id, set())):
            q.put_nowait(None)

    def schedule_run(
        self,
        *,
        svc: ApexPlatformService,
        persistence: PostgresPlatformPersistence | None,
        organization_id: str,
        user_id: str,
        run_id: str,
        step_delay_seconds: float = 0.08,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(
            self._execute_step_by_step(
                svc=svc,
                persistence=persistence,
                organization_id=organization_id,
                user_id=user_id,
                run_id=run_id,
                step_delay_seconds=step_delay_seconds,
            )
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return task

    async def _execute_step_by_step(
        self,
        *,
        svc: ApexPlatformService,
        persistence: PostgresPlatformPersistence | None,
        organization_id: str,
        user_id: str,
        run_id: str,
        step_delay_seconds: float,
    ) -> None:
        run = svc.agent_runs.get(run_id)
        if not run:
            self.close_stream(run_id)
            return

        total_steps = 1 if run.get("execution_mode") == "single_agent" else 8
        self.publish_event(
            run_id,
            {
                "event": "run.started",
                "run_id": run_id,
                "objective": run["objective"],
                "total_steps": total_steps,
                "timestamp": utcnow().isoformat(),
            },
        )

        try:
            for step_limit in range(1, total_steps + 1):
                current = svc.agent_runs.get(run_id)
                org = svc.organizations.get(organization_id, {})
                if not current or current.get("status") == "CANCELLED" or org.get("emergency_stop_active"):
                    if current:
                        current["status"] = "CANCELLED"
                        current["completed_at"] = utcnow().isoformat()
                    self.publish_event(
                        run_id,
                        {
                            "event": "run.cancelled",
                            "run_id": run_id,
                            "status": "CANCELLED",
                            "timestamp": utcnow().isoformat(),
                        },
                    )
                    if persistence is not None:
                        await persistence.sync_to_postgres(svc)
                    return

                # Execute next single DAG step off the event loop so HTTP/LLM calls never block
                updated = await asyncio.to_thread(
                    svc.advance_run_one_step,
                    organization_id=organization_id,
                    user_id=user_id,
                    run_id=run_id,
                    step_limit=step_limit,
                    total_steps=total_steps,
                )

                completed_steps = [
                    s for s in updated.get("steps", []) if s.get("status") == "SUCCEEDED"
                ]
                latest_step = completed_steps[-1] if completed_steps else None
                if latest_step:
                    self.publish_event(
                        run_id,
                        {
                            "event": "step.completed",
                            "run_id": run_id,
                            "step_index": latest_step["step_index"],
                            "agent": latest_step["agent_name"],
                            "role": latest_step["agent_role"],
                            "task": latest_step["task_title"],
                            "status": latest_step["status"],
                            "tokens_used": latest_step["tokens_used"],
                            "progress_pct": updated.get("progress_pct", 0),
                            "live_llm_invoked": bool(
                                (latest_step.get("output_json") or {}).get(
                                    "live_llm_invoked", False
                                )
                            ),
                        },
                    )

                if persistence is not None:
                    await persistence.sync_to_postgres(svc)

                if step_limit < total_steps and step_delay_seconds > 0:
                    await asyncio.sleep(step_delay_seconds)

            final_run = svc.agent_runs.get(run_id, {})
            self.publish_event(
                run_id,
                {
                    "event": "run.completed",
                    "run_id": run_id,
                    "status": final_run.get("status", "COMPLETED"),
                    "tokens_used": final_run.get("tokens_used", 0),
                    "cost_usd": final_run.get("cost_usd", 0.0),
                    "contradictions_detected": (
                        final_run.get("output_json") or {}
                    ).get("contradictions_detected", 0),
                },
            )
            if persistence is not None:
                await persistence.sync_to_postgres(svc)
        except Exception as exc:
            if run_id in svc.agent_runs:
                svc.agent_runs[run_id]["status"] = "FAILED"
                svc.agent_runs[run_id]["error_message"] = str(exc)
            self.publish_event(
                run_id,
                {
                    "event": "run.failed",
                    "run_id": run_id,
                    "status": "FAILED",
                    "error": str(exc),
                },
            )
            if persistence is not None:
                await persistence.sync_to_postgres(svc)
        finally:
            self.close_stream(run_id)

    async def stream_events(
        self,
        *,
        svc: ApexPlatformService,
        run_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield historical + live real-time SSE events until run reaches terminal state."""
        run = svc.agent_runs.get(run_id)
        if not run:
            return

        # If run is currently executing in background, subscribe to live queue
        if run_id in self._tasks or run.get("status") in {"QUEUED", "RUNNING"}:
            q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            self._subscribers.setdefault(run_id, set()).add(q)
            try:
                for past in list(self._event_history.get(run_id, [])):
                    yield past
                while True:
                    item = await asyncio.wait_for(q.get(), timeout=120.0)
                    if item is None:
                        break
                    yield item
            except TimeoutError:
                pass
            finally:
                self._subscribers.get(run_id, set()).discard(q)
            return

        # Completed / interrupted run: replay full deterministic trace
        yield {
            "event": "run.started",
            "run_id": run_id,
            "objective": run["objective"],
            "total_steps": len(run.get("steps") or []),
        }
        for s in run.get("steps") or []:
            yield {
                "event": "step.completed",
                "run_id": run_id,
                "step_index": s["step_index"],
                "agent": s["agent_name"],
                "role": s["agent_role"],
                "task": s["task_title"],
                "status": s["status"],
                "tokens_used": s["tokens_used"],
                "live_llm_invoked": bool(
                    (s.get("output_json") or {}).get("live_llm_invoked", False)
                ),
            }
        yield {
            "event": "run.completed",
            "run_id": run_id,
            "status": run["status"],
            "tokens_used": run["tokens_used"],
            "cost_usd": run["cost_usd"],
        }
