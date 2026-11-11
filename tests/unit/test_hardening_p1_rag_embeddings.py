"""HARDENING 1.0 — Stage 5 (P1): Embedding Space Consistency & Non-Destructive RAG Reindexing Tests.

Covers all 8 mandatory verification scenarios:
1. Every memory & chunk persists explicit 7-field `EmbeddingSpaceMetadata`
2. Matching index + query embedding model/provider/version/dimensions succeeds cleanly
3. Mismatched `embedding_model` or `embedding_version` fails safely (`EmbeddingSpaceMismatchError` / HTTP 409)
4. Mismatched `embedding_dimensions` fails safely (`EmbeddingDimensionMismatchError`)
5. Local feature-hashing (`local-deterministic`) is strictly isolated from OpenAI/Ollama semantic spaces (even at equal 1536-d; zero silent fallback)
6. Silent vector padding / tiled repetition (`768 -> 1536`) and truncation (`3072 -> 1536`) are forbidden
7. Non-destructive reindexing (`reindex_organization_memories`) migrates vectors cleanly while preserving document content & generation history
8. Mid-reindex provider failure leaves existing vectors and index metadata 100% intact
"""

from __future__ import annotations

import math

import httpx
import pytest
from fastapi.testclient import TestClient

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.llm_gateway import LLMGateway
from apex_one.platform.memory_and_docs import (
    EmbeddingDimensionMismatchError,
    EmbeddingSpaceMetadata,
    EmbeddingSpaceMismatchError,
    assert_compatible_embedding_spaces,
    build_local_deterministic_space,
    embed_text_1536,
    rank_memory_chunks,
    validate_and_l2_normalize_vector,
)
from apex_one.platform.ssrf_guard import ValidatedEndpointPlan
from apex_one.platform.workspace_service import ApexPlatformService


def _build_openai_embedding_gateway(
    *,
    returned_dim: int = 1536,
    fail_on_call: int | None = None,
) -> LLMGateway:
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if fail_on_call is not None and calls >= fail_on_call:
            return httpx.Response(503, json={"error": "upstream unavailable"})
        vec = [1.0 / math.sqrt(returned_dim)] * returned_dim
        return httpx.Response(200, json={"data": [{"embedding": vec}]})

    def _transport_factory(_plan: ValidatedEndpointPlan) -> httpx.BaseTransport:
        return httpx.MockTransport(_handler)

    return LLMGateway(
        dns_resolver=lambda _host, _port: ["104.18.7.192"],
        custom_transport_factory=_transport_factory,
    )


def test_01_index_persists_complete_seven_field_embedding_metadata() -> None:
    """1. Every indexed memory object and chunk stores all 7 required metadata fields."""
    svc = ApexPlatformService(seed_demo=False)
    svc.register_user(
        email="rag-meta@example.org",
        password="RagMetaStrongPassword!2026",
        display_name="RAG Researcher",
        organization_name="RAG Lab",
    )
    login = svc.login(
        email="rag-meta@example.org", password="RagMetaStrongPassword!2026"
    )
    uid = login["user"]["id"]
    oid = login["active_organization_id"]

    mem = svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="research",
        title="GARCH Volatility Notes",
        content="Realized volatility clustering in 30-day BTC return series.",
        source_ref="arxiv://2401.00001",
    )

    required_keys = {
        "embedding_model",
        "embedding_provider",
        "embedding_version",
        "embedding_dimensions",
        "normalization_method",
        "distance_metric",
        "created_at",
    }
    assert required_keys.issubset(mem["embedding_index_metadata"].keys())
    chunks = svc.memory_chunks[mem["id"]]
    assert len(chunks) >= 1
    for ch in chunks:
        assert required_keys.issubset(ch["embedding_index_metadata"].keys())
        assert ch["embedding_index_metadata"]["embedding_provider"] == "local-deterministic"
        assert ch["embedding_index_metadata"]["embedding_dimensions"] == 1536
        assert ch["embedding_index_metadata"]["normalization_method"] == "l2_unit_norm"
        assert ch["embedding_index_metadata"]["distance_metric"] == "cosine"


