"""pgvector semantic search API endpoint."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from apex_one.api.dependencies import get_vector_store, require_tenant_id
from apex_one.db.adapter import DatabaseNotConfiguredError
from apex_one.db.models import DEFAULT_EMBEDDING_DIM
from apex_one.db.repositories import KnowledgeVectorStoreProtocol
from apex_one.db.vector import EmbeddingDimensionError, validate_vector

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-pgvector"])


class VectorSearchRequest(BaseModel):
    query_vector: list[float]
    project_id: uuid.UUID | None = None
    limit: int = Field(default=5, ge=1, le=50)


@router.post("/search")
async def search_knowledge(
    body: VectorSearchRequest,
    tenant_id: uuid.UUID = Depends(require_tenant_id),
    store: KnowledgeVectorStoreProtocol = Depends(get_vector_store),
) -> dict[str, object]:
    try:
        checked = validate_vector(
            body.query_vector, expected_dim=DEFAULT_EMBEDDING_DIM
        )
        hits = await store.similarity_search(
            tenant_id=tenant_id,
            query_vector=checked,
            project_id=body.project_id,
            limit=body.limit,
        )
        return {"hits": [asdict(h) for h in hits], "count": len(hits)}
    except EmbeddingDimensionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "POSTGRESQL_NOT_CONFIGURED", "message": str(exc)},
        ) from exc
