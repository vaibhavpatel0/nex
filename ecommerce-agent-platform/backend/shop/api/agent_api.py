"""Chat (customers), Agent Workbench (staff), internal test API (AEGIS) and the live WebSocket."""
from __future__ import annotations

from shop.db import CommitBeforeResponseRoute

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from shop.agent import runner
from shop.agent.events import bus, to_dict
from shop.agent.pep import execute_approved
from shop.agent.providers import ModelError, ModelTimeout, ModelUnavailable, build_provider
from shop.agent.sentinel_client import SentinelUnavailable, sentinel
from shop.agent.tools import TOOLS, model_tool_specs
from shop.config import VERSION, get_settings
from shop.db import as_utc, get_db, iso, session_scope
from shop.models import AgentEvent, AgentRun, ChatMessage, Conversation, Customer, PendingAction
from shop.security import Principal, customer, internal, resolve, staff
from shop.services.store import ServiceError

router = APIRouter(route_class=CommitBeforeResponseRoute)


# ------------------------------------------------------------------ customer chat
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = Field(default=None, max_length=40)


@router.post("/chat/messages", status_code=202)
async def chat(body: ChatIn, p: Principal = Depends(customer)):
    return runner.start_chat(p.customer_id, body.message, body.conversation_id, p.email)


@router.get("/chat/conversations")
def conversations(p: Principal = Depends(customer), db: Session = Depends(get_db)):
    rows = db.execute(select(Conversation).where(Conversation.customer_id == p.customer_id).order_by(Conversation.created_at.desc()).limit(20)).scalars()
    return {"items": [{"id": c.id, "created_at": iso(c.created_at)} for c in rows]}


@router.get("/chat/conversations/{conv_id}")
def conversation(conv_id: str, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    c = db.get(Conversation, conv_id[:40])
    if c is None or c.customer_id != p.customer_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = db.execute(select(ChatMessage).where(ChatMessage.conversation_id == c.id).order_by(ChatMessage.id)).scalars().all()
    runs = {r.id: r.status for r in db.execute(select(AgentRun).where(AgentRun.conversation_id == c.id)).scalars()}
    return {"id": c.id, "messages": [{"role": m.role, "content": m.content, "correlation_id": m.correlation_id,
                                      "created_at": iso(m.created_at), "run_status": runs.get(m.correlation_id or "")} for m in msgs]}


# ------------------------------------------------------------------ workbench (staff)
def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return as_utc(datetime.fromisoformat(ts.replace("Z", "+00:00")))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid timestamp")


@router.get("/workbench/runs")
def wb_runs(q: Optional[str] = Query(None, max_length=80), kind: Optional[str] = None, status: Optional[str] = None,
            tool: Optional[str] = Query(None, max_length=64), decision: Optional[str] = None,
            scenario: Optional[str] = Query(None, max_length=64), aegis_run: Optional[str] = Query(None, max_length=40),
            start: Optional[str] = None, end: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), p: Principal = Depends(staff), db: Session = Depends(get_db)):
    stmt = select(AgentRun)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AgentRun.id.like(like), AgentRun.message.like(like), AgentRun.customer_id.like(like)))
    for col, val in ((AgentRun.kind, kind), (AgentRun.status, status), (AgentRun.scenario_key, scenario), (AgentRun.aegis_run_id, aegis_run)):
        if val:
            stmt = stmt.where(col == val)
    if tool or decision:
        ev = select(AgentEvent.correlation_id)
        if tool:
            ev = ev.where(AgentEvent.tool.like(f"%{tool}%"))
        if decision:
            ev = ev.where(AgentEvent.decision == decision)
        stmt = stmt.where(AgentRun.id.in_(ev))
    if (s := _parse(start)) is not None:
        stmt = stmt.where(AgentRun.started_at >= s)
    if (e := _parse(end)) is not None:
        stmt = stmt.where(AgentRun.started_at <= e)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AgentRun.started_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"items": [runner.run_summary(r) for r in rows], "total": total, "limit": limit, "offset": offset,
            "latest_seq": bus.latest()}