def test_02_same_model_and_space_queries_succeed_accurately() -> None:
    """2. Querying an index with identical `EmbeddingSpaceMetadata` succeeds and ranks relevant chunks."""
    svc = ApexPlatformService(seed_demo=False)
    svc.register_user(
        email="rag-match@example.org",
        password="RagMatchStrongPassword!2026",
        display_name="Match User",
        organization_name="Match Org",
    )
    login = svc.login(
        email="rag-match@example.org", password="RagMatchStrongPassword!2026"
    )
    uid, oid = login["user"]["id"], login["active_organization_id"]

    svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="research",
        title="Drawdown Tail Risk",
        content="Conditional Value-at-Risk and maximum drawdown under Monte Carlo jump diffusion.",
        source_ref="paper://tail-risk",
    )
    svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="research",
        title="Protein Folding",
        content="Ribosomal polypeptide chain secondary alpha-helix folding kinetics.",
        source_ref="paper://biology",
    )

    hits = svc.search_memories(
        user_id=uid,
        organization_id=oid,
        query="maximum drawdown Monte Carlo tail risk",
        top_k=5,
    )
    assert len(hits) == 2
    assert hits[0]["memory_title"] == "Drawdown Tail Risk"
    assert hits[0]["embedding_index_metadata"]["embedding_model"] == "blake2b-signed-4gram"


def test_03_mismatched_embedding_model_or_version_raises_safe_error() -> None:
    """3. Querying chunks from a different model or version raises `EmbeddingSpaceMismatchError` (HTTP 409)."""
    space_v1 = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
        embedding_version="v1",
        embedding_dimensions=1536,
    )
    space_v2 = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
        embedding_version="v2",
        embedding_dimensions=1536,
    )
    space_other_model = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-large",
        embedding_provider="openai",
        embedding_version="v1",
        embedding_dimensions=1536,
    )

    with pytest.raises(EmbeddingSpaceMismatchError, match="Incompatible embedding spaces"):
        assert_compatible_embedding_spaces(space_v1, space_v2)

    with pytest.raises(EmbeddingSpaceMismatchError, match="Incompatible embedding spaces"):
        assert_compatible_embedding_spaces(space_v1, space_other_model)

    # Also verify HTTP API returns 409 EMBEDDING_SPACE_MISMATCH when index contains mismatched space
    app = create_app(Settings(env="test", enable_demo_seed=False))
    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "http-rag@example.org",
                "password": "HttpRagStrongPassword!2026",
                "display_name": "HTTP RAG",
                "organization_name": "HTTP RAG Org",
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": "http-rag@example.org",
                "password": "HttpRagStrongPassword!2026",
            },
        )
        csrf = login_resp.json()["csrf_token"]
        oid = login_resp.json()["active_organization_id"]
        headers = {"X-CSRF-Token": csrf, "X-Organization-Id": oid}

        created = client.post(
            "/api/v1/memories",
            headers=headers,
            json={
                "memory_type": "research",
                "title": "Test Doc",
                "content": "Empirical econometric variance ratio test.",
            },
        )
        assert created.status_code == 201
        mid = created.json()["id"]

        # Simulate stale unmigrated chunk from an older provider in the index
        svc: ApexPlatformService = app.state.container.platform
        svc.memory_chunks[mid][0]["embedding_index_metadata"] = space_v1.to_dict()

        mismatch_resp = client.get(
            "/api/v1/memories/search",
            params={"q": "variance ratio"},
            headers=headers,
        )
        assert mismatch_resp.status_code == 409
        err = mismatch_resp.json().get("error") or mismatch_resp.json().get("detail")
        assert err["code"] == "EMBEDDING_SPACE_MISMATCH"


def test_04_mismatched_dimensions_rejected_at_index_and_query_time() -> None:
    """4. Different `embedding_dimensions` (`768` vs `1536`) raises `EmbeddingDimensionMismatchError`."""
    ollama_768 = EmbeddingSpaceMetadata(
        embedding_model="nomic-embed-text",
        embedding_provider="ollama",
        embedding_version="v1",
        embedding_dimensions=768,
    )
    local_1536 = build_local_deterministic_space(dimensions=1536)

    with pytest.raises(EmbeddingDimensionMismatchError, match="dimension mismatch"):
        assert_compatible_embedding_spaces(ollama_768, local_1536)

    candidate = {
        "id": "ch-768",
        "memory_id": "m-1",
        "memory_type": "research",
        "memory_title": "768d Doc",
        "chunk_index": 0,
        "content": "Nomic 768-d chunk",
        "source_citation": "ref#1",
        "embedding": [0.1] * 768,
        "embedding_index_metadata": ollama_768.to_dict(),
    }
    with pytest.raises(EmbeddingDimensionMismatchError):
        rank_memory_chunks("query", [candidate], query_space=local_1536)


