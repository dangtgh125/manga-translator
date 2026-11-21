"""Outbound network guard for user-configurable AI endpoints (SSRF protection).

- HTTPS only, fixed Responses API path.
- Host must be in the admin allowlist (unless internal mode is explicitly enabled by admin).
- Every resolved address is checked against loopback/private/link-local/metadata ranges.
- The connection is pinned to the validated IP (SNI/Host header carry the hostname), so DNS
  rebinding between check and connect is not possible.
- Redirects are never followed; response size and timeouts are bounded.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..config import get_settings

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
]
_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data", "localhost"}
_KNOWN_PUBLIC_HOSTS = {"api.openai.com", "api.anthropic.com", "translation.googleapis.com"}


class EndpointRejected(ValueError):
    pass


@dataclass(frozen=True)
class PinnedTarget:
    scheme: str
    host: str
    port: int
    ip: str
    path: str

    @property
    def pinned_url(self) -> str:
        ip = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"{self.scheme}://{ip}:{self.port}{self.path}"


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return True
    if addr.is_reserved or addr.is_unspecified:
        return True
    return any(addr in net for net in _BLOCKED_NETWORKS)


def resolve_all(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EndpointRejected("endpoint host cannot be resolved") from exc
    ips = sorted({str(info[4][0]) for info in infos})
    if not ips:
        raise EndpointRejected("endpoint host has no addresses")
    return ips


def validate_custom_endpoint(base_url: str, *, resolve: bool = True) -> PinnedTarget:
    s = get_settings()
    try:
        parts = urlsplit(base_url.strip())
    except ValueError as exc:
        raise EndpointRejected("malformed endpoint URL") from exc
    if parts.scheme != "https":
        raise EndpointRejected("only https endpoints are allowed")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise EndpointRejected("endpoint URL must not contain credentials, query or fragment")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise EndpointRejected("endpoint URL has no host")
    if host in _METADATA_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        if not s.allow_internal_endpoints:
            raise EndpointRejected("internal hosts are not allowed")
    port = parts.port or 443
    # Host literal IPs are rejected unless internal mode.
    try:
        ipaddress.ip_address(host)
        if not s.allow_internal_endpoints:
            raise EndpointRejected("IP-literal endpoints are not allowed")
    except ValueError:
        pass
    if not s.allow_internal_endpoints:
        allow = s.allowlisted_hosts | _KNOWN_PUBLIC_HOSTS
        if host not in allow and not any(host.endswith("." + a) for a in s.allowlisted_hosts):
            raise EndpointRejected("endpoint host is not in the administrator allowlist")
    path = parts.path.rstrip("/") or ""
    if not resolve:
        return PinnedTarget(parts.scheme, host, port, "", path)
    ips = resolve_all(host, port)
    if not s.allow_internal_endpoints:
        for ip in ips:
            if _is_blocked_ip(ip):
                raise EndpointRejected("endpoint resolves to a private or reserved address")
    return PinnedTarget(parts.scheme, host, port, ips[0], path)


def pinned_client(target: PinnedTarget, timeout: float | None = None) -> httpx.Client:
    """httpx client that connects to the validated IP while presenting the hostname for TLS/Host."""
    s = get_settings()
    return httpx.Client(
        timeout=httpx.Timeout(timeout or s.provider_timeout_seconds, connect=10.0),
        follow_redirects=False,
        verify=True,
        headers={"Host": target.host if target.port == 443 else f"{target.host}:{target.port}"},
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    )


def bounded_read(resp: httpx.Response, max_bytes: int | None = None) -> bytes:
    limit = max_bytes or get_settings().provider_max_response_bytes
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_bytes():
        total += len(chunk)
        if total > limit:
            resp.close()
            raise EndpointRejected("provider response too large")
        chunks.append(chunk)
    return b"".join(chunks)
