"""Ownership verification: prove control of the agent's domain with an expiring challenge.

Methods
- dns_txt:   publish ``aegis-verify=<value>`` as a TXT record at ``_aegis-challenge.<domain>``. The domain
             can be the endpoint host or a parent domain of it.
- https_file: serve ``<value>`` (text/plain) at ``https://<host>/.well-known/aegis-verification/<challenge id>.txt``.
             Redirects are not followed: the file must be served by the endpoint host itself.
- oauth_provider: not available in this deployment (no identity provider is configured); reported as such.

A publicly reachable endpoint is never treated as verified. Verification proves domain control only; access
to the agent's backend is a separate, explicit authorization step.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Dict, List

from app.connect.netguard import EgressError, parse_target, safe_request
from app.config.settings import get_settings
from app.core.timeutil import new_id, utcnow
from app.models import AgentConnection, ConnectionChallenge

METHODS: List[Dict[str, Any]] = [
    {"key": "dns_txt", "name": "DNS TXT record", "available": True,
     "detail": "Add a TXT record. Works for the endpoint host or a parent domain."},
    {"key": "https_file", "name": "HTTPS verification file", "available": True,
     "detail": "Serve a file from the endpoint host. Proves control of that host only."},
    {"key": "oauth_provider", "name": "OAuth identity provider", "available": False,
     "detail": "Not available: no OAuth identity provider is configured for this deployment."},
]


class VerificationError(Exception):
    pass


def allowed_domains(host: str) -> List[str]:
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)] or [host]


def issue(db, conn: AgentConnection, method: str, domain: str, actor: str) -> ConnectionChallenge:
    if method not in ("dns_txt", "https_file"):
        raise VerificationError("This verification method is not available")
    domain = (domain or conn.host).strip().lower().rstrip(".")
    if method == "https_file" and domain != conn.host:
        raise VerificationError("The HTTPS file method proves control of the endpoint host only")
    if domain != conn.host and domain not in allowed_domains(conn.host):
        raise VerificationError(f"{domain} is not {conn.host} or a parent domain of it")
    cid = new_id("chl")
    value = "aegis-verify=" + secrets.token_urlsafe(24)
    if method == "dns_txt":
        location = f"_aegis-challenge.{domain}"
    else:
        origin = parse_target(conn.submitted_url).origin
        location = f"{origin}/.well-known/aegis-verification/{cid}.txt"
    ch = ConnectionChallenge(id=cid, connection_id=conn.id, method=method, domain=domain, value=value, location=location,
                             status="pending", expires_at=utcnow() + timedelta(hours=get_settings().connect_challenge_ttl_hours),
                             created_by=actor, created_at=utcnow())
    db.add(ch)
    if conn.verification_status != "verified":
        conn.verification_status = "pending"
    db.flush()
    return ch


async def _txt_values(name: str) -> List[str]:
    import dns.asyncresolver
    import dns.exception
    import dns.resolver

    s = get_settings()
    servers = [x.strip() for x in s.connect_dns_servers.split(",") if x.strip()]
    r = dns.asyncresolver.Resolver(configure=not servers)
    if servers:
        r.nameservers = [x.split(":")[0] for x in servers]
        if ":" in servers[0]:
            r.port = int(servers[0].split(":")[1])
    r.lifetime = min(8.0, s.connect_timeout_seconds)
    try:
        ans = await r.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise VerificationError(f"DNS lookup for {name} failed ({type(exc).__name__})") from exc
    out = []
    for rr in ans:
        out.append(b"".join(rr.strings).decode("utf-8", errors="replace"))
    return out


async def check(ch: ConnectionChallenge) -> "tuple[bool, str]":
    """Returns (verified, detail). Does not mutate the challenge."""
    if ch.expires_at <= utcnow():
        return False, "The challenge has expired; create a new one"
    if ch.method == "dns_txt":
        values = await _txt_values(ch.location)
        if ch.value in values:
            return True, f"TXT record found at {ch.location}"
        return False, (f"No matching TXT record at {ch.location} ({len(values)} TXT value(s) found). "
                       "DNS changes can take a few minutes to appear.")
    try:
        r = await safe_request(ch.location, accept=("text/plain",), follow_redirects=False, max_bytes=4096)
    except EgressError as exc:
        return False, exc.message
    if r.status != 200:
        return False, f"{ch.location} answered HTTP {r.status}"
    if r.text().strip() != ch.value:
        return False, "The file was found but its content does not match the challenge value"
    return True, f"Verification file served by {parse_target(ch.location).host}"
