"""Protocol adapters for connected agents: A2A, MCP, OpenAPI and the documented AEGIS custom HTTP API.

Each adapter parses what an endpoint says about itself (untrusted claims), and, where the protocol
allows it, talks to the agent. All network access goes through ``netguard.safe_request``.

What each adapter can do is stated in ``capability_matrix``; unsupported capabilities are reported
as unsupported, never implied.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.connect.netguard import EgressError, SafeResponse, parse_target, safe_request
from app.security.redaction import redact_text

MCP_PROTOCOL_VERSION = "2025-06-18"
A2A_JSONRPC = "JSONRPC"
MAX_ITEMS = 100


def s(v: Any, n: int = 300) -> Optional[str]:
    """Untrusted string from a descriptor: type-checked, redacted, truncated."""
    if v is None:
        return None
    if not isinstance(v, (str, int, float)):
        return None
    return redact_text(str(v), n)


# --------------------------------------------------------------------------- classification
def looks_like_a2a(d: Any) -> bool:
    return (isinstance(d, dict) and isinstance(d.get("name"), str)
            and ("skills" in d or "capabilities" in d)
            and ("url" in d or "supportedInterfaces" in d or "additionalInterfaces" in d)
            and ("protocolVersion" in d or "defaultInputModes" in d))


def looks_like_openapi(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("paths"), dict) and (
        str(d.get("openapi", "")).startswith("3") or str(d.get("swagger", "")).startswith("2"))


def looks_like_custom(d: Any) -> bool:
    return isinstance(d, dict) and isinstance(d.get("aegis_agent_api"), str)


def same_host_endpoint(url: Any, submitted_host: str, warnings: List[str], what: str) -> Optional[str]:
    """Only endpoints on the submitted host can be connected; ownership is verified for that host."""
    if not isinstance(url, str) or not url:
        return None
    try:
        t = parse_target(url)
    except EgressError as exc:
        warnings.append(f"The {what} URL in the descriptor was rejected: {exc.message}")
        return None
    if t.host != submitted_host:
        warnings.append(f"The {what} points to a different host ({t.host}). Only {submitted_host} can be connected "
                        f"from this submission; submit a URL on {t.host} to connect that endpoint.")
        return None
    return t.url


# --------------------------------------------------------------------------- A2A
def parse_a2a(d: Dict[str, Any], host: str, warnings: List[str]) -> Dict[str, Any]:
    interfaces = [{"url": d.get("url"), "transport": d.get("preferredTransport") or A2A_JSONRPC}]
    for i in (d.get("additionalInterfaces") or d.get("supportedInterfaces") or [])[:10]:
        if isinstance(i, dict):
            interfaces.append({"url": i.get("url"), "transport": i.get("transport") or i.get("protocolBinding")})
    endpoint = None
    for i in interfaces:
        if str(i.get("transport") or "").upper() in ("JSONRPC", "JSON-RPC", "JSONRPC2"):
            endpoint = same_host_endpoint(i.get("url"), host, warnings, "A2A JSON-RPC endpoint")
            if endpoint:
                break
    if endpoint is None and not any(str(i.get("transport") or "").upper().startswith("JSON") for i in interfaces):
        warnings.append("The Agent Card offers no JSON-RPC interface; AEGIS can read the card but cannot send messages.")
    caps = d.get("capabilities") if isinstance(d.get("capabilities"), dict) else {}
    schemes = d.get("securitySchemes") if isinstance(d.get("securitySchemes"), dict) else {}
    auth = [{"name": s(k, 64), "type": s((v or {}).get("type"), 32), "scheme": s((v or {}).get("scheme"), 32),
             "detail": s((v or {}).get("description"), 200)} for k, v in list(schemes.items())[:10] if isinstance(v, dict)]
    provider = d.get("provider") if isinstance(d.get("provider"), dict) else {}
    return {
        "protocol": "a2a", "protocol_version": s(d.get("protocolVersion"), 32),
        "identity": {"name": s(d.get("name"), 128), "description": s(d.get("description"), 600),
                     "version": s(d.get("version"), 32), "provider": s(provider.get("organization"), 128),
                     "documentation_url": s(d.get("documentationUrl"), 300)},
        "endpoint": endpoint,
        "streaming": bool(caps.get("streaming")) if "streaming" in caps else None,
        "auth": {"required": bool(d.get("security")) or bool(auth) or None, "schemes": auth},
        "skills": [{"id": s(k.get("id"), 64), "name": s(k.get("name"), 128), "description": s(k.get("description"), 300),
                    "tags": [s(t, 32) for t in (k.get("tags") or [])[:8] if isinstance(t, str)]}
                   for k in (d.get("skills") or [])[:MAX_ITEMS] if isinstance(k, dict)],
        "tools": [],
        "input_modes": [s(m, 64) for m in (d.get("defaultInputModes") or [])[:10]],
    }


def _text_from_parts(parts: Any) -> List[str]:
    out = []
    for p in parts or []:
        if isinstance(p, dict) and (p.get("kind") == "text" or p.get("type") == "text" or "text" in p):
            if isinstance(p.get("text"), str):
                out.append(p["text"])
    return out


def a2a_reply_text(result: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    """Extract the agent's text from an A2A ``message/send`` result (a Message or a Task)."""
    meta: Dict[str, Any] = {}
    if not isinstance(result, dict):
        return None, meta
    texts: List[str] = []
    if result.get("kind") == "message" or "parts" in result:
        texts = _text_from_parts(result.get("parts"))
    else:  # Task
        meta["task_state"] = (result.get("status") or {}).get("state")
        for a in result.get("artifacts") or []:
            if isinstance(a, dict):
                texts += _text_from_parts(a.get("parts"))
        msg = (result.get("status") or {}).get("message")
        if not texts and isinstance(msg, dict):
            texts = _text_from_parts(msg.get("parts"))
        if not texts:
            for m in reversed(result.get("history") or []):
                if isinstance(m, dict) and m.get("role") == "agent":
                    texts = _text_from_parts(m.get("parts"))
                    break
    return ("\n".join(texts) if texts else None), meta


