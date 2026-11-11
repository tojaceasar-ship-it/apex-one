"""FastAPI Application Entrypoint for APEX ONE 1.0 (Full Research Edition).

Mounts:
- System health & honest Arena readiness (`/api/v1/health`, `/api/v1/environment/status`)
- PostgreSQL Outbox durable workflows (`/api/v1/workflows/*`)
- Guarded Experiment Sandbox (`/api/v1/sandbox/*`)
- pgvector similarity search (`/api/v1/knowledge/search`)
- Hybrid Local Node Gateway HTTP + WebSocket (`/api/v1/local-node/*`)
- Complete APEX ONE 1.0 Research, Agent, Memory, Experiment, Eval & Governance API (`/api/v1/*`)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apex_one import __version__
from apex_one.api.dependencies import AppContainer, build_app_container
from apex_one.api.platform_router import create_platform_router
from apex_one.api.routes import sandbox, system, vector, workflows
from apex_one.config import Settings, get_settings
from apex_one.local_node_gateway.router import build_local_node_router
from apex_one.platform.concurrency import (
    IdempotencyConflictError,
    StaleWriteConflictError,
    compute_request_payload_hash,
)
from apex_one.platform.security import extract_controlled_client_ip
from apex_one.platform.workspace_service import AuthorizationError

logger = logging.getLogger(__name__)


def _resolve_verified_rate_limit_principal(
    request: Request,
    container: AppContainer,
    controlled_ip: str,
) -> str:
    """Resolve rate-limit key ONLY from cryptographically verified session/API tokens or controlled IP.

    Unverified or spoofed `X-Apex-Session-Token` / `Authorization` headers are NEVER trusted.
    """
    apex_header = (request.headers.get("X-Apex-Session-Token") or "").strip()
    auth_header = (request.headers.get("Authorization") or "").strip()
    bearer_token = (
        auth_header[7:].strip()
        if auth_header.lower().startswith("bearer ")
        else ""
    )
    cookie_token = (request.cookies.get("apx_session") or "").strip()

    candidates: list[str] = []
    for cand in (apex_header, cookie_token, bearer_token):
        if cand and cand not in candidates:
            candidates.append(cand)

    if not candidates:
        return f"ip:{controlled_ip}"

    for cand in candidates:
        try:
            ctx = container.platform.authenticate_token(cand)
        except AuthorizationError:
            continue

        user_id = str(ctx["user"]["id"])
        default_org_id = str(ctx["default_organization_id"])
        requested_org = (
            (request.headers.get("X-Organization-Id") or "").strip()
            or default_org_id
        )
        try:
            container.platform.authorize(
                user_id=user_id,
                organization_id=requested_org,
                required_permission="read:all",
            )
            bound_org_id = requested_org
        except AuthorizationError:
            bound_org_id = default_org_id
        return f"auth:{bound_org_id}:{user_id}"

    # Every supplied auth header/cookie failed verification -> spoofed/expired credential!
    container.rate_limiter.record_spoofed_auth_ignored()
    return f"ip:{controlled_ip}"


def create_app(
    settings: Settings | None = None,
    *,
    seed_demo: bool | None = None,
    max_rpm: int | None = None,
    auth_max_rpm: int | None = None,
    expensive_max_rpm: int | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    container = build_app_container(
        cfg,
        seed_demo=seed_demo,
        max_rpm=max_rpm,
        auth_max_rpm=auth_max_rpm,
        expensive_max_rpm=expensive_max_rpm,
    )

    local_state_env = os.environ.get("APEX_LOCAL_STATE_FILE", "").strip()
    local_state_path = Path(local_state_env) if local_state_env else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container
        await container.platform_persistence.hydrate_or_seed(container.platform)
        if not container.platform_persistence.active and local_state_path is not None:
            container.platform.load_local_checkpoint(local_state_path)
        yield
        await container.cache_adapter.close()
        await container.db_adapter.dispose()

    app = FastAPI(
        title="APEX ONE API",
        version=__version__,
        description=(
            "APEX ONE 1.0 (Full Research Edition) — Multi-Agent AI Research, "
            "Evidence Graph, Experimentation, Evaluation & Governance Platform."
        ),
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.local_state_path = local_state_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins_list,
        allow_origin_regex=(
            r"^https://[a-zA-Z0-9-]+\.(e2b\.app|arena\.site)$"
            if (cfg.arena_isolated_mode and cfg.env not in {"staging", "production"})
            else None
        ),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Organization-Id",
            "X-Apex-Session-Token",
            "X-Request-ID",
            "Idempotency-Key",
            "If-Match",
            "X-Expected-Version",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Remaining",
            "X-Idempotent-Replay",
        ],
    )

    @app.middleware("http")
    async def request_id_and_rate_limit_middleware(
        request: Request, call_next: object
    ) -> JSONResponse:
        req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:16]}"
        request.state.request_id = req_id

        c: AppContainer = request.app.state.container
        peer_ip = request.client.host if request.client else "local"
        controlled_ip, ignored_xff = extract_controlled_client_ip(
            peer_ip=peer_ip,
            x_forwarded_for=request.headers.get("X-Forwarded-For"),
            trusted_proxies=c.settings.trusted_proxy_list,
        )
        if ignored_xff:
            c.rate_limiter.record_untrusted_xff_ignored()

        principal_key = _resolve_verified_rate_limit_principal(
            request, c, controlled_ip
        )
        allowed, remaining, blocked_tier = await c.rate_limiter.check_request_async(
            principal_key=principal_key,
            client_ip=controlled_ip,
            method=request.method,
            path=request.url.path,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={
                    "X-Request-ID": req_id,
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Tier": blocked_tier.value,
                },
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "tier": blocked_tier.value,
                        "message": "Too many requests; please retry shortly.",
                        "request_id": req_id,
                    }
                },
            )

        is_mutating_api = (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path.startswith("/api/v1/")
            and not request.url.path.startswith("/api/v1/lan-bridge/")
        )

        idem_header = (request.headers.get("Idempotency-Key") or "").strip()
        idem_org_id: str | None = None
        if (
            is_mutating_api
            and idem_header
            and principal_key.startswith("auth:")
        ):
            parts = principal_key.split(":", 2)
            idem_org_id = parts[1] if len(parts) >= 2 else None
            raw_body = await request.body()
            req_hash = compute_request_payload_hash(
                method=request.method,
                path=request.url.path,
                body_bytes=raw_body,
            )
            try:
                mode, idem_entry = c.platform.idempotency_ledger.begin_or_replay(
                    organization_id=idem_org_id,
                    idempotency_key=idem_header,
                    request_hash=req_hash,
                )
            except IdempotencyConflictError as exc:
                return JSONResponse(
                    status_code=409,
                    headers={"X-Request-ID": req_id},
                    content={
                        "detail": {
                            "code": "IDEMPOTENCY_KEY_CONFLICT",
                            "message": str(exc),
                        },
                        "error": {
                            "code": "IDEMPOTENCY_KEY_CONFLICT",
                            "message": str(exc),
                            "request_id": req_id,
                        },
                    },
                )
            except ValueError as exc:
                return JSONResponse(
                    status_code=400,
                    headers={"X-Request-ID": req_id},
                    content={
                        "detail": {"code": "VALIDATION_ERROR", "message": str(exc)},
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": str(exc),
                            "request_id": req_id,
                        },
                    },
                )

            if mode == "REPLAY":
                return JSONResponse(
                    status_code=int(idem_entry.status_code or 200),
                    content=idem_entry.response_json,
                    headers={
                        "X-Request-ID": req_id,
                        "X-RateLimit-Remaining": str(remaining),
                        "X-Idempotent-Replay": "true",
                    },
                )

        try:
            response = await call_next(request)  # type: ignore[operator]
        except Exception:
            if idem_org_id and idem_header:
                c.platform.idempotency_ledger.abort(
                    organization_id=idem_org_id, idempotency_key=idem_header
                )
            raise

        if idem_org_id and idem_header:
            if 200 <= response.status_code < 300:
                resp_chunks: list[bytes] = []
                async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                    resp_chunks.append(chunk)
                full_bytes = b"".join(resp_chunks)
                try:
                    parsed_resp = json.loads(full_bytes.decode("utf-8"))
                except Exception:
                    parsed_resp = {"raw": full_bytes.decode("utf-8", errors="replace")}
                c.platform.idempotency_ledger.complete(
                    organization_id=idem_org_id,
                    idempotency_key=idem_header,
                    status_code=response.status_code,
                    response_json=parsed_resp,
                )
                rebuilt_headers = dict(response.headers)
                rebuilt_headers["X-Idempotent-Replay"] = "false"
                response = Response(  # type: ignore[assignment]
                    content=full_bytes,
                    status_code=response.status_code,
                    headers=rebuilt_headers,
                    media_type=response.media_type,
                )
            else:
                c.platform.idempotency_ledger.abort(
                    organization_id=idem_org_id, idempotency_key=idem_header
                )

        if is_mutating_api and 200 <= response.status_code < 300:
            if c.platform_persistence.active:
                try:
                    await c.platform_persistence.sync_to_postgres(c.platform)
                except StaleWriteConflictError as exc:
                    return JSONResponse(
                        status_code=409,
                        headers={"X-Request-ID": req_id},
                        content={
                            "detail": {
                                "code": "STALE_WRITE_CONFLICT",
                                "message": str(exc),
                                "resource_type": exc.resource_type,
                                "resource_id": exc.resource_id,
                                "expected_version": exc.expected_version,
                                "actual_version": exc.actual_version,
                            },
                            "error": {
                                "code": "STALE_WRITE_CONFLICT",
                                "message": str(exc),
                                "request_id": req_id,
                            },
                        },
                    )
            elif local_state_path is not None:
                try:
                    c.platform.save_local_checkpoint(local_state_path)
                except Exception:
                    pass
        response.headers["X-Request-ID"] = req_id
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response  # type: ignore[no-any-return]

    @app.exception_handler(HTTPException)
    async def standardized_http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", f"req-{uuid.uuid4().hex[:12]}")
        if isinstance(exc.detail, dict):
            payload = {
                "detail": exc.detail,
                "error": {
                    "code": exc.detail.get("code", "HTTP_ERROR"),
                    "message": exc.detail.get("message", str(exc.detail)),
                    "request_id": req_id,
                },
            }
        else:
            payload = {
                "detail": exc.detail,
                "error": {
                    "code": f"HTTP_{exc.status_code}",
                    "message": str(exc.detail),
                    "request_id": req_id,
                },
            }
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(Exception)
    async def sanitized_unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        req_id = getattr(request.state, "request_id", f"req-{uuid.uuid4().hex[:12]}")
        logger.error(
            "Unhandled server exception [request_id=%s, type=%s]",
            req_id,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal error occurred.",
                },
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal error occurred.",
                    "request_id": req_id,
                },
            },
            headers={"X-Request-ID": req_id},
        )

    @app.get("/")
    async def root_info() -> dict[str, object]:
        return {
            "service": "apex-one-backend",
            "version": __version__,
            "edition": "Full Research Edition",
            "docs_url": "/docs",
            "health_url": "/api/v1/health",
            "environment_status_url": "/api/v1/environment/status",
        }

    app.include_router(system.router)
    app.include_router(workflows.router)
    app.include_router(sandbox.router)
    app.include_router(vector.router)
    app.include_router(build_local_node_router(container.local_node_registry))
    app.include_router(create_platform_router(lambda: app.state.container.platform))
    return app


app = create_app()
