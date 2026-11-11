"""HARDENING 1.0 — Stage 3 (P1): Comprehensive SSRF, DNS-Rebinding & Tenant-Bound Endpoint Tests.

Covers all 11 mandatory verification scenarios without performing any live internal network I/O:
1. Block localhost / loopback (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`)
2. Block IPv4 private & CGNAT ranges (`10/8`, `172.16/12`, `192.168/16`, `100.64/10`)
3. Block IPv6 ULA (`fc00::/7`), IPv4-mapped IPv6 (`::ffff:...`), and 6to4 (`2002::/16`)
4. Block link-local (`169.254/16`, `fe80::/10`) and multicast (`224/4`, `ff00::/8`)
5. Block cloud metadata IPs/hosts (`169.254.169.254`, `fd00:ec2::254`, `100.100.100.200`, `metadata.google.internal`) across ALL trust classes
6. Block public hostname resolving via DNS to private/internal IP
7. Anti-DNS-rebinding (TOCTOU): IP-pinned transport dials ONLY the validated IP even if attacker DNS flips to `127.0.0.1` immediately after validation
8. Block 301/302/307 redirect chains pivoting to private/loopback/metadata targets
9. Allow valid public HTTPS provider via mock transport (while blocking cleartext HTTP & oversized responses)
10. Allow controlled Owner-trusted Local Model (`LM Studio` / `Ollama`) & Local Bridge routes
11. Enforce RBAC (Owner-only local endpoint registration) and strict cross-tenant isolation of trusted local origins
"""

from __future__ import annotations

from typing import Any

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient

from apex_one.api.main import create_app
from apex_one.config import Settings
from apex_one.platform.llm_gateway import LLMGateway
from apex_one.platform.ssrf_guard import (
    EndpointTrustClass,
    PinnedIPNetworkBackend,
    SSRFBlockedError,
    ValidatedEndpointPlan,
    execute_ssrf_guarded_request,
    resolve_and_validate_url,
)
from apex_one.platform.workspace_service import ApexPlatformService, AuthorizationError


def _mock_transport_factory(
    handler: Any,
    recorded_targets: list[ValidatedEndpointPlan] | None = None,
):
    def _factory(target: ValidatedEndpointPlan) -> httpx.BaseTransport:
        if recorded_targets is not None:
            recorded_targets.append(target)
        return httpx.MockTransport(handler)

    return _factory


@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://localhost/v1/chat/completions",
        "https://localhost./v1/chat/completions",
        "https://api.localhost/v1/chat/completions",
        "https://127.0.0.1/v1/chat/completions",
        "https://127.0.99.5:8080/v1",
        "https://[::1]/v1/chat/completions",
        "https://0.0.0.0/v1/chat/completions",
        "https://[::]/v1/chat/completions",
    ],
)
def test_01_ssrf_blocks_localhost_and_loopback_variants(blocked_url: str) -> None:
    """1. Block localhost, loopback IPv4/IPv6, and unspecified addresses."""
    with pytest.raises(SSRFBlockedError):
        resolve_and_validate_url(
            blocked_url,
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
            allow_dev_loopback=False,
        )


@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://10.0.0.1/v1",
        "https://10.255.14.9:8000/v1",
        "https://172.16.0.1/v1",
        "https://172.31.255.254/v1",
        "https://192.168.1.1/v1",
        "https://192.168.18.184:1234/v1",
        "https://100.64.0.10/v1",
        "https://198.18.0.5/v1",
    ],
)
def test_02_ssrf_blocks_ipv4_private_and_cgnat_ranges(blocked_url: str) -> None:
    """2. Block RFC1918 private ranges, CGNAT 100.64/10, and benchmark 198.18/15."""
    with pytest.raises(SSRFBlockedError):
        resolve_and_validate_url(
            blocked_url,
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        )


