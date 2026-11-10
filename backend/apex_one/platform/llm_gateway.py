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

import ipaddress
import json
import math
import os
import re
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from apex_one.db.models import DEFAULT_EMBEDDING_DIM as EMBEDDING_DIM
from apex_one.integrations_hub import SSRFBlockedError, assert_public_http_url
from apex_one.platform.memory_and_docs import embed_text_1536


def _validate_llm_endpoint(url: str) -> str:
    """Validate LLM endpoint URL while always blocking cloud link-local metadata IPs."""
    parts = urlsplit(url.strip())
    if parts.hostname:
        try:
            ip = ipaddress.ip_address(parts.hostname)
            if ip.is_link_local:
                raise SSRFBlockedError(f"Metadata link-local IP '{ip}' is blocked.")
        except ValueError:
            pass
    return assert_public_http_url(url, allow_local=True)

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


def _estimate_cost_usd(
    provider_cfg: dict[str, Any],
    model_identifier: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    in_rate = 0.0005  # default per 1k tokens
    out_rate = 0.0015
    for m in provider_cfg.get("models") or []:
        if m.get("id") == model_identifier:
            in_rate = float(m.get("cost_per_1k_input_usd", in_rate))
            out_rate = float(m.get("cost_per_1k_output_usd", out_rate))
            break
    return round((prompt_tokens / 1000.0) * in_rate + (completion_tokens / 1000.0) * out_rate, 6)


def _normalize_to_1536(raw_vec: list[float]) -> list[float]:
    if not raw_vec:
        return [0.0] * EMBEDDING_DIM
    if len(raw_vec) == EMBEDDING_DIM:
        vec = [float(x) for x in raw_vec]
    elif len(raw_vec) > EMBEDDING_DIM:
        vec = [float(x) for x in raw_vec[:EMBEDDING_DIM]]
    else:
        # Deterministic tiled projection to 1536-d preserving cosine geometry
        vec = [float(raw_vec[i % len(raw_vec)]) for i in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        vec = [round(v / norm, 6) for v in vec]
    return vec


class LLMGateway:
    """Provider-neutral LLM & Embedding client with fallback and secret isolation."""

    def __init__(self, *, http_timeout: float = 90.0) -> None:
        self._timeout = http_timeout
        # Keyed by (organization_id, provider_slug) -> secret string (never serialized)
        self._runtime_secrets: dict[tuple[str, str], str] = {}
        # Short-lived circuit breaker for unreachable private LAN hosts
        self._unreachable_until: dict[str, float] = {}

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

            if (
                not bypass_circuit_breaker
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

        if slug.startswith("anthropic"):
            if not api_key:
                raise RuntimeError("Missing ANTHROPIC_API_KEY")
            ant_root = base_url or "https://api.anthropic.com"
            endpoint = (
                f"{ant_root}/messages"
                if ant_root.endswith("/v1")
                else f"{ant_root}/v1/messages"
            )
            _validate_llm_endpoint(endpoint)
            with httpx.Client(timeout=self._timeout, verify=ssl.create_default_context()) as client:
                resp = client.post(
                    endpoint,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_identifier,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            blocks = data.get("content") or []
            text_out = "\n".join(
                b.get("text", "") for b in blocks if b.get("type") == "text"
            ).strip()
            usage = data.get("usage") or {}
            p_tok = int(usage.get("input_tokens") or 0)
            c_tok = int(usage.get("output_tokens") or 0)
        elif cat in {"local", "local_ollama"} or slug.startswith("ollama"):
            ollama_root = (
                os.environ.get("APEX_OLLAMA_URL")
                or os.environ.get("OLLAMA_BASE_URL")
                or base_url
                or "http://127.0.0.1:11434"
            ).rstrip("/")
            endpoint = f"{ollama_root}/api/chat"
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    endpoint,
                    json={
                        "model": model_identifier,
                        "stream": False,
                        "options": {"temperature": temperature},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            text_out = str((data.get("message") or {}).get("content") or "").strip()
            p_tok = int(data.get("prompt_eval_count") or max(1, len(user_prompt) // 4))
            c_tok = int(data.get("eval_count") or max(1, len(text_out) // 4))
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
            _validate_llm_endpoint(endpoint)
            headers = {"Content-Type": "application/json"}
            headers["Authorization"] = f"Bearer {api_key or 'lm-studio'}"
            client_timeout = httpx.Timeout(
                connect=2.5,
                read=max(60.0, self._timeout),
                write=15.0,
                pool=5.0,
            )
            with httpx.Client(
                timeout=client_timeout,
                verify=ssl.create_default_context(),
            ) as client:
                resp = client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": model_identifier,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            choices = data.get("choices") or []
            msg_obj = (choices[0].get("message") or {}) if choices else {}
            raw_content = str(msg_obj.get("content") or "").strip()
            reasoning_fallback = str(msg_obj.get("reasoning_content") or "").strip()
            text_out = _strip_reasoning_tags(raw_content or reasoning_fallback)
            usage = data.get("usage") or {}
            p_tok = int(usage.get("prompt_tokens") or max(1, len(user_prompt) // 4))
            c_tok = int(usage.get("completion_tokens") or max(1, len(text_out) // 4))

        elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
        tot_tok = p_tok + c_tok
        cost = _estimate_cost_usd(provider_cfg, model_identifier, p_tok, c_tok)
        return LLMCompletionResult(
            content=text_out,
            structured_json=_extract_json_object(text_out),
            provider_slug=slug,
            model_identifier=model_identifier,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            total_tokens=tot_tok,
            cost_usd=cost,
            latency_ms=elapsed_ms,
            live_llm_invoked=True,
            fallback_triggered=fallback_triggered,
        )

    def embed_text_1536_with_live_provider(
        self,
        text: str,
        *,
        organization_id: str,
        providers_catalog: list[dict[str, Any]],
    ) -> tuple[list[float], str]:
        """Generate a 1536-d semantic vector via OpenAI or Ollama when configured, else local."""
        for prov in providers_catalog:
            if not prov.get("enabled", True):
                continue
            slug = str(prov.get("slug") or "")
            if slug in {"openai", "openai-compatible-cloud"}:
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
                        _validate_llm_endpoint(url)
                        with httpx.Client(timeout=12.0, verify=ssl.create_default_context()) as client:
                            r = client.post(
                                url,
                                headers={"Authorization": f"Bearer {api_key}"},
                                json={
                                    "model": "text-embedding-3-small",
                                    "input": text[:8000],
                                    "dimensions": EMBEDDING_DIM,
                                },
                            )
                            r.raise_for_status()
                            emb = (r.json().get("data") or [{}])[0].get("embedding")
                            if isinstance(emb, list) and emb:
                                return _normalize_to_1536(emb), "openai:text-embedding-3-small"
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
                    with httpx.Client(timeout=8.0) as client:
                        r = client.post(
                            f"{ollama_root}/api/embed",
                            json={
                                "model": os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
                                "input": text[:8000],
                            },
                        )
                        r.raise_for_status()
                        embs = r.json().get("embeddings") or []
                        if embs and isinstance(embs[0], list):
                            return _normalize_to_1536(embs[0]), "ollama:nomic-embed-text"
                except Exception:
                    pass

        return embed_text_1536(text), "deterministic-local-1536"
