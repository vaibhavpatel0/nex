"""Integration fixture: real HTTPS agent endpoints for the connection-center tests.

Runs as its own process (uvicorn with TLS, certificate from a test CA). Requests are routed by Host:

  agent.test        AEGIS agent API manifest + message endpoint. Requires a scoped bearer token. A deliberately
                    naive agent: it repeats CANARY markers it is asked to print. In protected mode it routes its
                    one tool (delete_record) through SENTINEL and executes only when SENTINEL says so.
  a2a.agent.test    A2A Agent Card + JSON-RPC message/send. A careful agent that refuses injected instructions.
  mcp.agent.test    MCP streamable-HTTP server (initialize, tools/list). Tool calls are never expected.
  api.agent.test    OpenAPI document only.
  web.agent.test    An ordinary HTML website.
  cross.agent.test  Agent Card whose endpoint points to a different host.
  redir.agent.test  Agent Card URL that redirects to internal.test (resolves to 127.0.0.1, not allowlisted).
  big.agent.test    Oversized JSON.
  oauth.agent.test  AEGIS manifest with OAuth 2.0 client credentials (token endpoint on the same host).

Test control (any host): POST /__fixture/verification-file, POST /__fixture/sentinel, GET /__fixture/state.
"""
from __future__ import annotations

import argparse
import re
import secrets
import uuid
from typing import Any, Dict

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

BEARER = "fixture-scoped-token-7f3a91c2"
OAUTH_CLIENT = ("aegis-test-client", "fixture-client-secret-55aa")
STATE: Dict[str, Any] = {"files": {}, "sentinel": None, "records": {"R-1": "customer note"}, "messages": [], "tokens": set(),
                         "registered": False}
PORT = 0
DEFAULT_HOST = ""  # demo mode: requests for an unknown Host are served as this fixture host
SCHEME = "https"
KNOWN = {"agent.test", "a2a.agent.test", "mcp.agent.test", "api.agent.test", "web.agent.test", "cross.agent.test",
         "redir.agent.test", "big.agent.test", "oauth.agent.test", "internal.test"}
app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


def host(req: Request) -> str:
    h = (req.headers.get("host") or "").split(":")[0].lower()
    return h if h in KNOWN or not DEFAULT_HOST else DEFAULT_HOST


def origin(req: Request) -> str:
    """The origin the caller used, so descriptor URLs stay on the submitted host."""
    return f"{SCHEME}://{req.headers.get('host')}"


# ---------------------------------------------------------------- control
@app.post("/__fixture/verification-file")
async def set_file(req: Request):
    d = await req.json()
    STATE["files"][d["id"]] = d["value"]
    return {"ok": True}


@app.post("/__fixture/sentinel")
async def set_sentinel(req: Request):
    STATE["sentinel"] = await req.json()
    STATE["registered"] = False
    return {"ok": True}


@app.get("/__fixture/state")
async def state():
    return {"records": STATE["records"], "messages": len(STATE["messages"])}


@app.get("/.well-known/aegis-verification/{name}")
async def verification(name: str):
    cid = name.removesuffix(".txt")
    if cid not in STATE["files"]:
        return PlainTextResponse("not found", status_code=404)
    return PlainTextResponse(STATE["files"][cid])


# ---------------------------------------------------------------- discovery documents
@app.get("/.well-known/aegis-agent.json")
async def manifest(req: Request):
    h = host(req)
    if h == "agent.test":
        return {"aegis_agent_api": "1.0", "name": "Fixture Support Agent", "description": "Naive fixture agent", "version": "2.1.0",
                "provider": "Fixture Labs", "endpoints": {"message": f"{origin(req)}/aegis/message"},
                "auth": {"type": "bearer", "description": "Scoped agent token"},
                "capabilities": {"tool_calls_reported": True, "sentinel_gateway": True},
                "tools": [{"name": "delete_record", "description": "Delete a record", "operation": "write"}]}
    if h == "oauth.agent.test":
        return {"aegis_agent_api": "1.0", "name": "OAuth Fixture Agent", "version": "1.0",
                "endpoints": {"message": f"{origin(req)}/aegis/message"},
                "auth": {"type": "oauth2_client_credentials", "token_url": f"{origin(req)}/oauth/token", "scopes": ["agent.invoke"]}}
    return JSONResponse({"detail": "not found"}, status_code=404)


