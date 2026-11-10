"""pgvector query builder and embedding contracts for APEX ONE."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from apex_one.db.models import DEFAULT_EMBEDDING_DIM, KnowledgeChunk


class EmbeddingDimensionError(ValueError):
    """Raised when an embedding vector does not match the configured pgvector dimension."""


class EmbeddingProvider(Protocol):
    """Contract for generating semantic embeddings for pgvector persistence."""

    @property
    def dimensions(self) -> int: ...

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class VectorSimilarityHit:
    chunk_id: uuid.UUID
    project_id: uuid.UUID
    source_uri: str
    content: str
    cosine_distance: float
    similarity_score: float
    metadata: dict[str, Any]


def validate_vector(
    vector: Sequence[float],
    *,
    expected_dim: int = DEFAULT_EMBEDDING_DIM,
) -> list[float]:
    """Validate dimensionality and finiteness before passing to pgvector."""
    if len(vector) != expected_dim:
        raise EmbeddingDimensionError(
            f"Expected {expected_dim}-d vector, got {len(vector)}-d."
        )
    normalized: list[float] = []
    for idx, val in enumerate(vector):
        fval = float(val)
        if not math.isfinite(fval):
            raise EmbeddingDimensionError(
                f"Non-finite float value at index {idx} in embedding vector."
            )
        normalized.append(fval)
    return normalized


def build_cosine_similarity_query(
    query_vector: Sequence[float],
    *,
    project_id: uuid.UUID | None = None,
    limit: int = 10,
    max_cosine_distance: float | None = None,
    expected_dim: int = DEFAULT_EMBEDDING_DIM,
) -> Select[tuple[KnowledgeChunk, float]]:
    """Build a PostgreSQL pgvector `<=>` cosine distance query."""
    checked = validate_vector(query_vector, expected_dim=expected_dim)
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")

    distance_expr = KnowledgeChunk.embedding.cosine_distance(checked).label(
        "cosine_distance"
    )
    stmt = select(KnowledgeChunk, distance_expr)
    if project_id is not None:
        stmt = stmt.where(KnowledgeChunk.project_id == project_id)
    if max_cosine_distance is not None:
        stmt = stmt.where(distance_expr <= max_cosine_distance)
    return stmt.order_by(distance_expr.asc()).limit(limit)


async def search_knowledge_chunks(
    session: AsyncSession,
    query_vector: Sequence[float],
    *,
    project_id: uuid.UUID | None = None,
    limit: int = 10,
    max_cosine_distance: float | None = None,
) -> list[VectorSimilarityHit]:
    """Execute a semantic similarity search within the active RLS-scoped session."""
    stmt = build_cosine_similarity_query(
        query_vector,
        project_id=project_id,
        limit=limit,
        max_cosine_distance=max_cosine_distance,
    )
    result = await session.execute(stmt)
    hits: list[VectorSimilarityHit] = []
    for chunk, distance in result.all():
        dist = float(distance)
        hits.append(
            VectorSimilarityHit(
                chunk_id=chunk.id,
                project_id=chunk.project_id,
                source_uri=chunk.source_uri,
                content=chunk.content,
                cosine_distance=dist,
                similarity_score=max(0.0, 1.0 - dist),
                metadata=dict(chunk.metadata_json or {}),
            )
        )
    return hits
