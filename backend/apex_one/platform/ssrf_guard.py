"""Hardened 3-Tier Endpoint Trust & Anti-DNS-Rebinding SSRF Guard (HARDENING 1.0 — P1).

Separates outbound model and data connections into three strict trust classes:
  A. PUBLIC_PROVIDER         — Public HTTPS endpoints only (DNS-resolved, IPv4/IPv6 bogon-checked,
                               IP-pinned socket transport eliminating DNS-rebinding TOCTOU,
                               hop-by-hop redirect re-validation, connect/read timeouts, byte cap).
  B. CONTROLLED_LOCAL_MODEL  — Ollama / LM Studio endpoints explicitly trusted by Organization Owner
                               and isolated per `organization_id` (still blocks cloud metadata,
                               link-local, multicast, unspecified, and reserved ranges).
  C. LOCAL_BRIDGE_OR_NODE    — Tenant-bound Reverse LAN Bridge or HMAC-authenticated Local Node
                               (no direct backend outbound private socket).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx


class SSRFBlockedError(PermissionError):
    """Raised when an outbound URL violates SSRF, scheme, DNS, IP, redirect, or tenant trust policy."""


class EndpointTrustClass(StrEnum):
    PUBLIC_PROVIDER = "PUBLIC_PROVIDER"
    CONTROLLED_LOCAL_MODEL = "CONTROLLED_LOCAL_MODEL"
    LOCAL_BRIDGE_OR_NODE = "LOCAL_BRIDGE_OR_NODE"


DNSResolverFn = Callable[[str, int], list[str]]

FORBIDDEN_METADATA_HOSTS: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.azure.com",
        "metadata.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

FORBIDDEN_METADATA_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "fd00:ec2::254",
    }
)

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK_NET = ipaddress.ip_network("198.18.0.0/15")
_IETF_PROTO_NET = ipaddress.ip_network("192.0.0.0/24")


def default_dns_resolver(hostname: str, port: int) -> list[str]:
    """Resolve `hostname` via OS `getaddrinfo` returning all distinct IPv4/IPv6 addresses."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution failed for host '{hostname}': {exc}") from exc

    resolved: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip_raw = str(sockaddr[0]).split("%", 1)[0]  # strip IPv6 zone index if any
        if ip_raw not in resolved:
            resolved.append(ip_raw)
    if not resolved:
        raise SSRFBlockedError(f"DNS resolution returned zero addresses for '{hostname}'.")
    return resolved


def _extract_embedded_ipv4(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address]:
    if isinstance(ip_obj, ipaddress.IPv4Address):
        return [ip_obj]
    extracted: list[ipaddress.IPv4Address] = []
    if ip_obj.ipv4_mapped is not None:
        extracted.append(ip_obj.ipv4_mapped)
    if ip_obj.sixtofour is not None:
        extracted.append(ip_obj.sixtofour)
    if ip_obj.teredo is not None:
        server_ip, client_ip = ip_obj.teredo
        extracted.extend([server_ip, client_ip])
    return extracted