@app.get("/.well-known/agent-card.json")
async def card(req: Request):
    h = host(req)
    if h == "a2a.agent.test":
        return {"protocolVersion": "0.3.0", "name": "Fixture Travel Agent", "description": "Careful A2A fixture agent",
                "version": "0.9.1", "url": f"{origin(req)}/a2a", "preferredTransport": "JSONRPC",
                "provider": {"organization": "Fixture Labs", "url": "https://fixture.example"},
                "capabilities": {"streaming": True, "pushNotifications": False},
                "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
                "skills": [{"id": "book", "name": "Book trips", "description": "Plans trips", "tags": ["travel"]}]}
    if h == "cross.agent.test":
        return {"protocolVersion": "0.3.0", "name": "Cross-host card", "url": "https://elsewhere.test/a2a",
                "preferredTransport": "JSONRPC", "capabilities": {}, "defaultInputModes": ["text/plain"], "skills": []}
    if h == "redir.agent.test":
        return RedirectResponse(f"https://internal.test:{PORT}/.well-known/agent-card.json", status_code=302)
    if h == "internal.test":
        return {"protocolVersion": "0.3.0", "name": "INTERNAL", "url": "x", "capabilities": {}, "defaultInputModes": [], "skills": []}
    if h == "big.agent.test":
        return JSONResponse({"name": "big", "pad": "x" * 1_200_000})
    return JSONResponse({"detail": "not found"}, status_code=404)


@app.get("/openapi.json")
async def openapi_doc(req: Request):
    if host(req) != "api.agent.test":
        return JSONResponse({"detail": "not found"}, status_code=404)
    return {"openapi": "3.1.0", "info": {"title": "Fixture Orders API", "version": "3.2.0"},
            "servers": [{"url": f"{origin(req)}/v1"}],
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}, "security": [{"bearer": []}],
            "paths": {"/v1/orders/{id}": {"get": {"operationId": "getOrder", "summary": "Read an order"},
                                          "delete": {"operationId": "deleteOrder", "summary": "Delete an order"}}}}


@app.get("/")
async def root(req: Request):
    if host(req) == "web.agent.test":
        return HTMLResponse("<!doctype html><html><body><h1>Chat with our bot</h1><script src='/chat.js'></script></body></html>")
    return JSONResponse({"detail": "not found"}, status_code=404)


# ---------------------------------------------------------------- OAuth
@app.post("/oauth/token")
async def token(req: Request):
    form = await req.form()
    if (form.get("grant_type"), form.get("client_id"), form.get("client_secret")) != ("client_credentials", *OAUTH_CLIENT):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    t = "oat_" + secrets.token_hex(12)
    STATE["tokens"].add(t)
    return {"access_token": t, "token_type": "Bearer", "expires_in": 600, "scope": form.get("scope")}


# ---------------------------------------------------------------- AEGIS agent API (naive agent)
def _authorized(req: Request) -> bool:
    auth = req.headers.get("authorization", "")
    if host(req) == "oauth.agent.test":
        return auth.startswith("Bearer ") and auth[7:] in STATE["tokens"]
    return auth == f"Bearer {BEARER}"


async def _sentinel(method: str, path: str, body: Any) -> Dict[str, Any]:
    s = STATE["sentinel"]
    async with httpx.AsyncClient(base_url=s["base_url"] + "/api/v1/sentinel", trust_env=False, timeout=10,
                                 headers={"Authorization": f"Bearer {s['token']}"}) as c:
        r = await c.request(method, path, json=body)
        r.raise_for_status()
        return r.json()


async def _delete_record(record: str, correlation_id: str, task: str) -> str:
    s = STATE["sentinel"]
    ns = s["namespace"]
    if not STATE["registered"]:
        await _sentinel("PUT", "/tools", {"namespace": ns, "tools": [
            {"name": f"{ns}.delete_record", "description": "Delete a record by id", "operation": "write", "sensitivity": "high",
             "resource_kind": "record", "args_schema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^R-[0-9]{1,4}$"}},
                                                        "required": ["record_id"], "additionalProperties": False}},
            {"name": f"{ns}.read_record", "description": "Read a record by id", "operation": "read", "sensitivity": "low",
             "resource_kind": "record", "args_schema": {"type": "object", "properties": {"record_id": {"type": "string", "pattern": "^R-[0-9]{1,4}$"}},
                                                        "required": ["record_id"], "additionalProperties": False}}]})
        STATE["registered"] = True
    cid = correlation_id if re.fullmatch(r"cor_[a-f0-9]{20}", correlation_id or "") else "cor_" + uuid.uuid4().hex[:20]
    await _sentinel("POST", "/sessions", {"correlation_id": cid, "agent_key": s["agent_key"], "task": task[:4000]})
    d = await _sentinel("POST", "/evaluate", {"correlation_id": cid, "agent_key": s["agent_key"], "tool": f"{ns}.delete_record",
                                              "args": {"record_id": record}, "resource": {}})
    if not d.get("execute"):
        return f"I did not delete {record}: SENTINEL decided {d['decision']} ({d.get('matched_policy')})."
    STATE["records"].pop(record, None)
    await _sentinel("POST", f"/tool-calls/{d['tool_call_id']}/result", {"status": "executed", "result_summary": {"deleted": record}})
    return f"Deleted {record}."


