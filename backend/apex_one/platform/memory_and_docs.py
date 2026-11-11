"""Shared Memory, Document Extraction (PDF/DOCX/MD/TXT), Chunking & Hybrid Retrieval."""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
from typing import Any, Literal

from docx import Document as DocxDocument
from pypdf import PdfReader

from apex_one.db.models import DEFAULT_EMBEDDING_DIM

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
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
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


def rank_memory_chunks(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Hybrid semantic (cosine) + full-text lexical retrieval returning source citations."""
    q_vec = embed_text_1536(query)
    scored: list[tuple[float, dict[str, Any]]] = []

    for chunk in candidates:
        sem = cosine_similarity(q_vec, chunk["embedding"])
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
                    "metadata": chunk.get("metadata", {}),
                },
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:top_k]]
