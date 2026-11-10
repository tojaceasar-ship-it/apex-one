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

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apex_one import __version__
from apex_one.api.dependencies import AppContainer, build_app_container
from apex_one.api.platform_router import create_platform_router
from apex_one.api.routes import sandbox, system, vector, workflows
from apex_one.config import Settings, get_settings
from apex_one.local_node_gateway.router import build_local_node_router


def create_app(
    settings: Settings | None = None,
    *,
    seed_demo: bool = True,
    max_rpm: int = 600,
) -> FastAPI:
    cfg = settings or get_settings()
    container = build_app_container(cfg, seed_demo=seed_demo, max_rpm=max_rpm)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins_list,
        allow_origin_regex=r"^https://[0-9]+-[a-zA-Z0-9-]+\.e2b\.app$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
    )

    @app.middleware("http")
    async def request_id_and_rate_limit_middleware(
        request: Request, call_next: object
    ) -> JSONResponse:
        req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:16]}"
        request.state.request_id = req_id

        c: AppContainer = request.app.state.container
        client_key = (
            request.headers.get("Authorization")
            or (request.client.host if request.client else "local")
        )
        allowed, remaining = c.rate_limiter.check(client_key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"X-Request-ID": req_id, "X-RateLimit-Remaining": "0"},
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests; please retry shortly.",
                        "request_id": req_id,
                    }
                },
            )

        response = await call_next(request)  # type: ignore[operator]
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
