"""Durable workflow API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apex_one.api.dependencies import (
    AppContainer,
    get_container,
    get_workflow_store,
    require_tenant_id,
)
from apex_one.db.adapter import DatabaseNotConfiguredError
from apex_one.db.repositories import WorkflowStoreProtocol

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


class CreateWorkflowRequest(BaseModel):
    workflow_type: str = Field(min_length=2, max_length=120)
    idempotency_key: str = Field(min_length=4, max_length=190)
    input_payload: dict[str, Any] = Field(default_factory=dict)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: CreateWorkflowRequest,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    store: WorkflowStoreProtocol = Depends(get_workflow_store),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    try:
        workflow, created_new = await store.create_or_get_workflow(
            tenant_id=tenant_id,
            workflow_type=body.workflow_type,
            idempotency_key=body.idempotency_key,
            input_payload=body.input_payload,
            max_attempts=container.settings.worker_max_attempts,
        )
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "POSTGRESQL_NOT_CONFIGURED", "message": str(exc)},
        ) from exc

    return {"workflow": workflow, "created": created_new}


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    store: WorkflowStoreProtocol = Depends(get_workflow_store),
) -> dict[str, Any]:
    try:
        workflow = await store.get_workflow(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
        )
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "POSTGRESQL_NOT_CONFIGURED", "message": str(exc)},
        ) from exc

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return {"workflow": workflow}


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(
    workflow_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    store: WorkflowStoreProtocol = Depends(get_workflow_store),
) -> dict[str, Any]:
    try:
        workflow = await store.request_cancel_workflow(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
        )
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "POSTGRESQL_NOT_CONFIGURED", "message": str(exc)},
        ) from exc

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return {"workflow": workflow}
