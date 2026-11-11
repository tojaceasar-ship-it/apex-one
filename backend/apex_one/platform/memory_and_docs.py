"""Shared Memory, Document Extraction (PDF/DOCX/MD/TXT), Chunking & Hybrid Retrieval."""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from docx import Document as DocxDocument
from pypdf import PdfReader

from apex_one.db.models import DEFAULT_EMBEDDING_DIM


class EmbeddingSpaceMismatchError(ValueError):
    """Raised when vectors from incompatible embedding models/providers/configs are compared or mixed."""


class EmbeddingDimensionMismatchError(EmbeddingSpaceMismatchError):
    """Raised when an embedding vector dimension does not match the index contract (padding/truncation forbidden)."""


@dataclass(frozen=True)
class EmbeddingSpaceMetadata:
    embedding_model: str
    embedding_provider: str
    embedding_version: str
    embedding_dimensions: int
    normalization_method: str = "l2_unit_norm"
    distance_metric: str = "cosine"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(UTC).isoformat(),
            )
        if self.embedding_dimensions <= 0:
            raise EmbeddingDimensionMismatchError(
                f"Invalid embedding_dimensions={self.embedding_dimensions}."
            )
        if not self.embedding_model.strip() or not self.embedding_provider.strip():
            raise EmbeddingSpaceMismatchError(
                "embedding_model and embedding_provider must be non-empty."
            )

    @property
    def space_id(self) -> str:
        return (
            f"{self.embedding_provider}:{self.embedding_model}:"
            f"{self.embedding_version}:{self.embedding_dimensions}:"
            f"{self.normalization_method}:{self.distance_metric}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "space_id": self.space_id}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EmbeddingSpaceMetadata:
        return cls(
            embedding_model=str(raw.get("embedding_model") or ""),
            embedding_provider=str(raw.get("embedding_provider") or ""),
            embedding_version=str(raw.get("embedding_version") or "v1"),
            embedding_dimensions=int(raw.get("embedding_dimensions") or 0),
            normalization_method=str(raw.get("normalization_method") or "l2_unit_norm"),
            distance_metric=str(raw.get("distance_metric") or "cosine"),
            created_at=str(raw.get("created_at") or ""),
        )


def build_local_deterministic_space(
    *,
    dimensions: int = DEFAULT_EMBEDDING_DIM,
    created_at: str = "",
) -> EmbeddingSpaceMetadata:
    return EmbeddingSpaceMetadata(
        embedding_model="blake2b-signed-4gram",
        embedding_provider="local-deterministic",
        embedding_version="v1",
        embedding_dimensions=dimensions,
        normalization_method="l2_unit_norm",
        distance_metric="cosine",
        created_at=created_at,
    )


def validate_and_l2_normalize_vector(
    raw_vec: list[float],
    *,
    expected_dimensions: int = DEFAULT_EMBEDDING_DIM,
) -> list[float]:
    """Validate exact dimensionality & finiteness and L2-normalize WITHOUT silent padding or truncation."""
    if not isinstance(raw_vec, list) or len(raw_vec) != expected_dimensions:
        got_dim = len(raw_vec) if isinstance(raw_vec, list) else -1
        raise EmbeddingDimensionMismatchError(
            f"Refusing to pad or truncate embedding vector: expected {expected_dimensions}-d, got {got_dim}-d."
        )
    cleaned: list[float] = []
    for idx, val in enumerate(raw_vec):
        fval = float(val)
        if not math.isfinite(fval):
            raise EmbeddingDimensionMismatchError(
                f"Non-finite value in embedding vector at index {idx}."
            )
        cleaned.append(fval)
    norm = math.sqrt(sum(v * v for v in cleaned))
    if norm <= 1e-12:
        raise EmbeddingDimensionMismatchError("Zero-norm embedding vector is invalid.")
    return [round(v / norm, 7) for v in cleaned]


def assert_compatible_embedding_spaces(
    index_space: EmbeddingSpaceMetadata | dict[str, Any],
    query_space: EmbeddingSpaceMetadata | dict[str, Any],
) -> None:
    """Verify two embedding spaces share identical provider, model, version, dimension, normalization & metric."""
    idx_meta = (
        index_space
        if isinstance(index_space, EmbeddingSpaceMetadata)
        else EmbeddingSpaceMetadata.from_dict(index_space)
    )
    qry_meta = (
        query_space
        if isinstance(query_space, EmbeddingSpaceMetadata)
        else EmbeddingSpaceMetadata.from_dict(query_space)
    )
    if idx_meta.embedding_dimensions != qry_meta.embedding_dimensions:
        raise EmbeddingDimensionMismatchError(
            f"Embedding dimension mismatch: index uses {idx_meta.embedding_dimensions}-d "
            f"('{idx_meta.embedding_provider}:{idx_meta.embedding_model}') while query uses "
            f"{qry_meta.embedding_dimensions}-d ('{qry_meta.embedding_provider}:{qry_meta.embedding_model}')."
        )
    if (
        idx_meta.embedding_provider != qry_meta.embedding_provider
        or idx_meta.embedding_model != qry_meta.embedding_model
        or idx_meta.embedding_version != qry_meta.embedding_version
        or idx_meta.normalization_method != qry_meta.normalization_method
        or idx_meta.distance_metric != qry_meta.distance_metric
    ):
        raise EmbeddingSpaceMismatchError(
            f"Incompatible embedding spaces: index '{idx_meta.space_id}' cannot be queried or mixed "
            f"with '{qry_meta.space_id}'. Run non-destructive reindexing before switching embedding models."
        )

MemoryType = Literal[
    "conversation",
    "research",
    "agent",
    "project",
    "document",
    "procedural",
    "experiment",
]

VALID_MEMORY_TYPES: frozenset[str] = frozenset(
    {
        "conversation",
        "research",
        "agent",
        "project",
        "document",
        "procedural",
        "experiment",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ0-9_]{2,}")


def tokenize_for_search(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(text)]


def embed_text_1536(text: str, *, dim: int = DEFAULT_EMBEDDING_DIM) -> list[float]:
    """Generate a deterministic L2-normalized 1536-d signed feature-hashed embedding.

    Preserves genuine cosine similarity across overlapping terminology and character
    4-grams without requiring paid third-party embedding API keys, while remaining
    100% compatible with PostgreSQL `pgvector` `VECTOR(1536)`.
    """
    vec = [0.0] * dim
    tokens = tokenize_for_search(text)
    features: list[tuple[str, float]] = []
    for tok in tokens:
        features.append((f"w:{tok}", 2.0))
        if len(tok) >= 4:
            for idx in range(len(tok) - 3):
                features.append((f"g:{tok[idx:idx + 4]}", 0.6))

    if not features:
        vec[0] = 1.0
        return vec

    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[bucket] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 7) for v in vec]


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if len(vec_a) != len(vec_b):
        raise EmbeddingDimensionMismatchError(
            f"Cannot compute cosine_similarity across mismatched dimensions ({len(vec_a)}-d vs {len(vec_b)}-d)."
        )
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    na = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    nb = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def lexical_overlap_score(query: str, passage: str) -> float:
    q_tokens = set(tokenize_for_search(query))
    if not q_tokens:
        return 0.0
    p_tokens = set(tokenize_for_search(passage))
    return len(q_tokens & p_tokens) / float(len(q_tokens))