@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://[fc00::1]/v1",
        "https://[fd12:3456:789a::1]:8443/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[::ffff:10.0.0.8]/v1",
        "https://[::ffff:192.168.18.184]/v1",
        "https://[::ffff:169.254.169.254]/latest/meta-data",
        "https://[2002:7f00:0001::1]/v1",  # 6to4 embedding 127.0.0.1
        "https://[2002:0a00:0001::1]/v1",  # 6to4 embedding 10.0.0.1
    ],
)
def test_03_ssrf_blocks_ipv6_private_ula_and_ipv4_mapped_or_6to4(
    blocked_url: str,
) -> None:
    """3. Block IPv6 ULA (`fc00::/7`), IPv4-mapped IPv6 (`::ffff:...`), and 6to4 (`2002::/16`)."""
    with pytest.raises(SSRFBlockedError):
        resolve_and_validate_url(
            blocked_url,
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        )


@pytest.mark.parametrize(
    "blocked_url",
    [
        "https://169.254.1.20/v1",
        "https://[fe80::1]/v1",
        "https://224.0.0.1/v1",
        "https://[ff02::1]/v1",
    ],
)
def test_04_ssrf_blocks_link_local_and_multicast(blocked_url: str) -> None:
    """4. Block IPv4/IPv6 link-local and multicast addresses."""
    with pytest.raises(SSRFBlockedError):
        resolve_and_validate_url(
            blocked_url,
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        )


@pytest.mark.parametrize(
    "metadata_url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://169.254.169.254/computeMetadata/v1/",
        "http://169.254.170.2/v2/credentials",
        "http://100.100.100.200/latest/meta-data/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/meta-data/",
    ],
)
def test_05_ssrf_unconditionally_blocks_cloud_metadata_even_for_owner_local_mode(
    metadata_url: str,
) -> None:
    """5. Block AWS/GCP/Azure/ECS/Alibaba cloud metadata across BOTH public and controlled local modes."""
    with pytest.raises(SSRFBlockedError):
        resolve_and_validate_url(
            metadata_url,
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        )
    # Even if an attacker or misconfigured Owner tries to whitelist metadata as a local model origin:
    gw = LLMGateway()
    with pytest.raises(SSRFBlockedError):
        gw.trust_local_origin(
            organization_id="org-meta-test",
            url_or_origin=metadata_url,
        )


def test_06_ssrf_blocks_public_domain_resolving_to_private_or_metadata_ip() -> None:
    """6. Block public hostname whose DNS A/AAAA record resolves to internal/metadata IP."""
    fake_dns_records = {
        "sneaky-lan.attacker.example": ["10.24.0.9"],
        "dual-stack-poison.attacker.example": ["93.184.216.34", "fd00:ec2::254"],
    }

    def fake_resolver(host: str, port: int) -> list[str]:
        return fake_dns_records[host]

    for host in fake_dns_records:
        with pytest.raises(SSRFBlockedError):
            resolve_and_validate_url(
                f"https://{host}/v1/chat/completions",
                trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
                dns_resolver=fake_resolver,
            )