def test_05_local_feature_hashing_never_mixed_with_openai_or_ollama_vectors() -> None:
    """5. Local feature-hashing (`embed_text_1536`) is strictly isolated from OpenAI/Ollama even at 1536-d."""
    openai_1536 = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
        embedding_version="v1",
        embedding_dimensions=1536,
    )
    local_1536 = build_local_deterministic_space(dimensions=1536)

    # Even though BOTH are 1536-d cosine L2-normalized, mixing them is strictly forbidden:
    with pytest.raises(EmbeddingSpaceMismatchError):
        assert_compatible_embedding_spaces(openai_1536, local_1536)

    openai_chunk = {
        "id": "ch-oai",
        "memory_id": "m-oai",
        "memory_type": "research",
        "memory_title": "OpenAI Indexed Doc",
        "chunk_index": 0,
        "content": "Semantic dense transformer representation.",
        "source_citation": "oai#1",
        "embedding": embed_text_1536("placeholder 1536 vector"),
        "embedding_index_metadata": openai_1536.to_dict(),
    }
    # Attempting to rank an OpenAI-indexed chunk with default local feature-hashing fails loudly:
    with pytest.raises(EmbeddingSpaceMismatchError, match="Incompatible embedding spaces"):
        rank_memory_chunks("transformer", [openai_chunk])

    # Attempting `embed_with_space_contract` for `openai` without an API key NEVER silently falls back to hash!
    gw = LLMGateway()
    with pytest.raises(EmbeddingSpaceMismatchError, match="refusing to substitute|OPENAI_API_KEY"):
        gw.embed_with_space_contract(
            "test query",
            organization_id="org-no-key",
            providers_catalog=[
                {
                    "slug": "openai",
                    "category": "cloud",
                    "base_url": "https://api.openai.com/v1",
                    "enabled": True,
                }
            ],
            target_space=openai_1536,
        )


def test_06_silent_vector_padding_tiling_and_truncation_are_forbidden() -> None:
    """6. Neither 768-d (padding/tiling) nor 3072-d (truncation) vectors are coerced into 1536-d."""
    with pytest.raises(EmbeddingDimensionMismatchError, match="Refusing to pad or truncate"):
        validate_and_l2_normalize_vector([0.5] * 768, expected_dimensions=1536)

    with pytest.raises(EmbeddingDimensionMismatchError, match="Refusing to pad or truncate"):
        validate_and_l2_normalize_vector([0.5] * 3072, expected_dimensions=1536)

    # Also verify LLMGateway rejects upstream provider returning wrong dimensionality (768 instead of 1536)
    gw_bad_dim = _build_openai_embedding_gateway(returned_dim=768)
    gw_bad_dim.set_runtime_secret(
        organization_id="org-dim", provider_slug="openai", api_key="sk-dim-test-123456"
    )
    with pytest.raises(EmbeddingDimensionMismatchError, match="expected 1536-d, got 768-d"):
        gw_bad_dim.embed_text_1536_with_live_provider(
            "check upstream dimension enforcement",
            organization_id="org-dim",
            providers_catalog=[
                {
                    "slug": "openai",
                    "category": "cloud",
                    "base_url": "https://api.openai.com/v1",
                    "enabled": True,
                }
            ],
        )