def chunk_document_text(
    text: str,
    *,
    chunk_chars: int = 520,
    overlap_chars: int = 80,
) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_chars:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    step = max(64, chunk_chars - overlap_chars)
    while start < len(cleaned):
        slice_str = cleaned[start : start + chunk_chars].strip()
        if slice_str:
            chunks.append(slice_str)
        if start + chunk_chars >= len(cleaned):
            break
        start += step
    return chunks


def extract_document_text(
    *,
    source_type: str,
    raw_text: str | None = None,
    content_base64: str | None = None,
) -> tuple[str, bytes]:
    """Extract UTF-8 text and raw bytes from TXT, MD, PDF, or DOCX inputs."""
    stype = source_type.upper().strip()
    if stype in {"TXT", "MD", "URL"}:
        text = (raw_text or "").strip()
        raw_bytes = text.encode("utf-8")
        return text, raw_bytes

    if content_base64:
        raw_bytes = base64.b64decode(content_base64)
        if stype == "PDF":
            reader = PdfReader(io.BytesIO(raw_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(p.strip() for p in pages if p.strip()), raw_bytes
        if stype == "DOCX":
            doc = DocxDocument(io.BytesIO(raw_bytes))
            paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paras), raw_bytes

    # Fallback if text representation was directly provided alongside PDF/DOCX metadata
    if raw_text:
        b = raw_text.encode("utf-8")
        return raw_text.strip(), b

    raise ValueError(
        f"Provide raw_text (for TXT/MD/URL) or content_base64 (for PDF/DOCX); got source_type='{source_type}'."
    )


def _infer_legacy_chunk_space(chunk: dict[str, Any]) -> EmbeddingSpaceMetadata:
    explicit = chunk.get("embedding_index_metadata") or (chunk.get("metadata") or {}).get(
        "embedding_index_metadata"
    )
    if isinstance(explicit, dict) and explicit.get("embedding_model"):
        return EmbeddingSpaceMetadata.from_dict(explicit)

    vec = chunk.get("embedding") or []
    dim = len(vec) if isinstance(vec, list) and vec else DEFAULT_EMBEDDING_DIM
    backend = str((chunk.get("metadata") or {}).get("embedding_backend") or "deterministic-local-1536")
    if backend in {"deterministic-local-1536", "local-deterministic:blake2b-signed-4gram"}:
        return build_local_deterministic_space(dimensions=dim)
    if ":" in backend:
        prov, model = backend.split(":", 1)
        return EmbeddingSpaceMetadata(
            embedding_model=model,
            embedding_provider=prov,
            embedding_version="v1",
            embedding_dimensions=dim,
        )
    return EmbeddingSpaceMetadata(
        embedding_model=backend,
        embedding_provider="custom",
        embedding_version="v1",
        embedding_dimensions=dim,
    )


def rank_memory_chunks(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    top_k: int = 8,
    query_vector: list[float] | None = None,
    query_space: EmbeddingSpaceMetadata | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Hybrid semantic (cosine) + full-text lexical retrieval enforcing embedding space compatibility."""
    resolved_query_space = (
        (
            query_space
            if isinstance(query_space, EmbeddingSpaceMetadata)
            else EmbeddingSpaceMetadata.from_dict(query_space)
        )
        if query_space is not None
        else build_local_deterministic_space()
    )
    if query_vector is None:
        if resolved_query_space.embedding_provider != "local-deterministic":
            raise EmbeddingSpaceMismatchError(
                f"Query space '{resolved_query_space.space_id}' requires an explicit provider query_vector; "
                "refusing to substitute local feature-hashing vector."
            )
        q_vec = embed_text_1536(query, dim=resolved_query_space.embedding_dimensions)
    else:
        q_vec = validate_and_l2_normalize_vector(
            query_vector,
            expected_dimensions=resolved_query_space.embedding_dimensions,
        )

    scored: list[tuple[float, dict[str, Any]]] = []

    for chunk in candidates:
        chunk_space = _infer_legacy_chunk_space(chunk)
        assert_compatible_embedding_spaces(chunk_space, resolved_query_space)
        c_vec = chunk["embedding"]
        if len(c_vec) != resolved_query_space.embedding_dimensions:
            raise EmbeddingDimensionMismatchError(
                f"Chunk '{chunk.get('id')}' has {len(c_vec)}-d vector, expected {resolved_query_space.embedding_dimensions}-d."
            )
        sem = cosine_similarity(q_vec, c_vec)
        lex = lexical_overlap_score(query, chunk["content"])
        combined = 0.65 * max(0.0, sem) + 0.35 * lex
        scored.append(
            (
                combined,
                {
                    "chunk_id": chunk["id"],
                    "memory_id": chunk["memory_id"],
                    "memory_type": chunk["memory_type"],
                    "memory_title": chunk["memory_title"],
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "source_citation": chunk["source_citation"],
                    "semantic_score": round(max(0.0, sem), 4),
                    "fulltext_score": round(lex, 4),
                    "hybrid_score": round(combined, 4),
                    "embedding_index_metadata": chunk_space.to_dict(),
                    "metadata": chunk.get("metadata", {}),
                },
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:top_k]]
