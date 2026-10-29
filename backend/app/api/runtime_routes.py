from __future__ import annotations

from app.database.base import CommitBeforeResponseRoute

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.aegis.trace_collector import build_trace
from app.api.common import Page, get_or_404, page_body, paginate
from app.core.timeutil import as_utc, iso, utcnow
from app.database.base import db_session, get_db
from app.models import (
    Agent, ApprovalRequest, PolicyDecision, RuntimeEvent, ScenarioResult, TestRun, ToolCall, Trace, model_to_dict,
)
from app.security.authentication import Principal
from app.security.authorization import require
from app.sentinel.approval import ApprovalService, approval_service
from app.services import runtime_service
from app.websocket.event_bus import event_bus, event_to_dict

router = APIRouter(route_class=CommitBeforeResponseRoute, tags=["runtime"])


def _parse_time(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return as_utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid timestamp; use ISO 8601")


# ------------------------------------------------------------------ events & decisions
@router.get("/runtime/events")
def list_events(since_seq: int = Query(0, ge=0), event_type: Optional[str] = None, severity: Optional[str] = None,
                agent_id: Optional[str] = None, run_id: Optional[str] = None, correlation_id: Optional[str] = None,
                start: Optional[str] = None, end: Optional[str] = None, order: str = Query("desc", pattern="^(asc|desc)$"),
                page: Page = Depends(), p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    stmt = select(RuntimeEvent).where(RuntimeEvent.seq > since_seq)
    for col, val in ((RuntimeEvent.agent_id, agent_id), (RuntimeEvent.run_id, run_id),
                     (RuntimeEvent.correlation_id, correlation_id), (RuntimeEvent.severity, severity)):
        if val:
            stmt = stmt.where(col == val)
    if event_type:
        stmt = stmt.where(RuntimeEvent.event_type.like(event_type.replace("*", "%")))
    if (s := _parse_time(start)) is not None:
        stmt = stmt.where(RuntimeEvent.created_at >= s)
    if (e := _parse_time(end)) is not None:
        stmt = stmt.where(RuntimeEvent.created_at <= e)
    stmt = stmt.order_by(RuntimeEvent.seq.desc() if order == "desc" else RuntimeEvent.seq)
    items, total = paginate(db, stmt, page)
    return {**page_body([event_to_dict(e) for e in items], total, page), "latest_seq": event_bus.latest_seq()}


@router.get("/runtime/decisions")
def list_decisions(decision: Optional[str] = None, agent_id: Optional[str] = None, tool_name: Optional[str] = None,
                   page: Page = Depends(), p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    stmt = select(PolicyDecision).order_by(PolicyDecision.created_at.desc())
    if decision:
        stmt = stmt.where(PolicyDecision.decision == decision)
    if agent_id:
        stmt = stmt.where(PolicyDecision.agent_id == agent_id)
    if tool_name:
        stmt = stmt.where(PolicyDecision.tool_name == tool_name)
    items, total = paginate(db, stmt, page)
    return page_body([model_to_dict(d) for d in items], total, page)


# ------------------------------------------------------------------ live sessions and direct calls
class SessionIn(BaseModel):
    agent_id: str = Field(max_length=40)
    task: str = Field(min_length=1, max_length=2000)
    session_subject: Optional[str] = Field(default=None, pattern=r"^CUST-\d{4}$")


@router.post("/runtime/sessions", status_code=202)
async def start_session(body: SessionIn, p: Principal = Depends(require("runtime:invoke"))):
    try:
        cid = runtime_service.start_session(body.agent_id, body.task, body.session_subject, p.username)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"correlation_id": cid, "trace_url": f"/api/v1/traces/{cid}"}


class InvokeIn(BaseModel):
    agent_id: str = Field(max_length=40)
    tool: str = Field(min_length=1, max_length=64)
    args: Any = Field(default_factory=dict)
    session_subject: Optional[str] = Field(default=None, pattern=r"^CUST-\d{4}$")
    correlation_id: Optional[str] = Field(default=None, pattern=r"^cor_[a-f0-9]{20}$")
    approval_id: Optional[str] = Field(default=None, pattern=r"^apr_[a-f0-9]{20}$")


@router.post("/runtime/invoke")
async def invoke(body: InvokeIn, p: Principal = Depends(require("runtime:invoke"))):
    try:
        r = await runtime_service.direct_invoke(body.agent_id, body.tool, body.args, body.session_subject,
                                                body.correlation_id, body.approval_id, p.username)
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    out = r.public_dict()
    out["correlation_id"] = None
    with db_session() as db:
        tc = db.get(ToolCall, r.tool_call_id)
        out["correlation_id"] = tc.correlation_id
        out["result_summary"] = tc.result_summary
    return out


@router.post("/runtime/sandbox/reset")
def reset_sandbox(p: Principal = Depends(require("data:delete"))):
    runtime_service.reset_runtime_sandbox()
    event_bus.publish("system.sandbox_reset", f"Runtime sandbox reset by {p.username}", severity="notice")
    return {"ok": True}


@router.get("/runtime/sandbox/outbox")
def sandbox_outbox(p: Principal = Depends(require("read"))):
    """Messages the simulated email tool placed in the runtime outbox (bodies redacted)."""
    from app.security.redaction import redact_text
    sb = runtime_service.runtime_sandbox()
    return {"items": [{**m, "body": redact_text(m.get("body", ""), 400)} for m in sb.read_jsonl("outbox.jsonl")][-50:]}


# ------------------------------------------------------------------ approvals
def approval_out(a: ApprovalRequest) -> dict:
    d = model_to_dict(a)
    d["resubmittable"] = approval_service.pending_action(a.id) is not None
    return d


@router.get("/approvals")
def list_approvals(status: Optional[str] = None, page: Page = Depends(), p: Principal = Depends(require("read")),
                   db: Session = Depends(get_db)):
    ApprovalService.expire_stale(db)
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    items, total = paginate(db, stmt, page)
    return page_body([approval_out(a) for a in items], total, page)


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: DecideIn, p: Principal = Depends(require("approvals:decide")), db: Session = Depends(get_db)):
    appr, status = approval_service.decide(db, approval_id, body.approve, p.username, body.note)
    if appr is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if status != "ok":
        raise HTTPException(status_code=409, detail=f"Approval cannot be decided ({status.replace('not_pending:', 'already ')})")
    db.commit()  # release the write lock before the event bus writes
    event_bus.publish("approval.decided", f"Approval {appr.id} {'approved' if body.approve else 'denied'} by {p.username}",
                      severity="notice", payload={"approval_id": appr.id, "approved": body.approve, "by": p.username,
                                                  "tool": appr.tool_name},
                      correlation_id=appr.correlation_id, run_id=appr.run_id, agent_id=appr.agent_id)
    return approval_out(appr)


@router.post("/approvals/{approval_id}/execute")
async def execute(approval_id: str, p: Principal = Depends(require("approvals:decide"))):
    with db_session() as db:
        a = db.get(ApprovalRequest, approval_id) if len(approval_id) <= 40 else None
        if a is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if a.status != "approved":
            raise HTTPException(status_code=409, detail=f"Approval is {a.status}; only approved actions can be executed")
    try:
        r = await runtime_service.execute_approved(approval_id, p.username)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return r.public_dict()


# ------------------------------------------------------------------ traces
@router.get("/traces")
def list_traces(agent_id: Optional[str] = None, run_id: Optional[str] = None, kind: Optional[str] = None,
                status: Optional[str] = None, q: Optional[str] = Query(None, max_length=100),
                has_block: Optional[bool] = None, start: Optional[str] = None, end: Optional[str] = None,
                page: Page = Depends(), p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    stmt = select(Trace).order_by(Trace.started_at.desc())
    for col, val in ((Trace.agent_id, agent_id), (Trace.run_id, run_id), (Trace.kind, kind), (Trace.status, status)):
        if val:
            stmt = stmt.where(col == val)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Trace.id.like(like), Trace.task_redacted.like(like), Trace.scenario_key.like(like)))
    if (s := _parse_time(start)) is not None:
        stmt = stmt.where(Trace.started_at >= s)
    if (e := _parse_time(end)) is not None:
        stmt = stmt.where(Trace.started_at <= e)
    if has_block:
        stmt = stmt.where(Trace.id.in_(select(ToolCall.correlation_id).where(ToolCall.status == "blocked")))
    items, total = paginate(db, stmt, page)
    ids = [t.id for t in items]
    stats: Dict[str, Dict[str, int]] = {}
    if ids:
        for cid, st, n in db.execute(select(ToolCall.correlation_id, ToolCall.status, func.count())
                                     .where(ToolCall.correlation_id.in_(ids)).group_by(ToolCall.correlation_id, ToolCall.status)):
            stats.setdefault(cid, {})[st] = n
    outcomes = dict(db.execute(select(ScenarioResult.correlation_id, ScenarioResult.outcome)
                               .where(ScenarioResult.correlation_id.in_(ids))).all()) if ids else {}
    agents = {a.id: a.key for a in db.execute(select(Agent)).scalars()}
    return page_body([{**model_to_dict(t), "agent_key": agents.get(t.agent_id), "call_stats": stats.get(t.id, {}),
                       "outcome": outcomes.get(t.id)} for t in items], total, page)