def test_07_non_destructive_reindexing_migrates_tenant_space_cleanly() -> None:
    """7. `reindex_organization_memories` migrates all vectors into new space while preserving content & history."""
    gw = _build_openai_embedding_gateway(returned_dim=1536)
    svc = ApexPlatformService(seed_demo=False, llm_gateway=gw)
    svc.register_user(
        email="reindex-ok@example.org",
        password="ReindexOkStrongPassword!2026",
        display_name="Reindex Owner",
        organization_name="Reindex Lab",
    )
    login = svc.login(
        email="reindex-ok@example.org", password="ReindexOkStrongPassword!2026"
    )
    uid, oid = login["user"]["id"], login["active_organization_id"]

    svc.configure_model_provider_secret(
        user_id=uid,
        organization_id=oid,
        provider_slug="openai",
        api_key="sk-reindex-mock-key-123456",
        base_url="https://api.openai.com/v1",
    )

    created = svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="document",
        title="Original Local-Indexed Document",
        content="Stochastic volatility and regime switching in high-frequency markets.",
        source_ref="doi://10.1000/xyz123",
    )
    mid = created["id"]
    assert svc.memories[mid]["embedding_index_metadata"]["embedding_provider"] == "local-deterministic"

    target_openai = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
        embedding_version="v1",
        embedding_dimensions=1536,
    )
    report = svc.reindex_organization_memories(
        user_id=uid,
        organization_id=oid,
        target_space=target_openai,
    )
    assert report["memories_reindexed"] == 1
    assert report["chunks_reindexed"] >= 1
    assert svc.get_active_embedding_space(oid).space_id == target_openai.space_id

    # Original content & non-destructive generation history are preserved
    updated_mem = svc.memories[mid]
    assert updated_mem["content"] == "Stochastic volatility and regime switching in high-frequency markets."
    assert updated_mem["version"] == 2
    assert len(updated_mem["previous_index_generations"]) == 1
    assert (
        updated_mem["previous_index_generations"][0]["embedding_index_metadata"][
            "embedding_provider"
        ]
        == "local-deterministic"
    )

    # Searching now seamlessly embeds the query in `openai:text-embedding-3-small` and succeeds!
    hits = svc.search_memories(
        user_id=uid,
        organization_id=oid,
        query="regime switching volatility",
    )
    assert len(hits) == 1
    assert hits[0]["embedding_index_metadata"]["embedding_provider"] == "openai"


def test_08_failed_reindex_rolls_back_atomically_without_corrupting_existing_index() -> None:
    """8. If provider fails mid-reindexing, existing vectors, versions, and active space remain untouched."""
    gw = _build_openai_embedding_gateway(returned_dim=1536, fail_on_call=2)
    svc = ApexPlatformService(seed_demo=False, llm_gateway=gw)
    svc.register_user(
        email="reindex-rollback@example.org",
        password="ReindexRollbackPassword!2026",
        display_name="Rollback Owner",
        organization_name="Rollback Lab",
    )
    login = svc.login(
        email="reindex-rollback@example.org", password="ReindexRollbackPassword!2026"
    )
    uid, oid = login["user"]["id"], login["active_organization_id"]

    svc.configure_model_provider_secret(
        user_id=uid,
        organization_id=oid,
        provider_slug="openai",
        api_key="sk-rollback-mock-123456",
        base_url="https://api.openai.com/v1",
    )

    m1 = svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="research",
        title="Doc One",
        content="First research note that succeeds during staging.",
        source_ref="doc://1",
    )
    m2 = svc.create_memory_object(
        user_id=uid,
        organization_id=oid,
        memory_type="research",
        title="Doc Two",
        content="Second research note where upstream OpenAI returns HTTP 503.",
        source_ref="doc://2",
    )
    orig_vec_m1 = list(svc.memory_chunks[m1["id"]][0]["embedding"])

    target_openai = EmbeddingSpaceMetadata(
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
        embedding_version="v1",
        embedding_dimensions=1536,
    )
    with pytest.raises(EmbeddingSpaceMismatchError, match="failed"):
        svc.reindex_organization_memories(
            user_id=uid,
            organization_id=oid,
            target_space=target_openai,
        )

    # Active space, chunk vectors, and versions are 100% unchanged; search continues working!
    assert svc.get_active_embedding_space(oid).embedding_provider == "local-deterministic"
    assert svc.memories[m1["id"]]["version"] == 1
    assert svc.memories[m2["id"]]["version"] == 1
    assert svc.memory_chunks[m1["id"]][0]["embedding"] == orig_vec_m1

    hits = svc.search_memories(user_id=uid, organization_id=oid, query="First research note")
    assert len(hits) == 2