async def a2a_send(endpoint: str, text: str, auth: Dict[str, str], correlation_id: str, timeout: float) -> Dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
            "params": {"message": {"role": "user", "kind": "message", "messageId": str(uuid.uuid4()),
                                   "parts": [{"kind": "text", "text": text}],
                                   "metadata": {"aegis_correlation_id": correlation_id}},
                       "configuration": {"blocking": True, "acceptedOutputModes": ["text/plain", "text"]},
                       "metadata": {"aegis_correlation_id": correlation_id}}}
    r = await safe_request(endpoint, method="POST", json_body=body, auth_headers=auth or None, timeout=timeout,
                           follow_redirects=False, accept=("application/json",))
    _raise_for_status(r)
    d = r.json()
    if not isinstance(d, dict) or d.get("jsonrpc") != "2.0":
        raise EgressError("protocol_error", "The A2A endpoint did not return a JSON-RPC 2.0 response")
    if d.get("error"):
        err = d["error"] if isinstance(d["error"], dict) else {}
        raise EgressError("agent_error", f"A2A error {s(err.get('code'), 16)}: {s(err.get('message'), 200)}")
    reply, meta = a2a_reply_text(d.get("result"))
    return {"reply": reply, "tool_calls": None, "meta": meta}


# --------------------------------------------------------------------------- AEGIS custom HTTP agent API
def parse_custom(d: Dict[str, Any], host: str, warnings: List[str]) -> Dict[str, Any]:
    eps = d.get("endpoints") if isinstance(d.get("endpoints"), dict) else {}
    auth = d.get("auth") if isinstance(d.get("auth"), dict) else {}
    caps = d.get("capabilities") if isinstance(d.get("capabilities"), dict) else {}
    if d.get("aegis_agent_api") not in ("1.0", "1"):
        warnings.append(f"Unsupported AEGIS agent API version {s(d.get('aegis_agent_api'), 16)}; version 1.0 is supported.")
    return {
        "protocol": "custom_http", "protocol_version": s(d.get("aegis_agent_api"), 16),
        "identity": {"name": s(d.get("name"), 128), "description": s(d.get("description"), 600),
                     "version": s(d.get("version"), 32), "provider": s(d.get("provider"), 128),
                     "documentation_url": s(d.get("documentation_url"), 300)},
        "endpoint": same_host_endpoint(eps.get("message"), host, warnings, "message endpoint"),
        "streaming": False,
        "auth": {"required": auth.get("type") not in (None, "none"),
                 "schemes": [{"name": "manifest", "type": s(auth.get("type"), 32), "scheme": None,
                              "detail": s(auth.get("description"), 200)}],
                 "token_url": same_host_endpoint(auth.get("token_url"), host, warnings, "OAuth token endpoint")
                 if auth.get("token_url") else None,
                 "scopes": [s(x, 64) for x in (auth.get("scopes") or [])[:10] if isinstance(x, str)]},
        "skills": [],
        "tools": [{"name": s(t.get("name"), 64), "description": s(t.get("description"), 300),
                   "operation": s(t.get("operation"), 16)} for t in (d.get("tools") or [])[:MAX_ITEMS] if isinstance(t, dict)],
        "reports_tool_calls": bool(caps.get("tool_calls_reported")),
        "sentinel_gateway": bool(caps.get("sentinel_gateway")),
    }