@router.get("/traces/{correlation_id}")
def get_trace(correlation_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    if len(correlation_id) > 48:
        raise HTTPException(status_code=404, detail="Trace not found")
    t = build_trace(db, correlation_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    a = db.get(Agent, t["trace"]["agent_id"])
    t["agent"] = {"id": a.id, "key": a.key, "name": a.name, "agent_type": a.agent_type} if a else None
    return t


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    ApprovalService.expire_stale(db)
    since = utcnow() - timedelta(hours=24)
    dec = dict(db.execute(select(PolicyDecision.decision, func.count()).group_by(PolicyDecision.decision)).all())
    dec24 = dict(db.execute(select(PolicyDecision.decision, func.count()).where(PolicyDecision.created_at >= since)
                            .group_by(PolicyDecision.decision)).all())
    # Live traffic (runtime sessions, including the e-commerce assistant) has no test run; test traffic does.
    live_dec = dict(db.execute(select(PolicyDecision.decision, func.count()).where(PolicyDecision.run_id.is_(None))
                               .group_by(PolicyDecision.decision)).all())
    executed = db.execute(select(func.count(ToolCall.id)).where(ToolCall.status == "executed")).scalar_one()
    live_executed = db.execute(select(func.count(ToolCall.id)).where(ToolCall.status == "executed", ToolCall.run_id.is_(None))).scalar_one()
    failed = db.execute(select(func.count(ToolCall.id)).where(ToolCall.status == "failed")).scalar_one()
    pending = db.execute(select(ApprovalRequest).where(ApprovalRequest.status == "pending")
                         .order_by(ApprovalRequest.created_at.desc()).limit(10)).scalars().all()
    active = db.execute(select(TestRun).where(TestRun.status.in_(("queued", "running"))).order_by(TestRun.created_at.desc())).scalars().all()
    recent_runs = db.execute(select(TestRun).order_by(TestRun.created_at.desc()).limit(6)).scalars().all()
    recent_fail_calls = db.execute(select(ToolCall).where(ToolCall.status == "failed").order_by(ToolCall.created_at.desc()).limit(6)).scalars().all()
    recent_fail_results = db.execute(select(ScenarioResult).where(ScenarioResult.outcome.in_(("FAIL", "ERROR")))
                                     .order_by(ScenarioResult.started_at.desc()).limit(6)).scalars().all()
    blocked = db.execute(select(PolicyDecision).where(PolicyDecision.decision == "BLOCK")
                         .order_by(PolicyDecision.created_at.desc()).limit(8)).scalars().all()
    events = db.execute(select(RuntimeEvent).order_by(RuntimeEvent.seq.desc()).limit(15)).scalars().all()
    agents = db.execute(select(Agent).order_by(Agent.created_at)).scalars().all()
    from app.api.agent_routes import connection_status
    total_runs = db.execute(select(func.count(TestRun.id))).scalar_one()
    total_calls = db.execute(select(func.count(ToolCall.id))).scalar_one()
    return {
        # "empty" means no agent activity yet; system events such as backend start do not count.
        "empty": total_runs == 0 and total_calls == 0,
        "decisions": {"all_time": {k: dec.get(k, 0) for k in ("ALLOW", "BLOCK", "REQUIRE_APPROVAL")},
                      "last_24h": {k: dec24.get(k, 0) for k in ("ALLOW", "BLOCK", "REQUIRE_APPROVAL")}},
        "decisions_live": {k: live_dec.get(k, 0) for k in ("ALLOW", "BLOCK", "REQUIRE_APPROVAL")},
        "tool_calls": {"executed": executed, "failed": failed, "executed_live": live_executed},
        "pending_approvals": [approval_out(a) for a in pending],
        "active_runs": [{"id": r.id, "label": r.label, "status": r.status, "completed": r.completed_scenarios,
                         "total": r.total_scenarios, "agent_key": r.agent_snapshot.get("key")} for r in active],
        "recent_runs": [{"id": r.id, "label": r.label, "status": r.status, "agent_key": r.agent_snapshot.get("key"),
                         "created_at": iso(r.created_at), "counts": (r.summary or {}).get("counts")} for r in recent_runs],
        "recent_failures": {"tool_calls": [model_to_dict(c) for c in recent_fail_calls],
                            "results": [{"id": r.id, "run_id": r.run_id, "scenario_key": r.scenario_key, "outcome": r.outcome,
                                         "reason": r.reason[:240], "correlation_id": r.correlation_id,
                                         "started_at": iso(r.started_at)} for r in recent_fail_results]},
        "recent_blocks": [model_to_dict(d, exclude=("evaluated",)) for d in blocked],
        "recent_events": [event_to_dict(e) for e in events],
        "agents": [{"id": a.id, "key": a.key, "name": a.name, "agent_type": a.agent_type, "enabled": a.enabled,
                    "connection": connection_status(db, a)} for a in agents],
        "event_stream": {"subscribers": event_bus.subscriber_count, "latest_seq": event_bus.latest_seq()},
    }