def test_07_ssrf_dns_rebinding_simulation_pins_validated_ip_without_second_lookup() -> None:
    """7. Anti-DNS-rebinding (TOCTOU): connect strictly to the validated IP even if DNS flips immediately."""
    lookup_calls = 0

    def rebinding_dns_resolver(host: str, port: int) -> list[str]:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return ["93.184.216.34"]  # Valid public IP during validation
        # Attacker TTL=0 DNS flips to loopback/metadata right before socket connect!
        return ["127.0.0.1"]

    recorded_targets: list[ValidatedEndpointPlan] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == "rebind-test.example.org"
        return httpx.Response(200, json={"ok": True})

    resp = execute_ssrf_guarded_request(
        "POST",
        "https://rebind-test.example.org/v1/chat/completions",
        json_body={"ping": 1},
        trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
        dns_resolver=rebinding_dns_resolver,
        custom_transport_factory=_mock_transport_factory(mock_handler, recorded_targets),
    )
    assert resp.status_code == 200
    assert lookup_calls == 1
    assert len(recorded_targets) == 1
    assert recorded_targets[0].pinned_ip == "93.184.216.34"

    # Also verify `PinnedIPNetworkBackend` rewrites connect_tcp(host=...) -> literal pinned_ip at httpcore layer:
    dialed_hosts: list[tuple[str, int]] = []

    class RecordingBackend(httpcore.NetworkBackend):
        def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Any = None,
        ) -> httpcore.NetworkStream:
            dialed_hosts.append((host, port))
            raise ConnectInterceptedError(f"Dialed {host}:{port}")

    class ConnectInterceptedError(RuntimeError):
        pass

    pinned_backend = PinnedIPNetworkBackend(
        expected_hostname="rebind-test.example.org",
        expected_port=443,
        pinned_ip="93.184.216.34",
        inner_backend=RecordingBackend(),
    )
    with pytest.raises(ConnectInterceptedError):
        pinned_backend.connect_tcp("rebind-test.example.org", 443, timeout=2.0)
    assert dialed_hosts == [("93.184.216.34", 443)]


def test_08_ssrf_blocks_redirect_chain_to_private_or_metadata_ip() -> None:
    """8. Block HTTP 301/302/307 redirects targeting private, loopback, or cloud metadata endpoints."""

    def dns_resolver(host: str, port: int) -> list[str]:
        if host == "public-hop.example.com":
            return ["93.184.216.34"]
        if host == "internal-hop.example.com":
            return ["192.168.10.25"]
        raise AssertionError(f"Unexpected host {host}")

    def redirect_to_metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        )

    with pytest.raises(SSRFBlockedError, match="metadata|link-local|HTTPS"):
        execute_ssrf_guarded_request(
            "GET",
            "https://public-hop.example.com/paper.pdf",
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
            require_https=False,
            dns_resolver=dns_resolver,
            custom_transport_factory=_mock_transport_factory(redirect_to_metadata),
        )

    def redirect_to_private_dns(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"Location": "https://internal-hop.example.com/v1/chat/completions"},
        )

    with pytest.raises(SSRFBlockedError, match="(?i)private"):
        execute_ssrf_guarded_request(
            "POST",
            "https://public-hop.example.com/v1/chat/completions",
            json_body={"messages": []},
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
            dns_resolver=dns_resolver,
            custom_transport_factory=_mock_transport_factory(redirect_to_private_dns),
        )


def test_09_allowed_public_https_provider_and_response_size_cap() -> None:
    """9. Allow legitimate public HTTPS provider while blocking cleartext HTTP & oversized bodies."""

    def fake_dns(host: str, port: int) -> list[str]:
        assert host == "api.openai.com"
        return ["104.18.7.192"]

    def openai_mock(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Verified safe public HTTPS call."}}],
                "usage": {"prompt_tokens": 14, "completion_tokens": 6},
            },
        )

    gw = LLMGateway(
        dns_resolver=fake_dns,
        custom_transport_factory=_mock_transport_factory(openai_mock),
        max_response_bytes=2048,
    )
    gw.set_runtime_secret(
        organization_id="org-pub", provider_slug="openai", api_key="sk-pub-mock-123456"
    )

    # Cleartext HTTP to public cloud provider MUST be rejected
    with pytest.raises(SSRFBlockedError, match="HTTPS"):
        gw.complete_sync(
            organization_id="org-pub",
            providers_catalog=[
                {
                    "slug": "openai",
                    "category": "cloud",
                    "base_url": "http://api.openai.com/v1",
                    "enabled": True,
                }
            ],
            preferred_model="gpt-4.1-mini",
            system_prompt="System",
            user_prompt="Hello",
        )

    # Valid HTTPS public provider succeeds cleanly
    res = gw.complete_sync(
        organization_id="org-pub",
        providers_catalog=[
            {
                "slug": "openai",
                "category": "cloud",
                "base_url": "https://api.openai.com/v1",
                "enabled": True,
            }
        ],
        preferred_model="gpt-4.1-mini",
        system_prompt="System",
        user_prompt="Hello",
    )
    assert res is not None
    assert res.live_llm_invoked is True
    assert res.content == "Verified safe public HTTPS call."

    # Oversized upstream payload exceeding `max_response_bytes` is terminated cleanly
    def oversized_mock(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"A" * 8192)

    gw_capped = LLMGateway(
        dns_resolver=fake_dns,
        custom_transport_factory=_mock_transport_factory(oversized_mock),
        max_response_bytes=1024,
    )
    with pytest.raises(SSRFBlockedError, match="maximum allowed size"):
        execute_ssrf_guarded_request(
            "GET",
            "https://api.openai.com/v1/models",
            trust_class=EndpointTrustClass.PUBLIC_PROVIDER,
            dns_resolver=fake_dns,
            max_response_bytes=1024,
            custom_transport_factory=_mock_transport_factory(oversized_mock),
        )
    assert gw_capped._max_response_bytes == 1024


