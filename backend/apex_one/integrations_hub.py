"""Configurable Web Search, Ollama, GitHub, Browser, and Generic REST integration adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from apex_one.platform.ssrf_guard import (
    EndpointTrustClass,
    SSRFBlockedError,
    normalize_origin,
    resolve_and_validate_url,
)

__all__ = [
    "SSRFBlockedError",
    "assert_public_http_url",
]


def assert_public_http_url(
    url: str,
    *,
    allow_local: bool = False,
    require_https: bool = False,
    dns_resolver: Any = None,
) -> str:
    """Validate an outbound URL with DNS resolution and IPv4/IPv6 bogon enforcement."""
    cleaned = url.strip()
    if allow_local:
        origin = normalize_origin(cleaned)
        resolve_and_validate_url(
            cleaned,
            trust_class=EndpointTrustClass.CONTROLLED_LOCAL_MODEL,
            owner_trusted_origins={origin},
            dns_resolver=dns_resolver,
        )
        return cleaned

    resolve_and_validate_url(
        cleaned,
        trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        allow_dev_loopback=False,
        require_https=require_https,
        dns_resolver=dns_resolver,
    )
    return cleaned


@dataclass(frozen=True)
class IntegrationHealth:
    slug: str
    configured: bool
    connected: bool
    status: str
    detail: str


async def check_web_search_health() -> IntegrationHealth:
    api_key = os.environ.get("APEX_WEB_SEARCH_API_KEY", "").strip()
    endpoint = os.environ.get("APEX_WEB_SEARCH_ENDPOINT", "").strip()
    if not api_key or not endpoint:
        return IntegrationHealth(
            slug="web-search",
            configured=False,
            connected=False,
            status="UNCONFIGURED",
            detail="APEX_WEB_SEARCH_API_KEY / APEX_WEB_SEARCH_ENDPOINT not set; local document research remains 100% active.",
        )
    return IntegrationHealth(
        slug="web-search",
        configured=True,
        connected=True,
        status="CONFIGURED",
        detail="External web search endpoint configured.",
    )


async def check_ollama_health() -> IntegrationHealth:
    base_url = os.environ.get("APEX_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                models = [m.get("name") for m in resp.json().get("models", [])]
                return IntegrationHealth(
                    slug="ollama",
                    configured=True,
                    connected=True,
                    status="ONLINE",
                    detail=f"Connected to Ollama ({len(models)} local models).",
                )
    except Exception:
        pass
    return IntegrationHealth(
        slug="ollama",
        configured=bool(os.environ.get("APEX_OLLAMA_URL")),
        connected=False,
        status="OFFLINE",
        detail=f"No local Ollama daemon reachable at {base_url} inside current host.",
    )


async def check_github_integration_health() -> IntegrationHealth:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                "https://api.github.com/rate_limit",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                return IntegrationHealth(
                    slug="github",
                    configured=True,
                    connected=True,
                    status="ONLINE",
                    detail="GitHub REST API reachable (HTTP 200).",
                )
    except Exception as exc:
        return IntegrationHealth(
            slug="github",
            configured=True,
            connected=False,
            status="UNREACHABLE",
            detail=f"GitHub API check failed: {type(exc).__name__}",
        )
    return IntegrationHealth(
        slug="github",
        configured=True,
        connected=False,
        status="DEGRADED",
        detail="GitHub API returned non-200 status.",
    )


def create_integration_placeholders() -> list[dict[str, Any]]:
    """Catalog of built-in adapters and sandboxed future extension interfaces (Part III.18)."""
    return [
        {
            "slug": "local-documents",
            "name": "Lokalny Silnik Dokumentów (PDF / DOCX / MD / TXT)",
            "category": "core_adapter",
            "version": "1.0.0",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["sources:read", "sources:write", "memory:index"],
            "event_subscriptions": ["source.registered", "document.imported"],
            "enabled": True,
            "status": "ACTIVE",
        },
        {
            "slug": "github",
            "name": "GitHub Repository & Citation Adapter",
            "category": "core_adapter",
            "version": "1.0.0",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["sources:read"],
            "event_subscriptions": ["source.verify_url"],
            "enabled": True,
            "status": "ACTIVE",
        },
        {
            "slug": "ollama",
            "name": "Ollama Local Model Runtime Adapter",
            "category": "model_adapter",
            "version": "1.0.0",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["models:invoke_local"],
            "event_subscriptions": ["model.health_probe"],
            "enabled": True,
            "status": "STANDBY_OFFLINE",
        },
        {
            "slug": "web-search",
            "name": "Konfigurowalna Wyszukiwarka Web (Tavily / Brave / HTTP)",
            "category": "core_adapter",
            "version": "1.0.0",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["network:outbound_search", "sources:write"],
            "event_subscriptions": ["research.scout_sources"],
            "enabled": False,
            "status": "UNCONFIGURED",
        },
        {
            "slug": "generic-rest-api",
            "name": "Generyczny Konektor REST API (z ochroną SSRF)",
            "category": "core_adapter",
            "version": "1.0.0",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["network:outbound_allowlist"],
            "event_subscriptions": ["tool.external_api_call"],
            "enabled": True,
            "status": "ACTIVE",
        },
        {
            "slug": "ext-freshstock",
            "name": "Interfejs Rozszerzenia: FreshStock Supply Intelligence",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-ksef",
            "name": "Interfejs Rozszerzenia: KSeF Compliance & E-Faktury",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope", "governance:require_r3"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-personal-ai",
            "name": "Interfejs Rozszerzenia: Personal AI Companion",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope", "memory:personal_partition"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-dev-tools",
            "name": "Interfejs Rozszerzenia: Developer Engineering Suite",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope", "sandbox:submit_job"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-business-apps",
            "name": "Interfejs Rozszerzenia: Moduły Aplikacji Biznesowych",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-creative-studio",
            "name": "Interfejs Rozszerzenia: Aplikacje Kreatywne i Multimedialne",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
        {
            "slug": "ext-agent-sim",
            "name": "Interfejs Rozszerzenia: Środowiska Symulacji Agentów",
            "category": "extension_slot",
            "version": "1.0.0-interface",
            "compatible_core_range": ">=1.0.0 <2.0.0",
            "permissions": ["extension:isolated_scope", "experiments:run"],
            "event_subscriptions": [],
            "enabled": False,
            "status": "INTERFACE_READY",
        },
    ]