async def custom_send(endpoint: str, text: str, auth: Dict[str, str], correlation_id: str, session_id: str,
                      test: Optional[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
    body = {"message": text, "session_id": session_id, "correlation_id": correlation_id, "test": test}
    r = await safe_request(endpoint, method="POST", json_body=body, auth_headers=auth or None, timeout=timeout,
                           follow_redirects=False, accept=("application/json",))
    _raise_for_status(r)
    d = r.json()
    if not isinstance(d, dict) or not (isinstance(d.get("reply"), str) or d.get("reply") is None):
        raise EgressError("protocol_error", "The agent did not return {\"reply\": string}")
    calls = []
    for c in (d.get("tool_calls") or [])[:50]:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            calls.append({"name": s(c["name"], 64), "status": s(c.get("status"), 24)})
    return {"reply": d.get("reply"), "tool_calls": calls if "tool_calls" in d else None,
            "usage": d.get("usage") if isinstance(d.get("usage"), dict) else None, "meta": {}}


# --------------------------------------------------------------------------- MCP (remote, streamable HTTP)
def _jsonrpc_from(r: SafeResponse, want_id: Any) -> Dict[str, Any]:
    if r.content_type == "text/event-stream":
        msgs = []
        for line in r.text().splitlines():
            if line.startswith("data:"):
                try:
                    msgs.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == want_id:
                return m
        raise EgressError("protocol_error", "No JSON-RPC response in the MCP event stream")
    d = r.json()
    if not isinstance(d, dict):
        raise EgressError("protocol_error", "The MCP endpoint returned an unexpected body")
    return d


async def mcp_probe(url: str, auth: Dict[str, str], timeout: float) -> Dict[str, Any]:
    """initialize + notifications/initialized + tools/list. Read-only: no tool is ever called."""
    hdrs = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
    accept = ("application/json", "text/event-stream")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "aegis-connect", "version": "1.0"}}}
    r = await safe_request(url, method="POST", headers=hdrs, json_body=init, auth_headers=auth or None, accept=accept,
                           timeout=timeout, follow_redirects=False)
    if r.status in (401, 403):
        return {"auth_required": True, "www_authenticate": s(r.headers.get("www-authenticate"), 300)}
    _raise_for_status(r)
    d = _jsonrpc_from(r, 1)
    res = d.get("result")
    if not isinstance(res, dict) or "protocolVersion" not in res:
        raise EgressError("not_mcp", "The endpoint did not answer the MCP initialize request")
    sid = r.headers.get("mcp-session-id")
    h2 = dict(hdrs, **({"Mcp-Session-Id": sid} if sid else {}))
    await safe_request(url, method="POST", headers=h2, json_body={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       auth_headers=auth or None, accept=accept, timeout=timeout, follow_redirects=False,
                       check_content_type=False)
    tools: List[Dict[str, Any]] = []
    if isinstance(res.get("capabilities"), dict) and "tools" in res["capabilities"]:
        r3 = await safe_request(url, method="POST", headers=h2, json_body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                auth_headers=auth or None, accept=accept, timeout=timeout, follow_redirects=False)
        _raise_for_status(r3)
        lst = (_jsonrpc_from(r3, 2).get("result") or {}).get("tools") or []
        for t in lst[:MAX_ITEMS]:
            if isinstance(t, dict):
                ann = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
                op = "read" if ann.get("readOnlyHint") else ("write" if ann.get("destructiveHint") or ann else None)
                tools.append({"name": s(t.get("name"), 64), "description": s(t.get("description"), 300), "operation": op})
    info = res.get("serverInfo") if isinstance(res.get("serverInfo"), dict) else {}
    return {"auth_required": False, "protocol_version": s(res.get("protocolVersion"), 32),
            "name": s(info.get("name"), 128), "version": s(info.get("version"), 32),
            "instructions": s(res.get("instructions"), 600), "tools": tools,
            "has_tools": "tools" in (res.get("capabilities") or {})}


# --------------------------------------------------------------------------- OpenAPI
def parse_openapi(d: Dict[str, Any], host: str, warnings: List[str]) -> Dict[str, Any]:
    info = d.get("info") if isinstance(d.get("info"), dict) else {}
    ops = []
    for path, item in list((d.get("paths") or {}).items())[:500]:
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                continue
            ops.append({"name": s(op.get("operationId") or f"{method.upper()} {path}", 96),
                        "description": s(op.get("summary") or op.get("description"), 300),
                        "operation": "read" if method.lower() == "get" else "write"})
            if len(ops) >= MAX_ITEMS:
                break
    comps = d.get("components") if isinstance(d.get("components"), dict) else {}
    schemes = comps.get("securitySchemes") or d.get("securityDefinitions") or {}
    auth = [{"name": s(k, 64), "type": s((v or {}).get("type"), 32), "scheme": s((v or {}).get("scheme"), 32),
             "detail": s((v or {}).get("description"), 200)} for k, v in list(schemes.items())[:10] if isinstance(v, dict)]
    servers = [s(x.get("url"), 300) for x in (d.get("servers") or [])[:5] if isinstance(x, dict)]
    for sv in servers:
        if sv and sv.startswith("http"):
            same_host_endpoint(sv, host, warnings, "OpenAPI server")
    return {
        "protocol": "openapi", "protocol_version": s(d.get("openapi") or d.get("swagger"), 16),
        "identity": {"name": s(info.get("title"), 128), "description": s(info.get("description"), 600),
                     "version": s(info.get("version"), 32), "provider": s((info.get("contact") or {}).get("name"), 128),
                     "documentation_url": None},
        "endpoint": None, "streaming": None,
        "auth": {"required": bool(d.get("security")) or bool(auth) or None, "schemes": auth},
        "skills": [], "tools": ops, "servers": servers,
    }


# --------------------------------------------------------------------------- helpers
def _raise_for_status(r: SafeResponse) -> None:
    if r.status in (401, 403):
        raise EgressError("auth_rejected", f"The endpoint rejected the request (HTTP {r.status}); check the credential")
    if r.status == 429:
        raise EgressError("rate_limited", "The endpoint is rate limiting AEGIS (HTTP 429)")
    if r.status >= 400:
        raise EgressError("http_status", f"The endpoint answered HTTP {r.status}")


def _cap(supported: Any, detail: str) -> Dict[str, Any]:
    return {"supported": supported, "detail": detail}


def capability_matrix(protocol: str, desc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """What AEGIS can do with this endpoint. ``supported`` is True, False or "conditional"."""
    enforcement = _cap("conditional", "Only after the owner routes tool calls through SENTINEL (service token and "
                                      "/api/v1/sentinel/evaluate before every call). A connected URL alone is never protected.")
    if protocol == "a2a":
        can_talk = bool(desc.get("endpoint"))
        return {
            "discovery": _cap(True, "Agent Card"),
            "capability_inspection": _cap(True, f"{len(desc.get('skills') or [])} skill(s) declared in the Agent Card"),
            "interaction": _cap(can_talk, "A2A message/send over JSON-RPC" if can_talk else "No usable JSON-RPC endpoint on this host"),
            "streaming": _cap(False, "The card declares streaming; AEGIS uses blocking message/send only"
                              if desc.get("streaming") else "Not declared; AEGIS uses blocking message/send"),
            "tool_discovery": _cap(False, "A2A exposes skills, not tools. Tools become visible when the agent registers them with SENTINEL."),
            "execution_tracing": _cap("conditional", "Only tool calls the agent reports to SENTINEL with the correlation ID AEGIS "
                                                     "sends in message metadata (aegis_correlation_id)"),
            "security_testing": _cap(can_talk, "Black-box scenarios judged from the reply, plus tool-call checks when the agent reports to SENTINEL"
                                     if can_talk else "Needs a JSON-RPC endpoint"),
            "runtime_enforcement": enforcement,
        }
    if protocol == "custom_http":
        can_talk = bool(desc.get("endpoint"))
        return {
            "discovery": _cap(True, "AEGIS agent manifest (/.well-known/aegis-agent.json)"),
            "capability_inspection": _cap(True, "Manifest capabilities"),
            "interaction": _cap(can_talk, "POST message endpoint" if can_talk else "No message endpoint on this host"),
            "streaming": _cap(False, "Not part of AEGIS agent API 1.0"),
            "tool_discovery": _cap(bool(desc.get("tools")), f"{len(desc.get('tools') or [])} tool(s) declared in the manifest (claims)"),
            "execution_tracing": _cap("conditional", "Tool calls the agent reports in replies are recorded as agent-reported; "
                                                     "enforced calls appear when the agent uses SENTINEL"
                                      if desc.get("reports_tool_calls") else "The manifest does not report tool calls"),
            "security_testing": _cap(can_talk, "Black-box scenarios plus SENTINEL tool-call checks" if can_talk else "Needs a message endpoint"),
            "runtime_enforcement": enforcement,
        }
    if protocol == "mcp":
        return {
            "discovery": _cap(True, "MCP initialize"),
            "capability_inspection": _cap(True, "Server capabilities from initialize"),
            "interaction": _cap(False, "An MCP server provides tools to an agent; it is not an agent AEGIS can converse with"),
            "streaming": _cap(False, "Not used by AEGIS"),
            "tool_discovery": _cap(True, "tools/list (AEGIS never calls the tools)"),
            "execution_tracing": _cap(False, "MCP servers do not report agent execution"),
            "security_testing": _cap(False, "No model to test. Connect the agent that uses this server (A2A or custom HTTP)."),
            "runtime_enforcement": _cap("conditional", "When the agent that calls these tools asks SENTINEL before each call"),
        }
    if protocol == "openapi":
        return {
            "discovery": _cap(True, "OpenAPI document"),
            "capability_inspection": _cap(True, f"{len(desc.get('tools') or [])} operation(s)"),
            "interaction": _cap(False, "An OpenAPI document describes an API, not a conversational agent. Expose the AEGIS agent API or A2A."),
            "streaming": _cap(False, "Not used"),
            "tool_discovery": _cap(True, "Operations listed as candidate tools (claims, not registered with SENTINEL)"),
            "execution_tracing": _cap(False, "Not available from an API description"),
            "security_testing": _cap(False, "Not available without an agent interaction endpoint"),
            "runtime_enforcement": _cap("conditional", "When the agent calling this API asks SENTINEL before each call"),
        }
    return {k: _cap(False, "No supported agent protocol was found") for k in (
        "discovery", "capability_inspection", "interaction", "streaming", "tool_discovery", "execution_tracing",
        "security_testing", "runtime_enforcement")}