@app.post("/aegis/message")
async def message(req: Request):
    if not _authorized(req):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    d = await req.json()
    text = d.get("message") or ""
    STATE["messages"].append(d)
    m = re.search(r"delete record (R-\d+)", text, re.I)
    if m and STATE["sentinel"]:
        reply = await _delete_record(m.group(1), d.get("correlation_id") or "", text)
        return {"reply": reply, "tool_calls": [{"name": "delete_record", "status": "requested"}]}
    canaries = re.findall(r"CANARY-[A-Z0-9-]+[A-Z0-9]", text)
    if canaries:  # naive: does what injected text says
        return {"reply": "Sure. " + " ".join(canaries), "tool_calls": []}
    if "READY" in text:
        return {"reply": "READY", "tool_calls": []}
    if "connection check" in text.lower():
        return {"reply": "OK", "tool_calls": []}
    return {"reply": "Here is a short answer.", "tool_calls": []}


# ---------------------------------------------------------------- A2A JSON-RPC (careful agent)
@app.post("/a2a")
async def a2a(req: Request):
    d = await req.json()
    if d.get("method") != "message/send":
        return {"jsonrpc": "2.0", "id": d.get("id"), "error": {"code": -32601, "message": "Method not found"}}
    msg = d["params"]["message"]
    text = " ".join(p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text")
    if "CANARY-" in text or "OVERRIDE" in text or "API key" in text or "password" in text:
        out = "I can't help with that request."
    elif "READY" in text:
        out = "READY"
    elif "connection check" in text.lower():
        out = "OK"
    else:
        out = "Happy to help with travel plans."
    task = {"kind": "task", "id": str(uuid.uuid4()), "contextId": str(uuid.uuid4()),
            "status": {"state": "completed"}, "artifacts": [{"artifactId": "a1", "parts": [{"kind": "text", "text": out}]}]}
    return {"jsonrpc": "2.0", "id": d.get("id"), "result": task}


# ---------------------------------------------------------------- MCP streamable HTTP
@app.post("/mcp")
async def mcp(req: Request):
    d = await req.json()
    if d.get("method") == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": d["id"], "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fixture-files", "version": "1.4.0"}, "instructions": "File tools"}},
            headers={"Mcp-Session-Id": "sess-" + secrets.token_hex(4)})
    if d.get("method") == "notifications/initialized":
        return Response(status_code=202)
    if d.get("method") == "tools/list":
        body = {"jsonrpc": "2.0", "id": d["id"], "result": {"tools": [
            {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
            {"name": "delete_file", "description": "Delete a file", "inputSchema": {"type": "object"}, "annotations": {"destructiveHint": True}}]}}
        import json as _j
        return Response("event: message\ndata: " + _j.dumps(body) + "\n\n", media_type="text/event-stream")
    if d.get("method") == "tools/call":
        STATE["mcp_tool_called"] = True
        return {"jsonrpc": "2.0", "id": d.get("id"), "error": {"code": -1, "message": "fixture never expects tool calls"}}
    return {"jsonrpc": "2.0", "id": d.get("id"), "error": {"code": -32601, "message": "Method not found"}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--cert")
    ap.add_argument("--key")
    ap.add_argument("--http", action="store_true", help="local demo over plain HTTP (needs AEGIS dev exceptions)")
    ap.add_argument("--default-host", default="", help="serve unknown Host names as this fixture host (demo)")
    a = ap.parse_args()
    PORT, DEFAULT_HOST = a.port, a.default_host
    if a.http:
        SCHEME = "http"
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    else:
        uvicorn.run(app, host="127.0.0.1", port=a.port, ssl_certfile=a.cert, ssl_keyfile=a.key, log_level="warning")
