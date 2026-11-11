"""Dependency container and FastAPI providers for APEX ONE."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from apex_one.cache.redis_adapter import CacheAdapter, build_cache_adapter
from apex_one.config import Settings, get_settings
from apex_one.db.adapter import PostgresConnectionAdapter
from apex_one.db.platform_persistence import PostgresPlatformPersistence
from apex_one.db.repositories import (
    KnowledgeVectorStoreProtocol,
    SqlAlchemyKnowledgeVectorStore,
    SqlAlchemyWorkflowStore,
    WorkflowStoreProtocol,
)
from apex_one.local_node_gateway.protocol import (
    DEFAULT_DEV_NODE_SECRET,
    LocalNodeGatewayRegistry,
)
from apex_one.platform.async_runner import AsyncRunCoordinator
from apex_one.platform.security import SlidingWindowRateLimiter
from apex_one.platform.workspace_service import ApexPlatformService
from apex_one.sandbox.adapter import ExperimentSandboxAdapter, build_sandbox_adapter


@dataclass
class AppContainer:
    settings: Settings
    db_adapter: PostgresConnectionAdapter
    workflow_store: WorkflowStoreProtocol
    vector_store: KnowledgeVectorStoreProtocol
    cache_adapter: CacheAdapter
    sandbox_adapter: ExperimentSandboxAdapter
    local_node_registry: LocalNodeGatewayRegistry
    platform: ApexPlatformService
    platform_persistence: PostgresPlatformPersistence
    async_runner: AsyncRunCoordinator
    rate_limiter: SlidingWindowRateLimiter


def build_app_container(
    settings: Settings | None = None,
    *,
    seed_demo: bool = True,
    max_rpm: int = 600,
) -> AppContainer:
    cfg = settings or get_settings()
    db_adapter = PostgresConnectionAdapter(cfg)
    node_secret = (
        cfg.local_node_shared_secret.get_secret_value()
        if cfg.local_node_shared_secret
        else DEFAULT_DEV_NODE_SECRET
    )
    return AppContainer(
        settings=cfg,
        db_adapter=db_adapter,
        workflow_store=SqlAlchemyWorkflowStore(db_adapter),
        vector_store=SqlAlchemyKnowledgeVectorStore(db_adapter),
        cache_adapter=build_cache_adapter(cfg),
        sandbox_adapter=build_sandbox_adapter(cfg),
        local_node_registry=LocalNodeGatewayRegistry(
            shared_secret=node_secret,
            arena_isolated_mode=cfg.arena_isolated_mode,
        ),
        platform=ApexPlatformService(seed_demo=seed_demo),
        platform_persistence=PostgresPlatformPersistence(db_adapter),
        async_runner=AsyncRunCoordinator(),
        rate_limiter=SlidingWindowRateLimiter(
            max_requests=max_rpm, window_seconds=60
        ),
    )


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


def get_workflow_store(request: Request) -> WorkflowStoreProtocol:
    return get_container(request).workflow_store


def get_vector_store(request: Request) -> KnowledgeVectorStoreProtocol:
    return get_container(request).vector_store


def get_sandbox_adapter(request: Request) -> ExperimentSandboxAdapter:
    return get_container(request).sandbox_adapter


def require_tenant_id(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> uuid.UUID:
    if not x_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="Missing required X-Tenant-Id UUID header for RLS tenant context.",
        )
    try:
        return uuid.UUID(x_tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid X-Tenant-Id header: expected canonical UUID.",
        ) from exc
