"""URL discovery: what kind of endpoint is this, and what does it say about itself?

Discovery only issues GET requests, except the MCP ``initialize`` handshake, which is sent only when
the developer chose MCP or the URL path looks like an MCP endpoint. Nothing discovered here is proof
of ownership; it is recorded as the endpoint's own claims.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.connect import protocols as P
from app.connect.netguard import EgressError, parse_target, safe_request
from app.config.settings import get_settings

WELL_KNOWN = (
    ("custom_http", "/.well-known/aegis-agent.json"),
    ("a2a", "/.well-known/agent-card.json"),
    ("a2a", "/.well-known/agent.json"),
    ("openapi", "/openapi.json"),
)
HINTS = ("auto", "a2a", "mcp", "openapi", "custom_http")
CLAIMS_NOTE = ("Everything below is what the endpoint says about itself. It is recorded as a claim and is not proof "
               "of ownership or of what the agent will actually do.")


def _parse(kind: str, d: Any, host: str, warnings: List[str]) -> Optional[Dict[str, Any]]:
    if kind == "custom_http" and P.looks_like_custom(d):
        return P.parse_custom(d, host, warnings)
    if kind == "a2a" and P.looks_like_a2a(d):
        return P.parse_a2a(d, host, warnings)
    if kind == "openapi" and P.looks_like_openapi(d):
        return P.parse_openapi(d, host, warnings)
    return None


def _classify_body(d: Any, host: str, warnings: List[str]) -> Optional[Dict[str, Any]]:
    for kind in ("custom_http", "a2a", "openapi"):
        out = _parse(kind, d, host, warnings)
        if out:
            return out
    return None


async def discover(url: str, hint: str = "auto", auth: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if hint not in HINTS:
        raise EgressError("invalid_hint", "Unknown protocol hint")
    t = parse_target(url)
    timeout = get_settings().connect_timeout_seconds
    evidence: List[Dict[str, Any]] = []
    warnings: List[str] = []
    found: Optional[Dict[str, Any]] = None
    website = False

    def note(u: str, status: Any, ct: str, finding: str) -> None:
        evidence.append({"url": u, "status": status, "content_type": ct, "finding": finding})

    # 1. The submitted URL itself (GET).
    if hint != "mcp":
        try:
            r = await safe_request(t.url, accept=("application/json", "text/html"), check_content_type=False,
                                   timeout=timeout, auth_headers=auth or None, follow_redirects=not auth)
            if r.content_type in ("application/json",) or r.content_type.endswith("+json"):
                try:
                    found = _classify_body(r.json(), t.host, warnings) if r.status < 400 else None
                except EgressError:
                    found = None
                note(t.url, r.status, r.content_type, f"{found['protocol']} descriptor" if found else "JSON, not a known agent descriptor")
            else:
                website = r.content_type == "text/html"
                note(t.url, r.status, r.content_type, "HTML page (a website or chat front end, not an agent backend)"
                     if website else "Not JSON")
        except EgressError as exc:
            if exc.code in ("address_not_allowed", "https_required", "ip_literal", "invalid_url", "port_not_allowed", "dns_failed"):
                raise
            note(t.url, None, "", exc.message)

    path = t.path_qs.split("?")[0].lower()
    # 2. MCP handshake: only when asked, or when the path says so (before well-known probes).
    if found is None and (hint == "mcp" or (hint == "auto" and ("mcp" in path or path.endswith("/sse")))):
        try:
            m = await P.mcp_probe(t.url, auth or {}, timeout)
            note(t.url, 200 if not m.get("auth_required") else 401, "", "MCP server" + (" (authentication required)"
                                                                                       if m.get("auth_required") else ""))
            found = {
                "protocol": "mcp", "protocol_version": m.get("protocol_version"),
                "identity": {"name": m.get("name"), "description": m.get("instructions"), "version": m.get("version"),
                             "provider": None, "documentation_url": None},
                "endpoint": t.url, "streaming": None,
                "auth": {"required": bool(m.get("auth_required")),
                         "schemes": [{"name": "mcp", "type": "http", "scheme": "bearer", "detail": m.get("www_authenticate")}]
                         if m.get("auth_required") else []},
                "skills": [], "tools": m.get("tools") or [], "tools_listed": not m.get("auth_required"),
            }
        except EgressError as exc:
            note(t.url, None, "", f"MCP handshake: {exc.message}")

    # 3. Well-known locations on the same origin.
    if found is None and hint != "mcp":
        for kind, path in WELL_KNOWN:
            if hint != "auto" and kind != hint:
                continue
            u = t.origin + path
            try:
                r = await safe_request(u, accept=("application/json",), check_content_type=False, timeout=timeout)
            except EgressError as exc:
                note(u, None, "", exc.message)
                continue
            if r.status != 200 or not (r.content_type == "application/json" or r.content_type.endswith("+json")):
                note(u, r.status, r.content_type, "Not found" if r.status == 404 else "No descriptor")
                continue
            try:
                body = r.json()
            except EgressError:
                note(u, r.status, r.content_type, "Invalid JSON")
                continue
            found = _parse(kind, body, t.host, warnings)
            note(u, r.status, r.content_type, f"{kind} descriptor" if found else "JSON, not a valid descriptor")
            if found:
                break

    if found is None:
        found = {"protocol": "none", "protocol_version": None,
                 "identity": {"name": None, "description": None, "version": None, "provider": None, "documentation_url": None},
                 "endpoint": None, "streaming": None, "auth": {"required": None, "schemes": []}, "skills": [], "tools": []}
        warnings.append("No supported agent protocol was found at this URL. A website or chatbot page is not an agent "
                        "backend. Submit the agent's A2A Agent Card, MCP endpoint, OpenAPI document or AEGIS agent manifest."
                        if website else "No supported agent protocol was found at this URL.")
    found.update(submitted_url=t.url, host=t.host, origin=t.origin, evidence=evidence, warnings=warnings,
                 claims_note=CLAIMS_NOTE, dev_exception=t.dev_exception)
    return found