def validate_resolved_ip(
    ip_str: str,
    *,
    trust_class: EndpointTrustClass = EndpointTrustClass.PUBLIC_PROVIDER,
    allow_dev_loopback: bool = False,
    env: str | None = None,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Validate a single IPv4 or IPv6 address (and any embedded IPv4 transitions) against SSRF rules."""
    cleaned = ip_str.strip().strip("[]").split("%", 1)[0]
    try:
        ip_obj = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise SSRFBlockedError(f"Invalid IP address '{ip_str}'.") from exc

    norm_env = (env or os.environ.get("APEX_ENV", "development")).strip().lower()
    effective_dev_loopback = allow_dev_loopback and norm_env in {"development", "test"}

    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip_obj]
    candidates.extend(_extract_embedded_ipv4(ip_obj))

    for cand in candidates:
        if str(cand).lower() in FORBIDDEN_METADATA_IPS:
            raise SSRFBlockedError(f"Cloud metadata IP '{cand}' is unconditionally blocked.")
        if cand.is_unspecified:
            raise SSRFBlockedError(f"Unspecified IP '{cand}' is blocked.")
        if cand.is_link_local:
            raise SSRFBlockedError(f"Link-local IP '{cand}' is blocked.")
        if cand.is_multicast:
            raise SSRFBlockedError(f"Multicast IP '{cand}' is blocked.")
        if isinstance(cand, ipaddress.IPv4Address) and (
            cand in _BENCHMARK_NET or cand in _IETF_PROTO_NET
        ):
            raise SSRFBlockedError(f"Reserved special-purpose IPv4 '{cand}' is blocked.")

        if cand.is_loopback:
            if trust_class == EndpointTrustClass.CONTROLLED_LOCAL_MODEL or effective_dev_loopback:
                continue
            raise SSRFBlockedError(f"Loopback IP '{cand}' is blocked by SSRF policy.")

        if isinstance(cand, ipaddress.IPv4Address) and cand in _CGNAT_NET:
            if trust_class == EndpointTrustClass.CONTROLLED_LOCAL_MODEL:
                continue
            raise SSRFBlockedError(f"Carrier-grade NAT / shared-space IP '{cand}' is blocked.")

        if cand.is_private:
            if trust_class == EndpointTrustClass.CONTROLLED_LOCAL_MODEL:
                continue
            raise SSRFBlockedError(f"Private IP '{cand}' is blocked by SSRF policy.")

        if cand.is_reserved:
            raise SSRFBlockedError(f"Reserved IP '{cand}' is blocked by SSRF policy.")

        if not cand.is_global and trust_class == EndpointTrustClass.PUBLIC_PROVIDER:
            raise SSRFBlockedError(f"Non-global IP '{cand}' is blocked for public endpoints.")

    return ip_obj


def normalize_origin(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    default_port = 443 if scheme == "https" else 80
    port = parts.port or default_port
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True)
class ValidatedEndpointPlan:
    url: str
    scheme: str
    hostname: str
    port: int
    pinned_ip: str
    all_resolved_ips: tuple[str, ...]
    trust_class: EndpointTrustClass


def resolve_and_validate_url(
    url: str,
    *,
    trust_class: EndpointTrustClass = EndpointTrustClass.PUBLIC_PROVIDER,
    allow_dev_loopback: bool = False,
    require_https: bool | None = None,
    env: str | None = None,
    owner_trusted_origins: set[str] | None = None,
    dns_resolver: DNSResolverFn | None = None,
) -> ValidatedEndpointPlan:
    """Validate URL syntax, scheme, hostname, DNS answers, and IPv4/IPv6 ranges, returning a pinned plan."""
    raw = url.strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise SSRFBlockedError(f"Unsupported URL scheme or missing hostname in '{url}'.")
    if parts.username or parts.password:
        raise SSRFBlockedError("Embedded URL credentials are forbidden by SSRF policy.")

    host = parts.hostname.lower().strip("[]")
    if host in FORBIDDEN_METADATA_HOSTS or host.endswith(".metadata.google.internal"):
        raise SSRFBlockedError(f"Cloud metadata hostname '{host}' is blocked.")

    norm_env = (env or os.environ.get("APEX_ENV", "development")).strip().lower()
    effective_dev_loopback = allow_dev_loopback and norm_env in {"development", "test"}

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SSRFBlockedError(f"Invalid port in URL '{url}'.") from exc

    if trust_class == EndpointTrustClass.PUBLIC_PROVIDER:
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            if not (effective_dev_loopback and host == "localhost"):
                raise SSRFBlockedError(f"Local hostname '{host}' is blocked for public endpoints.")
        enforce_https = (not effective_dev_loopback) if require_https is None else require_https
        if enforce_https and scheme != "https":
            raise SSRFBlockedError(
                f"Public provider endpoint '{url}' must use HTTPS (got '{scheme}://')."
            )
    elif trust_class == EndpointTrustClass.CONTROLLED_LOCAL_MODEL:
        origin = normalize_origin(raw)
        if owner_trusted_origins is None or origin not in owner_trusted_origins:
            raise SSRFBlockedError(
                f"Local model origin '{origin}' is not authorized in owner-trusted endpoints for this organization."
            )
    elif trust_class == EndpointTrustClass.LOCAL_BRIDGE_OR_NODE:
        raise SSRFBlockedError(
            "LOCAL_BRIDGE_OR_NODE routes must traverse the authenticated Bridge/Node channel, not direct HTTP."
        )

    # Check literal IP first if host is numeric, otherwise resolve via DNS and inspect ALL answers
    try:
        literal_ip = ipaddress.ip_address(host)
        resolved_ips = [str(literal_ip)]
    except ValueError:
        resolver = dns_resolver or default_dns_resolver
        resolved_ips = resolver(host, port)

    validated_list: list[str] = []
    for candidate_ip in resolved_ips:
        checked = validate_resolved_ip(
            candidate_ip,
            trust_class=trust_class,
            allow_dev_loopback=effective_dev_loopback,
            env=norm_env,
        )
        validated_list.append(str(checked))

    if not validated_list:
        raise SSRFBlockedError(f"No valid IP addresses resolved for '{host}'.")

    # If HTTP was permitted via effective_dev_loopback, ensure the resolved IP is strictly loopback!
    if (
        trust_class == EndpointTrustClass.PUBLIC_PROVIDER
        and scheme == "http"
        and effective_dev_loopback
    ):
        for vip in validated_list:
            if not ipaddress.ip_address(vip).is_loopback:
                raise SSRFBlockedError(
                    f"Plain HTTP in dev-loopback mode is restricted to 127.0.0.1/::1 (got '{vip}')."
                )

    return ValidatedEndpointPlan(
        url=raw,
        scheme=scheme,
        hostname=host,
        port=port,
        pinned_ip=validated_list[0],
        all_resolved_ips=tuple(validated_list),
        trust_class=trust_class,
    )


class PinnedIPNetworkBackend(httpcore.NetworkBackend):
    """`httpcore` network backend that connects TCP directly to the validated `pinned_ip`.

    Eliminates DNS-rebinding TOCTOU between validation and `socket.connect()`, while
    preserving the original hostname on `httpcore.HTTPConnection` for TLS SNI and
    X.509 certificate hostname verification.
    """

    def __init__(
        self,
        *,
        expected_hostname: str,
        expected_port: int,
        pinned_ip: str,
        inner_backend: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._expected_hostname = expected_hostname.lower().strip("[]")
        self._expected_port = expected_port
        self._pinned_ip = pinned_ip
        self._inner = inner_backend or httpcore.SyncBackend()
        self.connected_ips: list[str] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        norm_host = host.lower().strip("[]")
        if norm_host != self._expected_hostname or int(port) != self._expected_port:
            raise SSRFBlockedError(
                f"Pinned transport refused unexpected socket target '{host}:{port}' "
                f"(expected '{self._expected_hostname}:{self._expected_port}')."
            )
        self.connected_ips.append(self._pinned_ip)
        return self._inner.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        raise SSRFBlockedError("Unix domain sockets are forbidden in SSRF-guarded HTTP transport.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def build_pinned_httpx_transport(
    plan: ValidatedEndpointPlan,
    *,
    verify_tls: bool | ssl.SSLContext = True,
    inner_backend: httpcore.NetworkBackend | None = None,
) -> tuple[httpx.HTTPTransport, PinnedIPNetworkBackend]:
    """Construct an `httpx.HTTPTransport` bound to `plan.pinned_ip` with TLS verification."""
    ssl_ctx: ssl.SSLContext | None = None
    if plan.scheme == "https":
        if isinstance(verify_tls, ssl.SSLContext):
            ssl_ctx = verify_tls
        elif verify_tls:
            ssl_ctx = ssl.create_default_context()
        else:
            ssl_ctx = ssl._create_unverified_context()

    backend = PinnedIPNetworkBackend(
        expected_hostname=plan.hostname,
        expected_port=plan.port,
        pinned_ip=plan.pinned_ip,
        inner_backend=inner_backend,
    )
    transport = httpx.HTTPTransport(verify=ssl_ctx if ssl_ctx is not None else True, trust_env=False)
    transport._pool = httpcore.ConnectionPool(
        ssl_context=ssl_ctx,
        max_connections=10,
        max_keepalive_connections=2,
        keepalive_expiry=5.0,
        http1=True,
        http2=False,
        network_backend=backend,
    )
    return transport, backend


@dataclass(frozen=True)
class GuardedHTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str
    pinned_ips_used: tuple[str, ...]

    def json(self) -> Any:
        import json

        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def execute_ssrf_guarded_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    trust_class: EndpointTrustClass = EndpointTrustClass.PUBLIC_PROVIDER,
    allow_dev_loopback: bool = False,
    require_https: bool | None = None,
    env: str | None = None,
    owner_trusted_origins: set[str] | None = None,
    dns_resolver: DNSResolverFn | None = None,
    connect_timeout: float = 3.0,
    read_timeout: float = 60.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    max_redirects: int = 3,
    custom_transport_factory: Callable[[ValidatedEndpointPlan], httpx.BaseTransport] | None = None,
) -> GuardedHTTPResponse:
    """Execute an HTTP request with full pre-flight DNS/IP validation, IP-pinned socket,
    hop-by-hop redirect re-validation, strict timeouts, and streaming response size limits.
    """
    current_url = url.strip()
    current_method = method.upper()
    current_json = json_body
    req_headers = dict(headers or {})
    used_ips: list[str] = []

    timeout_cfg = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=15.0,
        pool=5.0,
    )

    for hop in range(max_redirects + 1):
        plan = resolve_and_validate_url(
            current_url,
            trust_class=trust_class,
            allow_dev_loopback=allow_dev_loopback,
            require_https=require_https,
            env=env,
            owner_trusted_origins=owner_trusted_origins,
            dns_resolver=dns_resolver,
        )
        used_ips.append(plan.pinned_ip)

        if custom_transport_factory is not None:
            transport: httpx.BaseTransport = custom_transport_factory(plan)
        else:
            transport, _backend = build_pinned_httpx_transport(plan)

        with httpx.Client(
            transport=transport,
            timeout=timeout_cfg,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                current_method,
                plan.url,
                headers=req_headers,
                json=current_json,
            ) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    loc = resp.headers.get("location")
                    if not loc:
                        raise SSRFBlockedError(
                            f"Redirect HTTP {resp.status_code} missing Location header."
                        )
                    if hop >= max_redirects:
                        raise SSRFBlockedError(
                            f"Exceeded maximum allowed redirects ({max_redirects})."
                        )
                    next_url = urljoin(plan.url, loc)
                    # Strip sensitive auth headers when redirected across origins
                    if normalize_origin(next_url) != normalize_origin(plan.url):
                        req_headers.pop("Authorization", None)
                        req_headers.pop("authorization", None)
                        req_headers.pop("x-api-key", None)
                    if resp.status_code == 303:
                        current_method = "GET"
                        current_json = None
                    current_url = next_url
                    continue

                cl_header = resp.headers.get("content-length")
                if cl_header and cl_header.isdigit() and int(cl_header) > max_response_bytes:
                    raise SSRFBlockedError(
                        f"Response Content-Length ({cl_header}) exceeds maximum allowed size ({max_response_bytes})."
                    )

                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_response_bytes:
                        raise SSRFBlockedError(
                            f"Streamed response exceeded maximum allowed size ({max_response_bytes})."
                        )

                if resp.status_code >= 400:
                    resp_obj = httpx.Response(
                        status_code=resp.status_code,
                        headers=resp.headers,
                        content=bytes(buf),
                        request=resp.request,
                    )
                    resp_obj.raise_for_status()

                return GuardedHTTPResponse(
                    status_code=resp.status_code,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=bytes(buf),
                    final_url=plan.url,
                    pinned_ips_used=tuple(used_ips),
                )

    raise SSRFBlockedError("Redirect resolution loop terminated unexpectedly.")