def test_10_allowed_controlled_owner_local_model_and_lan_bridge_routes() -> None:
    """10. Controlled Local Model (Owner-trusted LM Studio / Ollama) & LAN Bridge remain operational."""

    def local_lmstudio_mock(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://192.168.18.184:1234/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "<think>local</think>Local Qwen synthesis OK."}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 9},
            },
        )

    gw = LLMGateway(
        custom_transport_factory=_mock_transport_factory(local_lmstudio_mock)
    )
    svc = ApexPlatformService(seed_demo=False, llm_gateway=gw)
    svc.register_user(
        email="owner-lan@example.org",
        password="OwnerLanStrongPassword!2026",
        display_name="LAN Owner",
        organization_name="LAN Research Lab",
    )
    owner_login = svc.login(
        email="owner-lan@example.org",
        password="OwnerLanStrongPassword!2026",
    )
    oid = owner_login["active_organization_id"]
    uid = owner_login["user"]["id"]

    # Owner configures controlled local LM Studio endpoint
    cfg_resp = svc.configure_model_provider_secret(
        user_id=uid,
        organization_id=oid,
        provider_slug="lmstudio-lan-qwen",
        api_key="lm-studio",
        base_url="http://192.168.18.184:1234/v1",
    )
    assert cfg_resp["health_status"] == "CONFIGURED"

    test_out = svc.test_model_provider_inference(
        user_id=uid,
        organization_id=oid,
        model_identifier="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        prompt="Verify controlled local model route.",
    )
    assert test_out["live_llm_invoked"] is True
    assert test_out["content"] == "Local Qwen synthesis OK."


