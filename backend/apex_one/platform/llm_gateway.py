"""Production Multi-Provider LLM & Embedding Gateway for APEX ONE 1.0.

Supports:
- OpenAI & OpenAI-compatible HTTP endpoints (`/v1/chat/completions`, `/v1/embeddings`)
  including OpenRouter, vLLM, LiteLLM, and Azure/custom gateways.
- Anthropic Messages API (`/v1/messages`).
- Local / LAN Ollama (`/api/chat`, `/api/embed`).
- Automatic provider fallback chains (`fallback_provider_slug`), real token/cost
  accounting, runtime per-organization secret binding (never echoed in responses),
  and graceful fallback to the deterministic analytical engine when offline.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from apex_one.db.models import DEFAULT_EMBEDDING_DIM as EMBEDDING_DIM
from apex_one.integrations_hub import SSRFBlockedError
from apex_one.platform.cost_attribution import (
    PRICING_CATALOG_VERSION,
    CostCategory,
    CostStatus,
    compute_call_cost_quote,
)
from apex_one.platform.lan_llm_bridge import LanLLMBridgeBroker
from apex_one.platform.local_intelligence import redact_confidential_text
from apex_one.platform.memory_and_docs import (
    EmbeddingDimensionMismatchError,
    EmbeddingSpaceMetadata,
    EmbeddingSpaceMismatchError,
    build_local_deterministic_space,
    embed_text_1536,
    validate_and_l2_normalize_vector,
)
from apex_one.platform.ssrf_guard import (
    DNSResolverFn,
    EndpointTrustClass,
    execute_ssrf_guarded_request,
    normalize_origin,
    resolve_and_validate_url,
)


def classify_provider_trust_class(provider_cfg: dict[str, Any]) -> EndpointTrustClass:
    """Classify a model provider configuration into PUBLIC_PROVIDER vs CONTROLLED_LOCAL_MODEL."""
    slug = str(provider_cfg.get("slug") or "").lower()
    cat = str(provider_cfg.get("category") or "").lower()
    if cat in {"cloud", "commercial_cloud"} or slug.startswith(
        ("openai", "anthropic", "openrouter")
    ):
        return EndpointTrustClass.PUBLIC_PROVIDER
    if (
        cat in {"local", "local_ollama"}
        or slug in {"ollama", "ollama-local", "lmstudio-lan-qwen"}
        or slug.startswith(("ollama", "lmstudio"))
        or (cat == "compatible_http" and provider_cfg.get("auth_env_var") is None)
    ):
        return EndpointTrustClass.CONTROLLED_LOCAL_MODEL
    return EndpointTrustClass.PUBLIC_PROVIDER

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning_tags(text: str) -> str:
    """Strip Qwen3 / DeepSeek `<think>...</think>` blocks while preserving final answer."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    return cleaned if cleaned else text.strip()


@dataclass(frozen=True)
class LLMCompletionResult:
    content: str
    structured_json: dict[str, Any] | None
    provider_slug: str
    model_identifier: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: int
    live_llm_invoked: bool
    fallback_triggered: bool
    error_note: str | None = None
    cached_tokens: int = 0
    actual_cost_usd: float | None = None
    estimated_cost_usd: float | None = 0.0
    pricing_version: str = PRICING_CATALOG_VERSION
    cost_status: str = CostStatus.LOCAL_ZERO_API_COST.value
    cost_category: str = CostCategory.LOCAL_COMPUTE.value
    request_id: str | None = None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    match = _JSON_BLOCK_RE.search(stripped)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _normalize_to_1536(raw_vec: list[float]) -> list[float]:
    """Validate exact 1536-d vector and L2-normalize (padding/truncation strictly forbidden)."""
    return validate_and_l2_normalize_vector(
        raw_vec, expected_dimensions=EMBEDDING_DIM
    )


