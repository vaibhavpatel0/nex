"""Outbound requests to developer-submitted URLs, with SSRF defences.

Every URL handled by the connection center is untrusted. ``safe_request`` is the only way this
package talks to the network:

- HTTPS only. Plain HTTP only for host names listed in AEGIS_CONNECT_ALLOW_HTTP_HOSTS (development).
- No user-info in URLs, no IP literals (ownership is proven per domain), ports 443 or 1024-65535.
- DNS is resolved by AEGIS. Every returned address must be globally routable; loopback, private,
  link-local (including cloud metadata 169.254.169.254), CGNAT, multicast, reserved and unspecified
  addresses are refused. AEGIS_CONNECT_ALLOWED_PRIVATE_HOSTS lists exact host names exempted for local
  development and tests.
- DNS rebinding: the connection is made to the address that was validated (the URL is rewritten to the
  IP; Host and TLS SNI carry the name, and the certificate is verified against the name).
- Redirects are followed manually (at most 3), and every hop is validated again. Requests that carry
  credentials never follow a redirect to a different host, and credentials are attached only for the
  host they were issued for.
- Environment proxies are ignored, timeouts are bounded, bodies are read up to a size cap and the
  content type is checked against what the caller expects.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config.settings import get_settings

REDIRECTS = {301, 302, 303, 307, 308}
MAX_HOPS = 3
USER_AGENT = "AEGIS-Connect/1.0 (+agent connection verification)"


class EgressError(Exception):
    """A request was refused or failed. ``code`` is stable; ``message`` is safe to show to users."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    path_qs: str
    dev_exception: bool = False

    @property
    def origin(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return f"{self.scheme}://{self.host}" + ("" if self.port == default else f":{self.port}")


@dataclass
class SafeResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    content_type: str
    final_url: str
    address: str
    hops: List[str] = field(default_factory=list)

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise EgressError("invalid_json", "The endpoint did not return valid JSON") from exc

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def parse_target(url: str) -> Target:
    s = get_settings()
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise EgressError("invalid_url", "Enter a full URL of at most 2048 characters")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if parts.username or parts.password or "@" in parts.netloc:
        raise EgressError("invalid_url", "URLs with embedded user names or passwords are not accepted")
    raw_host = (parts.hostname or "").rstrip(".").lower()
    if not raw_host:
        raise EgressError("invalid_url", "The URL has no host name")
    try:
        host = raw_host if _is_ip(raw_host) else raw_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EgressError("invalid_url", "The host name is not valid") from exc
    dev = host in s.connect_private_allowlist or host in s.connect_http_allowlist
    if scheme == "http":
        if host not in s.connect_http_allowlist:
            raise EgressError("https_required", "Only HTTPS endpoints can be connected")
    elif scheme != "https":
        raise EgressError("https_required", "Only HTTPS endpoints can be connected")
    if _is_ip(host) and host not in s.connect_private_allowlist:
        raise EgressError("ip_literal", "Use the agent's domain name, not an IP address. Ownership is verified per domain.")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise EgressError("invalid_url", "The port is not valid") from exc
    if not (port in (443, 80) or 1024 <= port <= 65535):
        raise EgressError("port_not_allowed", "Only port 443 or ports 1024-65535 are allowed")
    if port == 80 and scheme == "https":
        raise EgressError("port_not_allowed", "HTTPS on port 80 is not allowed")
    path_qs = parts.path or "/"
    if parts.query:
        path_qs += "?" + parts.query
    hn = f"[{host}]" if ":" in host else host
    netloc = hn if port in (443, 80) and (port == 443) == (scheme == "https") else f"{hn}:{port}"
    return Target(url=urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), scheme=scheme, host=host,
                  port=port, path_qs=path_qs, dev_exception=dev)


def address_allowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(addr.is_global) and not (addr.is_multicast or addr.is_reserved or addr.is_unspecified)


async def resolve(host: str) -> List[str]:
    s = get_settings()
    if _is_ip(host):
        return [host.strip("[]")]
    servers = [x.strip() for x in s.connect_dns_servers.split(",") if x.strip()]
    timeout = min(5.0, s.connect_timeout_seconds)
    if servers:
        import dns.asyncresolver
        import dns.exception
        import dns.resolver

        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = [x.split(":")[0] for x in servers]
        if ":" in servers[0]:
            r.port = int(servers[0].split(":")[1])
        r.lifetime = timeout
        out: List[str] = []
        for rtype in ("A", "AAAA"):
            try:
                ans = await r.resolve(host, rtype)
                out += [a.to_text() for a in ans]
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                continue
            except dns.exception.Timeout as exc:
                raise EgressError("dns_timeout", f"DNS lookup for {host} timed out") from exc
        if not out:
            raise EgressError("dns_failed", f"{host} does not resolve")
        return out
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout)
    except (socket.gaierror, asyncio.TimeoutError) as exc:
        raise EgressError("dns_failed", f"{host} does not resolve") from exc
    return list(dict.fromkeys(i[4][0] for i in infos))