def test_11_tenant_and_rbac_isolation_for_local_and_ssrf_endpoints() -> None:
    """11. Non-Owners cannot register local origins; Tenant A's local origin is blocked for Tenant B; HTTP API rejects SSRF."""
    gw = LLMGateway(
        custom_transport_factory=_mock_transport_factory(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Local response"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )
        )
    )
    svc = ApexPlatformService(seed_demo=False, llm_gateway=gw)

    # Tenant A Owner registers custom local workstation `http://192.168.50.77:1234/v1`
    svc.register_user(
        email="owner-a@tenant-a.org",
        password="TenantAOwnerPassword!2026",
        display_name="Owner A",
        organization_name="Tenant A",
    )
    login_a = svc.login(
        email="owner-a@tenant-a.org",
        password="TenantAOwnerPassword!2026",
    )
    oid_a = login_a["active_organization_id"]
    uid_a = login_a["user"]["id"]
    svc.configure_model_provider_secret(
        user_id=uid_a,
        organization_id=oid_a,
        provider_slug="lmstudio-lan-qwen",
        api_key="lm-studio",
        base_url="http://192.168.50.77:1234/v1",
    )

    # Non-Owner (`Administrator` has `models:manage` but NOT `Owner` role) in Tenant A CANNOT alter controlled local endpoint
    admin_reg = svc.register_user(
        email="admin-a@tenant-a.org",
        password="TenantAAdminPassword!2026",
        display_name="Admin A",
        organization_name="Admin Scratch Org",
    )
    admin_uid = admin_reg["id"]
    svc.memberships.append(
        {
            "id": "mem-admin-a",
            "user_id": admin_uid,
            "organization_id": oid_a,
            "role_name": "Administrator",
            "status": "active",
        }
    )
    with pytest.raises(AuthorizationError, match="Owner"):
        svc.configure_model_provider_secret(
            user_id=admin_uid,
            organization_id=oid_a,
            provider_slug="lmstudio-lan-qwen",
            api_key="lm-studio",
            base_url="http://192.168.50.88:1234/v1",
        )

    # Tenant B CANNOT invoke Tenant A's `http://192.168.50.77:1234/v1` origin via LLMGateway
    svc.register_user(
        email="owner-b@tenant-b.org",
        password="TenantBOwnerPassword!2026",
        display_name="Owner B",
        organization_name="Tenant B",
    )
    login_b = svc.login(
        email="owner-b@tenant-b.org",
        password="TenantBOwnerPassword!2026",
    )
    oid_b = login_b["active_organization_id"]
    with pytest.raises(SSRFBlockedError, match="owner-trusted"):
        gw.complete_sync(
            organization_id=oid_b,
            providers_catalog=[
                {
                    "slug": "lmstudio-lan-qwen",
                    "category": "compatible_http",
                    "base_url": "http://192.168.50.77:1234/v1",
                    "auth_env_var": None,
                    "enabled": True,
                    "models": [
                        {"id": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"}
                    ],
                }
            ],
            preferred_model="qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
            system_prompt="Sys",
            user_prompt="Cross-tenant SSRF probe",
        )

    # End-to-end HTTP API check: configuring `openai` with private/metadata base_url returns `400 SSRF_BLOCKED`
    app = create_app(Settings(env="test", seed_demo=False))
    with TestClient(app) as client:
        reg = client.post(
            "/api/v1/auth/register",
            json={
                "email": "api-ssrf@example.org",
                "password": "ApiSsrfTestPassword!2026",
                "display_name": "API User",
                "organization_name": "API Org",
            },
        )
        assert reg.status_code == 201
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": "api-ssrf@example.org",
                "password": "ApiSsrfTestPassword!2026",
            },
        )
        assert login_resp.status_code == 200
        csrf = login_resp.json()["csrf_token"]
        api_oid = login_resp.json()["active_organization_id"]
        headers = {
            "X-CSRF-Token": csrf,
            "X-Organization-Id": api_oid,
        }

        ssrf_provider_resp = client.post(
            "/api/v1/models/providers/openai/secret",
            headers=headers,
            json={
                "api_key": "sk-test-ssrf-123456",
                "base_url": "http://169.254.169.254/latest/meta-data",
            },
        )
        assert ssrf_provider_resp.status_code == 400
        body_prov = ssrf_provider_resp.json()
        err_code_prov = (body_prov.get("error") or body_prov.get("detail") or {}).get("code")
        assert err_code_prov == "SSRF_BLOCKED"

        ssrf_source_resp = client.post(
            "/api/v1/sources/fetch-url",
            headers=headers,
            json={"url": "http://[::ffff:127.0.0.1]:8080/internal-admin"},
        )
        assert ssrf_source_resp.status_code == 400
        body_src = ssrf_source_resp.json()
        err_code_src = (body_src.get("error") or body_src.get("detail") or {}).get("code")
        assert err_code_src == "SSRF_BLOCKED"