class LLMGateway:
    """Provider-neutral LLM & Embedding client with fallback and secret isolation."""

    def __init__(
        self,
        *,
        http_timeout: float = 90.0,
        lan_bridge: LanLLMBridgeBroker | None = None,
        dns_resolver: DNSResolverFn | None = None,
        custom_transport_factory: Any = None,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._timeout = http_timeout
        self.lan_bridge = lan_bridge or LanLLMBridgeBroker()
        self._dns_resolver = dns_resolver
        self._custom_transport_factory = custom_transport_factory
        self._max_response_bytes = max_response_bytes
        # Keyed by (organization_id, provider_slug) -> secret string (never serialized)
        self._runtime_secrets: dict[tuple[str, str], str] = {}
        # Tenant-isolated owner-trusted local origins: organization_id -> set[origin]
        self._owner_trusted_origins: dict[str, set[str]] = {}
        # Short-lived circuit breaker for unreachable private LAN hosts
        self._unreachable_until: dict[str, float] = {}

    def trust_local_origin(self, *, organization_id: str, url_or_origin: str) -> str:
        """Register an owner-authorized local model origin scoped strictly to `organization_id`."""
        origin = normalize_origin(url_or_origin)
        # Validate IP safety (always blocks cloud metadata, link-local, multicast, unspecified)
        resolve_and_validate_url(
            url_or_origin,
            trust_class=EndpointTrustClass.CONTROLLED_LOCAL_MODEL,
            owner_trusted_origins={origin},
            dns_resolver=self._dns_resolver,
        )
        self._owner_trusted_origins.setdefault(organization_id, set()).add(origin)
        return origin

    def get_owner_trusted_origins(self, organization_id: str) -> set[str]:
        trusted = set(self._owner_trusted_origins.get(organization_id, set()))
        # Host-owner process env vars for local Ollama daemon (still subject to metadata/bogon checks)
        for env_key in ("APEX_OLLAMA_URL", "OLLAMA_BASE_URL"):
            env_val = os.environ.get(env_key, "").strip()
            if env_val:
                trusted.add(normalize_origin(env_val))
        return trusted

    def set_runtime_secret(
        self,
        *,
        organization_id: str,
        provider_slug: str,
        api_key: str,
    ) -> dict[str, Any]:
        cleaned = api_key.strip()
        if not cleaned:
            self._runtime_secrets.pop((organization_id, provider_slug), None)
            return {"provider_slug": provider_slug, "configured": False, "masked": None}
        self._runtime_secrets[(organization_id, provider_slug)] = cleaned
        masked = f"***{cleaned[-4:]}" if len(cleaned) >= 6 else "***set"
        return {"provider_slug": provider_slug, "configured": True, "masked": masked}

    def resolve_api_key(
        self,
        *,
        organization_id: str,
        provider_cfg: dict[str, Any],
    ) -> str | None:
        slug = str(provider_cfg.get("slug") or "")
        runtime_val = self._runtime_secrets.get((organization_id, slug))
        if runtime_val:
            return runtime_val
        env_var = provider_cfg.get("auth_env_var")
        if env_var and os.environ.get(str(env_var)):
            return os.environ[str(env_var)].strip()
        if slug == "openai" and os.environ.get("OPENAI_API_KEY"):
            return os.environ["OPENAI_API_KEY"].strip()
        if slug == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            return os.environ["ANTHROPIC_API_KEY"].strip()
        if slug == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
            return os.environ["OPENROUTER_API_KEY"].strip()
        return None

    def has_credentials(
        self,
        organization_id: str,
        provider_cfg: dict[str, Any],
    ) -> bool:
        slug = str(provider_cfg.get("slug") or "")
        cat = str(provider_cfg.get("category") or "")
        if slug == "apex-analytical" or cat == "builtin":
            return True
        if cat in {"local", "local_ollama"} or slug.startswith("ollama"):
            return bool(
                os.environ.get("APEX_OLLAMA_URL")
                or os.environ.get("OLLAMA_BASE_URL")
                or self._runtime_secrets.get((organization_id, slug))
            )
        if provider_cfg.get("auth_env_var") is None and cat == "compatible_http":
            return bool(str(provider_cfg.get("base_url") or "").startswith(("http://", "https://")))
        return (
            self.resolve_api_key(
                organization_id=organization_id, provider_cfg=provider_cfg
            )
            is not None
        )

    def is_provider_ready(
        self,
        *,
        organization_id: str,
        provider_cfg: dict[str, Any],
    ) -> bool:
        if not provider_cfg.get("enabled", True):
            return False
        return self.has_credentials(organization_id, provider_cfg)

    def complete_sync(
        self,
        *,
        organization_id: str,
        providers_catalog: list[dict[str, Any]],
        preferred_model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1600,
        bypass_circuit_breaker: bool = False,
    ) -> LLMCompletionResult | None:
        """Attempt live LLM completion with automatic fallback across configured providers.

        Returns `None` when only the local deterministic analytical engine is active,
        allowing `DurableCheckpointRuntimeAdapter` to run offline without failure.
        """
        by_slug = {str(p["slug"]): p for p in providers_catalog if p.get("enabled", True)}

        # Pick starting provider matching preferred_model or first configured external provider
        ordered_candidates: list[dict[str, Any]] = []
        for p in providers_catalog:
            if not p.get("enabled", True) or p.get("slug") == "apex-analytical":
                continue
            model_ids = {m.get("id") for m in (p.get("models") or [])}
            if preferred_model in model_ids or preferred_model.startswith(str(p.get("slug")) + "/"):
                ordered_candidates.insert(0, p)
            elif self.resolve_api_key(organization_id=organization_id, provider_cfg=p):
                ordered_candidates.append(p)

        if not ordered_candidates:
            return None

        visited: set[str] = set()
        queue = list(ordered_candidates)
        fallback_used = False
        last_err: str | None = None

        while queue:
            prov = queue.pop(0)
            slug = str(prov.get("slug") or "")
            if slug in visited or slug == "apex-analytical":
                continue
            visited.add(slug)

            # Enqueue configured fallback if present
            fb_slug = prov.get("fallback_provider_slug")
            if fb_slug and fb_slug in by_slug and fb_slug not in visited and fb_slug != "apex-analytical":
                queue.append(by_slug[fb_slug])

            bridge_ready = (
                slug in {"lmstudio-lan-qwen", "ollama-local", "ollama"}
                or prov.get("category") in {"compatible_http", "local_ollama"}
            ) and self.lan_bridge.is_online(organization_id)

            if (
                not bypass_circuit_breaker
                and not bridge_ready
                and self._unreachable_until.get(slug, 0.0) > time.monotonic()
            ):
                last_err = f"{slug}: ConnectTimeout (cached unreachable LAN endpoint)"
                fallback_used = True
                continue

            model_id = preferred_model
            if "/" in model_id and model_id.split("/", 1)[0] in by_slug:
                model_id = model_id.split("/", 1)[1]
            prov_models = [m.get("id") for m in (prov.get("models") or []) if m.get("id")]
            if model_id == "apex-analytical-local-v1" and prov_models:
                model_id = str(prov_models[0])

            try:
                res = self._invoke_single_provider(
                    organization_id=organization_id,
                    provider_cfg=prov,
                    model_identifier=model_id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    fallback_triggered=fallback_used,
                )
                self._unreachable_until.pop(slug, None)
                return res
            except SSRFBlockedError:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                self._unreachable_until[slug] = time.monotonic() + 30.0
                last_err = f"{slug}: {type(exc).__name__}: {exc}"
                fallback_used = True
            except Exception as exc:
                last_err = f"{slug}: {type(exc).__name__}: {exc}"
                fallback_used = True

        if last_err:
            return LLMCompletionResult(
                content="",
                structured_json=None,
                provider_slug="apex-analytical",
                model_identifier="apex-analytical-local-v1",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_usd=0.0,
                latency_ms=0,
                live_llm_invoked=False,
                fallback_triggered=True,
                error_note=last_err,
            )
        return None

    def _invoke_single_provider(
        self,
        *,
        organization_id: str,
        provider_cfg: dict[str, Any],
        model_identifier: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        fallback_triggered: bool,
    ) -> LLMCompletionResult:
        slug = str(provider_cfg.get("slug") or "compatible")
        cat = str(provider_cfg.get("category") or "compatible_http")
        base_url = str(provider_cfg.get("base_url") or "").rstrip("/")
        api_key = self.resolve_api_key(
            organization_id=organization_id, provider_cfg=provider_cfg
        )

        t0 = time.perf_counter()

        # Reverse LAN Bridge relay for Cloud Preview -> Private LAN LLM (e.g. 192.168.18.184:1234)
        if (
            slug in {"lmstudio-lan-qwen", "ollama-local", "ollama"}
            or (cat in {"compatible_http", "local_ollama"} and not api_key)
        ) and self.lan_bridge.is_online(organization_id):
            br = self.lan_bridge.submit_and_wait_sync(
                organization_id=organization_id,
                model=model_identifier,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = _strip_reasoning_tags(str(br.get("content") or ""))
            in_tok = int(br.get("prompt_tokens") or max(1, len(user_prompt) // 4))
            out_tok = int(br.get("completion_tokens") or max(1, len(content) // 4))
            tot_tok = int(br.get("total_tokens") or (in_tok + out_tok))
            lat_ms = int(
                br.get("latency_ms") or max(1, int((time.perf_counter() - t0) * 1000))
            )
            resolved_mod = str(br.get("model") or model_identifier)
            quote = compute_call_cost_quote(
                provider=slug,
                model=resolved_mod,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cached_tokens=int(br.get("cached_tokens") or 0),
                llm_invoked=True,
                provider_cfg=provider_cfg,
                explicit_category=CostCategory.LOCAL_LLM_API,
            )
            return LLMCompletionResult(
                content=content,
                structured_json=_extract_json_object(content),
                provider_slug=slug,
                model_identifier=resolved_mod,
                prompt_tokens=in_tok,
                completion_tokens=out_tok,
                total_tokens=tot_tok,
                cost_usd=0.0,
                latency_ms=lat_ms,
                live_llm_invoked=True,
                fallback_triggered=fallback_triggered,
                cached_tokens=int(br.get("cached_tokens") or 0),
                actual_cost_usd=quote.actual_cost_usd,
                estimated_cost_usd=quote.estimated_cost_usd,
                pricing_version=quote.pricing_version,
                cost_status=quote.cost_status,
                cost_category=quote.cost_category,
                request_id=str(br.get("request_id") or f"bridge_{int(t0 * 1000)}"),
            )

        trust_class = classify_provider_trust_class(provider_cfg)
        allow_dev_loopback = bool(provider_cfg.get("allow_dev_loopback", False))
        trusted_local = self.get_owner_trusted_origins(organization_id)
        cached_tok = 0
        upstream_actual_cost: float | None = None
        req_id: str | None = None

        if slug.startswith("anthropic"):
            if not api_key:
                raise RuntimeError("Missing ANTHROPIC_API_KEY")
            ant_root = base_url or "https://api.anthropic.com"
            endpoint = (
                f"{ant_root}/messages"
                if ant_root.endswith("/v1")
                else f"{ant_root}/v1/messages"
            )
            guarded = execute_ssrf_guarded_request(
                "POST",
                endpoint,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json_body={
                    "model": model_identifier,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
                allow_dev_loopback=allow_dev_loopback,
                dns_resolver=self._dns_resolver,
                connect_timeout=3.0,
                read_timeout=max(30.0, self._timeout),
                max_response_bytes=self._max_response_bytes,
                custom_transport_factory=self._custom_transport_factory,
            )
            data = guarded.json()
            blocks = data.get("content") or []
            text_out = "\n".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            ).strip()
            usage = data.get("usage") or {}
            cached_tok = int(usage.get("cache_read_input_tokens") or 0)
            p_tok = int(usage.get("input_tokens") or 0) + cached_tok
            c_tok = int(usage.get("output_tokens") or 0)
            req_id = str(
                data.get("id")
                or guarded.headers.get("request-id")
                or guarded.headers.get("x-request-id")
                or ""
            ) or None
        elif cat in {"local", "local_ollama"} or slug.startswith("ollama"):
            ollama_root = (
                os.environ.get("APEX_OLLAMA_URL")
                or os.environ.get("OLLAMA_BASE_URL")
                or base_url
                or "http://127.0.0.1:11434"
            ).rstrip("/")
            endpoint = f"{ollama_root}/api/chat"
            guarded = execute_ssrf_guarded_request(
                "POST",
                endpoint,
                json_body={
                    "model": model_identifier,
                    "stream": False,
                    "options": {"temperature": temperature},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                trust_class=EndpointTrustClass.CONTROLLED_LOCAL_MODEL,
                owner_trusted_origins=trusted_local,
                dns_resolver=self._dns_resolver,
                connect_timeout=3.0,
                read_timeout=max(30.0, self._timeout),
                max_response_bytes=self._max_response_bytes,
                custom_transport_factory=self._custom_transport_factory,
            )
            data = guarded.json()
            text_out = str((data.get("message") or {}).get("content") or "").strip()
            p_tok = int(data.get("prompt_eval_count") or max(1, len(user_prompt) // 4))
            c_tok = int(data.get("eval_count") or max(1, len(text_out) // 4))
            req_id = guarded.headers.get("x-request-id") or None
        else:
            # OpenAI / OpenRouter / LM Studio / vLLM / Compatible HTTP (`/v1/chat/completions`)
            if not api_key and cat in {"cloud", "commercial_cloud"}:
                raise RuntimeError(f"Missing API key for provider '{slug}'")
            root = base_url or "https://api.openai.com/v1"
            endpoint = (
                f"{root}/chat/completions"
                if root.endswith("/v1")
                else f"{root}/v1/chat/completions"
            )
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key or 'lm-studio'}",
            }
            guarded = execute_ssrf_guarded_request(
                "POST",
                endpoint,
                headers=headers,
                json_body={
                    "model": model_identifier,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                trust_class=trust_class,
                allow_dev_loopback=allow_dev_loopback,
                owner_trusted_origins=trusted_local if trust_class == EndpointTrustClass.CONTROLLED_LOCAL_MODEL else None,
                dns_resolver=self._dns_resolver,
                connect_timeout=2.5,
                read_timeout=max(60.0, self._timeout),
                max_response_bytes=self._max_response_bytes,
                custom_transport_factory=self._custom_transport_factory,
            )
            data = guarded.json()
            choices = data.get("choices") or []
            msg_obj = (choices[0].get("message") or {}) if choices else {}
            raw_content = str(msg_obj.get("content") or "").strip()
            reasoning_fallback = str(msg_obj.get("reasoning_content") or "").strip()
            text_out = _strip_reasoning_tags(raw_content or reasoning_fallback)
            usage = data.get("usage") or {}
            p_tok = int(usage.get("prompt_tokens") or max(1, len(user_prompt) // 4))
            c_tok = int(usage.get("completion_tokens") or max(1, len(text_out) // 4))
            p_details = usage.get("prompt_tokens_details") or {}
            cached_tok = int(
                p_details.get("cached_tokens")
                or usage.get("cached_tokens")
                or 0
            )
            if usage.get("cost") is not None:
                upstream_actual_cost = float(usage["cost"])
            elif guarded.headers.get("x-billing-cost-usd") is not None:
                upstream_actual_cost = float(guarded.headers["x-billing-cost-usd"])
            req_id = str(
                data.get("id")
                or guarded.headers.get("x-request-id")
                or ""
            ) or None

        elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
        tot_tok = p_tok + c_tok
        quote = compute_call_cost_quote(
            provider=slug,
            model=model_identifier,
            input_tokens=p_tok,
            output_tokens=c_tok,
            cached_tokens=cached_tok,
            llm_invoked=True,
            provider_cfg=provider_cfg,
            upstream_actual_cost_usd=upstream_actual_cost,
        )
        effective_usd = float(
            quote.actual_cost_usd
            if quote.actual_cost_usd is not None
            else (quote.estimated_cost_usd or 0.0)
        )
        return LLMCompletionResult(
            content=text_out,
            structured_json=_extract_json_object(text_out),
            provider_slug=slug,
            model_identifier=model_identifier,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=tot_tok,
            cost_usd=effective_usd,
            latency_ms=elapsed_ms,
            live_llm_invoked=True,
            fallback_triggered=fallback_triggered,
            cached_tokens=cached_tok,
            actual_cost_usd=quote.actual_cost_usd,
            estimated_cost_usd=quote.estimated_cost_usd,
            pricing_version=quote.pricing_version,
            cost_status=quote.cost_status,
            cost_category=quote.cost_category,
            request_id=req_id,
        )

    def embed_with_space_contract(
        self,
        text: str,
        *,
        organization_id: str,
        providers_catalog: list[dict[str, Any]],
        target_space: EmbeddingSpaceMetadata | None = None,
    ) -> tuple[list[float], EmbeddingSpaceMetadata]:
        """Generate an embedding strictly honoring `target_space` without silent provider/hash fallback."""
        space = target_space or build_local_deterministic_space(dimensions=EMBEDDING_DIM)
        prov_name = space.embedding_provider.strip().lower()

        if prov_name == "local-deterministic":
            vec = embed_text_1536(text, dim=space.embedding_dimensions)
            return validate_and_l2_normalize_vector(
                vec, expected_dimensions=space.embedding_dimensions
            ), space

        if prov_name == "openai":
            prov = next(
                (
                    p
                    for p in providers_catalog
                    if p.get("enabled", True)
                    and str(p.get("slug") or "") in {"openai", "openai-compatible-cloud"}
                ),
                None,
            )
            if prov is None:
                raise EmbeddingSpaceMismatchError(
                    f"Active embedding space '{space.space_id}' requires OpenAI provider configuration; "
                    "refusing to substitute incompatible local feature-hashing vectors."
                )
            api_key = self.resolve_api_key(
                organization_id=organization_id, provider_cfg=prov
            )
            if not api_key:
                raise EmbeddingSpaceMismatchError(
                    f"Active embedding space '{space.space_id}' requires OPENAI_API_KEY; "
                    "refusing to silently mix local feature-hashing vectors."
                )
            base = str(prov.get("base_url") or "https://api.openai.com/v1").rstrip("/")
            url = (
                f"{base}/embeddings"
                if base.endswith("/v1")
                else f"{base}/v1/embeddings"
            )
            safe_input = redact_confidential_text(text[:8000], for_external_cloud=True)
            try:
                guarded = execute_ssrf_guarded_request(
                    "POST",
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json_body={
                        "model": space.embedding_model,
                        "input": safe_input,
                        "dimensions": space.embedding_dimensions,
                    },
                    trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
                    allow_dev_loopback=bool(prov.get("allow_dev_loopback", False)),
                    dns_resolver=self._dns_resolver,
                    connect_timeout=3.0,
                    read_timeout=12.0,
                    max_response_bytes=self._max_response_bytes,
                    custom_transport_factory=self._custom_transport_factory,
                )
            except SSRFBlockedError:
                raise
            except Exception as exc:
                raise EmbeddingSpaceMismatchError(
                    f"Embedding provider '{space.space_id}' failed ({type(exc).__name__}: {exc}); "
                    "refusing to silently fall back to incompatible local hash vectors."
                ) from exc
            emb = (guarded.json().get("data") or [{}])[0].get("embedding")
            if not isinstance(emb, list) or not emb:
                raise EmbeddingSpaceMismatchError(
                    f"OpenAI embedding response missing vector for space '{space.space_id}'."
                )
            return (
                validate_and_l2_normalize_vector(
                    emb, expected_dimensions=space.embedding_dimensions
                ),
                space,
            )

        if prov_name == "ollama":
            prov = next(
                (
                    p
                    for p in providers_catalog
                    if p.get("enabled", True)
                    and str(p.get("slug") or "").startswith("ollama")
                ),
                None,
            )
            ollama_root = (
                os.environ.get("APEX_OLLAMA_URL")
                or os.environ.get("OLLAMA_BASE_URL")
                or (str(prov.get("base_url") or "") if prov else "")
            ).rstrip("/")
            if not ollama_root:
                raise EmbeddingSpaceMismatchError(
                    f"Active embedding space '{space.space_id}' requires Ollama base URL; "
                    "refusing to silently fall back to local hash vectors."
                )
            try:
                guarded = execute_ssrf_guarded_request(
                    "POST",
                    f"{ollama_root}/api/embed",
                    json_body={
                        "model": space.embedding_model,
                        "input": text[:8000],
                    },
                    trust_class=EndpointTrustClass.CONTROLLED_LOCAL_MODEL,
                    owner_trusted_origins=self.get_owner_trusted_origins(organization_id),
                    dns_resolver=self._dns_resolver,
                    connect_timeout=2.5,
                    read_timeout=8.0,
                    max_response_bytes=self._max_response_bytes,
                    custom_transport_factory=self._custom_transport_factory,
                )
            except SSRFBlockedError:
                raise
            except Exception as exc:
                raise EmbeddingSpaceMismatchError(
                    f"Ollama embedding provider '{space.space_id}' failed ({type(exc).__name__}: {exc}); "
                    "refusing to silently fall back to local hash vectors."
                ) from exc
            embs = guarded.json().get("embeddings") or []
            if not embs or not isinstance(embs[0], list):
                raise EmbeddingSpaceMismatchError(
                    f"Ollama embedding response missing vector for space '{space.space_id}'."
                )
            return (
                validate_and_l2_normalize_vector(
                    embs[0], expected_dimensions=space.embedding_dimensions
                ),
                space,
            )

        raise EmbeddingSpaceMismatchError(
            f"Unsupported embedding_provider='{space.embedding_provider}' in space '{space.space_id}'."
        )

    def embed_text_1536_with_live_provider(
        self,
        text: str,
        *,
        organization_id: str,
        providers_catalog: list[dict[str, Any]],
        allow_external_cloud: bool = True,
    ) -> tuple[list[float], str]:
        """Generate a 1536-d vector, raising `EmbeddingDimensionMismatchError` if upstream returns non-1536d."""
        for prov in providers_catalog:
            if not prov.get("enabled", True):
                continue
            slug = str(prov.get("slug") or "")
            if allow_external_cloud and slug in {"openai", "openai-compatible-cloud"}:
                api_key = self.resolve_api_key(
                    organization_id=organization_id, provider_cfg=prov
                )
                if api_key:
                    base = str(prov.get("base_url") or "https://api.openai.com/v1").rstrip("/")
                    url = (
                        f"{base}/embeddings"
                        if base.endswith("/v1")
                        else f"{base}/v1/embeddings"
                    )
                    try:
                        safe_input = redact_confidential_text(text[:8000], for_external_cloud=True)
                        guarded = execute_ssrf_guarded_request(
                            "POST",
                            url,
                            headers={"Authorization": f"Bearer {api_key}"},
                            json_body={
                                "model": "text-embedding-3-small",
                                "input": safe_input,
                                "dimensions": EMBEDDING_DIM,
                            },
                            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
                            allow_dev_loopback=bool(prov.get("allow_dev_loopback", False)),
                            dns_resolver=self._dns_resolver,
                            connect_timeout=3.0,
                            read_timeout=12.0,
                            max_response_bytes=self._max_response_bytes,
                            custom_transport_factory=self._custom_transport_factory,
                        )
                        emb = (guarded.json().get("data") or [{}])[0].get("embedding")
                        if isinstance(emb, list) and emb:
                            return _normalize_to_1536(emb), "openai:text-embedding-3-small"
                    except (SSRFBlockedError, EmbeddingDimensionMismatchError):
                        raise
                    except Exception:
                        pass
            elif slug.startswith("ollama") and (
                os.environ.get("OLLAMA_BASE_URL") or os.environ.get("APEX_OLLAMA_URL")
            ):
                ollama_root = (
                    os.environ.get("APEX_OLLAMA_URL")
                    or os.environ["OLLAMA_BASE_URL"]
                ).rstrip("/")
                try:
                    guarded = execute_ssrf_guarded_request(
                        "POST",
                        f"{ollama_root}/api/embed",
                        json_body={
                            "model": os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                            "input": text[:8000],
                        },
                        trust_class=EndpointTrustClass.CONTROLLED_LOCAL_MODEL,
                        owner_trusted_origins=self.get_owner_trusted_origins(organization_id),
                        dns_resolver=self._dns_resolver,
                        connect_timeout=2.5,
                        read_timeout=8.0,
                        max_response_bytes=self._max_response_bytes,
                        custom_transport_factory=self._custom_transport_factory,
                    )
                    embs = guarded.json().get("embeddings") or []
                    if embs and isinstance(embs[0], list):
                        return _normalize_to_1536(embs[0]), "ollama:nomic-embed-text"
                except (SSRFBlockedError, EmbeddingDimensionMismatchError):
                    raise
                except Exception:
                    pass

        return embed_text_1536(text), "deterministic-local-1536"