async def validated_address(t: Target) -> str:
    ips = await resolve(t.host)
    if t.host in get_settings().connect_private_allowlist:
        return ips[0]
    bad = [ip for ip in ips if not address_allowed(ip)]
    if bad:
        raise EgressError("address_not_allowed",
                          f"{t.host} resolves to a private, loopback, link-local or reserved address; requests to it are refused")
    return ips[0]


@lru_cache(maxsize=4)
def _tls_context(extra_ca: str) -> ssl.SSLContext:
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    if extra_ca:
        ctx.load_verify_locations(cafile=extra_ca)
    return ctx


def _mimetype(ct: str) -> str:
    return (ct or "").split(";")[0].strip().lower()


def content_type_ok(mimetype: str, accept: Sequence[str]) -> bool:
    for a in accept:
        if a == "application/json" and mimetype.endswith("+json"):
            return True
        if mimetype == a:
            return True
    return False


async def safe_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    auth_headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    form_body: Optional[Dict[str, str]] = None,
    accept: Iterable[str] = ("application/json",),
    max_bytes: Optional[int] = None,
    timeout: Optional[float] = None,
    follow_redirects: bool = True,
    check_content_type: bool = True,
) -> SafeResponse:
    s = get_settings()
    accept = tuple(accept)
    limit = max_bytes or s.connect_max_response_bytes
    to = timeout or s.connect_timeout_seconds
    first = parse_target(url)
    cred_host = (first.host, first.port) if auth_headers else None
    hops: List[str] = []
    current = first
    for _hop in range(MAX_HOPS + 1):
        ip = await validated_address(current)
        hdrs = {"User-Agent": USER_AGENT, "Accept": ", ".join(accept) or "*/*"}
        hdrs.update(headers or {})
        if auth_headers:
            if (current.host, current.port) != cred_host:
                raise EgressError("redirect_refused", "A redirect to a different host was refused; credentials are only sent to the host they were issued for")
            hdrs.update(auth_headers)
        hdrs["Host"] = current.host if current.port in (80, 443) else f"{current.host}:{current.port}"
        ip_host = f"[{ip}]" if ":" in ip else ip
        target_url = f"{current.scheme}://{ip_host}:{current.port}{current.path_qs}"
        ext = {"sni_hostname": current.host} if current.scheme == "https" else {}
        verify: Any = _tls_context(s.connect_ca_bundle) if current.scheme == "https" else False
        try:
            async with httpx.AsyncClient(verify=verify, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(to)) as client:
                req = client.build_request(method, target_url, headers=hdrs, json=json_body, data=form_body, extensions=ext)
                resp = await client.send(req, stream=True)
                try:
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > limit:
                            raise EgressError("response_too_large", f"The response exceeded the {limit} byte limit")
                finally:
                    await resp.aclose()
        except EgressError:
            raise
        except httpx.TimeoutException as exc:
            raise EgressError("timeout", f"{current.host} did not answer within {to:g}s") from exc
        except httpx.ConnectError as exc:
            msg = str(exc)
            if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate" in msg.lower():
                raise EgressError("tls_invalid", f"The TLS certificate of {current.host} could not be verified") from exc
            raise EgressError("unreachable", f"Could not connect to {current.host}") from exc
        except httpx.HTTPError as exc:
            raise EgressError("http_error", f"The request to {current.host} failed ({type(exc).__name__})") from exc
        hops.append(current.url)
        rh = {k.lower(): v for k, v in resp.headers.items()}
        if resp.status_code in REDIRECTS and follow_redirects and method in ("GET", "HEAD"):
            loc = rh.get("location")
            if not loc:
                raise EgressError("redirect_invalid", "Redirect without a Location header")
            current = parse_target(urljoin(current.url, loc))
            continue
        if resp.status_code in REDIRECTS:
            raise EgressError("redirect_refused", "The endpoint answered with a redirect, which is not followed for this request")
        mt = _mimetype(rh.get("content-type", ""))
        if check_content_type and 200 <= resp.status_code < 300 and accept and not content_type_ok(mt, accept):
            raise EgressError("unexpected_content_type",
                              f"Expected {' or '.join(accept)} but the endpoint returned {mt or 'no content type'}")
        return SafeResponse(status=resp.status_code, headers=rh, body=bytes(body), content_type=mt,
                            final_url=current.url, address=ip, hops=hops)
    raise EgressError("too_many_redirects", f"More than {MAX_HOPS} redirects")