@router.get("/workbench/runs/{cid}")
def wb_run(cid: str, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    r = db.get(AgentRun, cid[:48])
    if r is None:
        raise HTTPException(status_code=404, detail="Run not found")
    events = db.execute(select(AgentEvent).where(AgentEvent.correlation_id == r.id).order_by(AgentEvent.seq)).scalars().all()
    pend = db.execute(select(PendingAction).where(PendingAction.correlation_id == r.id)).scalars().all()
    return {"run": runner.run_summary(r), "events": [to_dict(e) for e in events],
            "pending_actions": [{"approval_id": x.approval_id, "tool": x.tool, "status": x.status, "fixture": x.fixture,
                                 "executed_by": x.executed_by, "result": x.result} for x in pend]}


@router.get("/workbench/runs/{cid}/aegis")
async def wb_aegis(cid: str, p: Principal = Depends(staff)):
    try:
        return await sentinel.evaluation(cid[:48])
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class StartIn(BaseModel):
    customer_id: str = Field(pattern=r"^CUST-\d{4}$")
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = Field(default=None, max_length=40)


@router.post("/workbench/conversations", status_code=202)
async def wb_start(body: StartIn, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    if db.get(Customer, body.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return runner.start_chat(body.customer_id, body.message, body.conversation_id, f"{p.email} (staff, as {body.customer_id})")


@router.get("/workbench/customers")
def wb_customers(p: Principal = Depends(staff), db: Session = Depends(get_db)):
    return {"items": [{"customer_id": c.id, "name": c.name, "synthetic": c.synthetic} for c in db.execute(select(Customer).order_by(Customer.id)).scalars()]}


@router.get("/workbench/tools")
def wb_tools(p: Principal = Depends(staff)):
    s = get_settings()
    return {"items": [{"name": t.qualified, "operation": t.operation, "sensitivity": t.sensitivity, "staff_only": t.staff_only,
                       "exposed_to_model": s.expose_staff_tools or not t.staff_only, "description": t.description,
                       "schema": t.schema()} for t in TOOLS.values()]}


@router.get("/workbench/model")
async def wb_model(p: Principal = Depends(staff)):
    s = get_settings()
    st = await build_provider().status()
    return {**st, "max_rounds": s.max_model_rounds, "timeout_seconds": s.model_timeout_seconds,
            "staff_tools_exposed": s.expose_staff_tools}


@router.post("/workbench/model/check")
async def wb_model_check(p: Principal = Depends(staff)):
    """One real inference request with a tool definition, to verify the configured model calls tools.
    It sends a short prompt to the configured provider (may use cloud credits)."""
    prov = build_provider()
    tools = [x for x in model_tool_specs(False) if x["function"]["name"] == "check_inventory"]
    msgs = [{"role": "system", "content": "You are a tool-calling test. Call the check_inventory tool for product P-1001."},
            {"role": "user", "content": "Is P-1001 in stock?"}]
    t0 = time.perf_counter()
    try:
        reply = await prov.chat(msgs, tools)
    except (ModelTimeout, ModelUnavailable, ModelError) as exc:
        return {"ok": False, "provider": prov.name, "model": prov.model, "error_class": type(exc).__name__, "error": str(exc),
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
    called = [c.name for c in reply.calls]
    return {"ok": "check_inventory" in called, "provider": prov.name, "model": reply.model or prov.model, "tool_calls": called,
            "arguments": [c.arguments for c in reply.calls][:2], "content": (reply.content or "")[:300],
            "usage": reply.usage, "duration_ms": round(reply.duration_ms, 1),
            "note": "No tool was executed; this only checks that the model proposes a tool call."}


@router.get("/workbench/integration")
async def wb_integration(p: Principal = Depends(staff)):
    s = get_settings()
    out: Dict[str, Any] = {"aegis_base_url": s.aegis_base_url, "aegis_ui_url": s.aegis_ui_url, "agent_key": s.aegis_agent_key,
                           "token_configured": bool(s.aegis_api_token), "internal_token_configured": bool(s.internal_token),
                           "reachable": False}
    try:
        res = await sentinel.scenarios()
        out["reachable"] = True
        out["scenarios"] = res["items"]
    except SentinelUnavailable as exc:
        out["error"] = str(exc)
    return out


class SuiteIn(BaseModel):
    suite_key: Optional[str] = Field(default=None, max_length=64)
    scenario_keys: Optional[List[str]] = Field(default=None, max_length=50)
    mode: Literal["protected", "baseline"] = "protected"
    label: str = Field(default="", max_length=120)


@router.post("/workbench/aegis/test-runs", status_code=202)
async def wb_start_run(body: SuiteIn, p: Principal = Depends(staff)):
    try:
        return await sentinel.start_run(body.suite_key, body.scenario_keys, body.mode,
                                        body.label or f"{body.mode} run started by {p.email}")
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/workbench/aegis/test-runs")
async def wb_list_runs(p: Principal = Depends(staff)):
    try:
        return await sentinel.runs()
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/workbench/aegis/test-runs/{run_id}")
async def wb_get_run(run_id: str, p: Principal = Depends(staff)):
    try:
        return await sentinel.run(run_id[:40])
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/workbench/aegis/policies/{key}")
async def wb_policy(key: str, p: Principal = Depends(staff)):
    try:
        return await sentinel.policy(key[:32])
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/workbench/approvals")
async def wb_approvals(status: Optional[str] = Query(None, max_length=16), p: Principal = Depends(staff),
                       db: Session = Depends(get_db)):
    try:
        items = (await sentinel.approvals(status))["items"]
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    pend = {x.approval_id: x for x in db.execute(select(PendingAction).where(PendingAction.approval_id.in_([i["id"] for i in items] or ["-"]))).scalars()}
    for i in items:
        pa = pend.get(i["id"])
        i["local"] = {"status": pa.status, "fixture": pa.fixture, "executable": pa.fixture == "live" and pa.status == "pending",
                      "result": pa.result} if pa else None
    return {"items": items}


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=300)


@router.post("/workbench/approvals/{approval_id}/decide")
async def wb_decide(approval_id: str, body: DecideIn, p: Principal = Depends(staff)):
    try:
        return await sentinel.decide(approval_id[:40], body.approve, body.note, p.email)
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=409 if "HTTP 409" in str(exc) else 502, detail=str(exc))


@router.post("/workbench/approvals/{approval_id}/execute")
async def wb_execute(approval_id: str, p: Principal = Depends(staff)):
    try:
        return await execute_approved(approval_id[:40], p.email)
    except ServiceError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    except SentinelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ------------------------------------------------------------------ internal API (AEGIS)
@router.get("/internal/health")
def internal_health(_: str = Depends(internal)):
    s = get_settings()
    return {"status": "ok", "version": VERSION, "model": {"provider": s.model_provider, "model": s.model_name},
            "sentinel_configured": bool(s.aegis_api_token)}


class AgentRunIn(BaseModel):
    correlation_id: str = Field(pattern=r"^cor_[a-f0-9]{20}$")
    run_id: Optional[str] = Field(default=None, max_length=40)
    scenario_key: Optional[str] = Field(default=None, max_length=64)
    message: str = Field(default="", max_length=2000)
    customer_id: Optional[str] = Field(default=None, pattern=r"^CUST-\d{4}$")
    faults: Dict[str, str] = Field(default_factory=dict)
    model_fault: Optional[Literal["malformed_tool_call", "empty_output"]] = None
    direct_tool_call: Optional[Dict[str, Any]] = None


@router.post("/internal/agent-runs")
async def internal_run(body: AgentRunIn, _: str = Depends(internal)):
    allowed_faults = {"model": {"timeout"}, "inventory": {"unavailable", "server_error", "timeout"}}
    for k, v in body.faults.items():
        if k not in allowed_faults or not (v in allowed_faults[k] or (k == "inventory" and v.startswith("race:P-"))):
            raise HTTPException(status_code=422, detail=f"Unsupported fault {k}={v}")
    if not body.message.strip() and not body.direct_tool_call:
        raise HTTPException(status_code=422, detail="message or direct_tool_call is required")
    return await runner.run_for_aegis(body.model_dump())


# ------------------------------------------------------------------ live events
@router.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    """First frame: {"type":"auth","token":"...","since_seq":N}. Staff receive all events;
    customers receive events for their own chat runs only."""
    await socket.accept()
    try:
        msg = json.loads(await asyncio.wait_for(socket.receive_text(), timeout=10))
    except asyncio.TimeoutError:
        await socket.close(code=4401, reason="no auth frame within 10 s")
        return
    except (ValueError, WebSocketDisconnect):
        await socket.close(code=4401, reason="invalid auth frame")
        return
    with session_scope() as db:
        pr = resolve(db, str(msg.get("token", ""))[:256]) if isinstance(msg, dict) else None
    if pr is None:
        await socket.close(code=4401, reason="invalid or expired token")
        return
    scope = None if pr.role == "staff" else pr.customer_id
    # since_seq omitted -> live events only; a number (including 0) -> replay everything after it.
    raw_since = msg.get("since_seq")
    try:
        since = None if raw_since is None else max(0, int(raw_since))
    except (TypeError, ValueError):
        since = None
    sub = bus.subscribe(scope)
    try:
        latest = bus.latest()
        await socket.send_text(json.dumps({"type": "ready", "latest_seq": latest}))
        sent = latest if since is None else since
        if since is not None and since < latest:
            for ev in bus.replay(since, scope):
                await socket.send_text(json.dumps({"type": "event", "event": ev, "replayed": True}))
                sent = max(sent, ev["seq"])

        async def reader() -> None:
            while True:
                await socket.receive_text()

        rt = asyncio.create_task(reader())
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(sub.queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    if rt.done():
                        break
                    await socket.send_text(json.dumps({"type": "ping"}))
                    continue
                if ev["seq"] <= sent:
                    continue
                sent = ev["seq"]
                await socket.send_text(json.dumps({"type": "event", "event": ev}))
                if rt.done():
                    break
                if sub.overflowed:
                    await socket.close(code=4408)
                    break
        finally:
            rt.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(sub)
