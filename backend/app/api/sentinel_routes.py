"""SENTINEL remote decision API for external agent applications.

An external application (for example ecommerce-agent-platform) is the policy enforcement point:
it executes its own tools, but only after SENTINEL returns a decision that permits execution.

Protocol (all calls authenticated with a service token; see `python -m app.cli create-service-token`)
1. PUT  /sentinel/tools                          register the application's tool definitions
2. POST /sentinel/sessions                       open (or join) the trace for one agent run
3. POST /sentinel/sessions/{cid}/events          record model activity (requests, responses)
4. POST /sentinel/evaluate                       ask for a decision before each tool call
5. POST /sentinel/tool-calls/{id}/result         report the outcome of an allowed execution
6. POST /sentinel/sessions/{cid}/complete        record the final answer (runtime sessions only)

Trust model: the model is untrusted; the calling application is trusted to report resource
attributes (record ownership) from its own database and the authenticated customer identity.
Service tokens are bound to agent keys and tool namespaces.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.tools.registry import TOOL_SPECS
from app.core.timeutil import iso, utcnow
from app.database.base import db_session, get_db
from app.models import Agent, TestRun, ToolCall, ToolDefinition, Trace
from app.security.audit import agent_view
from app.security.authentication import Principal
from app.security.authorization import require
from app.security.redaction import redact_text, redact_value
from app.sentinel.decision_point import decide, record_result, remote_meta, validate_with_schema
from app.websocket.event_bus import event_bus

router = APIRouter(prefix="/sentinel", tags=["sentinel"])

CID = r"^cor_[a-f0-9]{20}$"
TOOL_NAME = r"^[a-z][a-z0-9_]{1,23}\.[a-z][a-z0-9_]{1,40}$"


def _agent_for(db: Session, p: Principal, agent_key: str) -> Agent:
    if agent_key not in p.agent_keys:
        raise HTTPException(status_code=403, detail="This service token is not bound to that agent")
    a = db.execute(select(Agent).where(Agent.key == agent_key)).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not a.enabled:
        raise HTTPException(status_code=409, detail="Agent is disabled")
    return a


def _small_json(v: Any, limit: int, what: str) -> Any:
    try:
        n = len(json.dumps(v, default=str))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{what} is not JSON serialisable")
    if n > limit:
        raise HTTPException(status_code=413, detail=f"{what} exceeds {limit} bytes")
    return v


# ------------------------------------------------------------------ tool registration
class ToolDef(BaseModel):
    name: str = Field(pattern=TOOL_NAME)
    description: str = Field(min_length=3, max_length=600)
    operation: Literal["read", "write", "egress"]
    sensitivity: Literal["low", "medium", "high"]
    resource_kind: str = Field(min_length=1, max_length=32)
    args_schema: Dict[str, Any]
    sensitive_fields: List[str] = Field(default_factory=list, max_length=16)

    @field_validator("args_schema")
    @classmethod
    def _strict_object_schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON schema: {exc.message[:120]}")
        if v.get("type") != "object" or v.get("additionalProperties") is not False:
            raise ValueError("args_schema must be an object schema with additionalProperties: false")
        if len(json.dumps(v)) > 16000:
            raise ValueError("args_schema is too large")
        return v


class ToolRegistration(BaseModel):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{1,23}$")
    tools: List[ToolDef] = Field(min_length=1, max_length=64)


@router.put("/tools")
def register_tools(body: ToolRegistration, p: Principal = Depends(require("tools:register", True)), db: Session = Depends(get_db)):
    if body.namespace not in p.tool_namespaces:
        raise HTTPException(status_code=403, detail="This service token may not register tools in that namespace")
    provider = f"remote:{body.namespace}"
    changed, created = [], []
    for t in body.tools:
        if not t.name.startswith(body.namespace + "."):
            raise HTTPException(status_code=422, detail=f"{t.name} is outside namespace {body.namespace}")
        if t.name in TOOL_SPECS:
            raise HTTPException(status_code=409, detail=f"{t.name} collides with a built-in tool")
        row = db.get(ToolDefinition, t.name)
        fields = dict(description=t.description, operation=t.operation, sensitivity=t.sensitivity,
                      resource_kind=t.resource_kind, args_schema=t.args_schema, sensitive_fields=t.sensitive_fields)
        if row is None:
            db.add(ToolDefinition(name=t.name, provider=provider, simulated=False, revision=1, updated_at=utcnow(), **fields))
            created.append(t.name)
        else:
            if row.provider != provider:
                raise HTTPException(status_code=409, detail=f"{t.name} belongs to another provider")
            if any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                row.revision += 1
                row.updated_at = utcnow()
                changed.append(t.name)
    db.commit()
    if created or changed:
        event_bus.publish("tool.registered", f"{p.username} registered {len(created)} new and updated {len(changed)} tool(s)",
                          severity="notice", payload={"namespace": body.namespace, "created": created, "updated": changed})
    return {"namespace": body.namespace, "created": created, "updated": changed,
            "unchanged": len(body.tools) - len(created) - len(changed)}


# ------------------------------------------------------------------ sessions (traces)
class SessionIn(BaseModel):
    correlation_id: str = Field(pattern=CID)
    agent_key: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=4000)
    session_subject: Optional[str] = Field(default=None, max_length=64)
    conversation_id: Optional[str] = Field(default=None, max_length=64)


def _session_out(tr: Trace, db: Session) -> Dict[str, Any]:
    run = db.get(TestRun, tr.run_id) if tr.run_id else None
    enforce = not (run is not None and run.mode == "baseline" and run.status == "running")
    return {"correlation_id": tr.id, "kind": tr.kind, "run_id": tr.run_id, "scenario_key": tr.scenario_key,
            "mode": run.mode if run else "live", "enforce": enforce}


@router.post("/sessions")
def open_session(body: SessionIn, p: Principal = Depends(require("sentinel:ingest", True)), db: Session = Depends(get_db)):
    a = _agent_for(db, p, body.agent_key)
    tr = db.get(Trace, body.correlation_id)
    if tr is not None:
        if tr.agent_id != a.id:
            raise HTTPException(status_code=409, detail="Correlation ID belongs to another agent")
        return {**_session_out(tr, db), "created": False}
    tr = Trace(id=body.correlation_id, kind="runtime", agent_id=a.id, agent_revision=a.revision, status="running",
               task_redacted=redact_text(body.task, 2000), started_at=utcnow(), triggered_by=p.username)
    db.add(tr)
    db.commit()
    event_bus.publish("agent.request", f"{a.key} received a task", correlation_id=tr.id, agent_id=a.id,
                      payload={"task": body.task, "session_subject": body.session_subject,
                               "conversation_id": body.conversation_id, "source": p.username})
    return {**_session_out(tr, db), "created": True}


class SessionEventIn(BaseModel):
    event_type: Literal["model.request", "model.response", "model.error", "agent.status"]
    summary: str = Field(min_length=1, max_length=400)
    severity: Literal["info", "notice", "warning", "error"] = "info"
    payload: Dict[str, Any] = Field(default_factory=dict)


def _trace_for(db: Session, p: Principal, cid: str) -> Trace:
    tr = db.get(Trace, cid) if len(cid) <= 48 else None
    if tr is None:
        raise HTTPException(status_code=404, detail="Session not found")
    a = db.get(Agent, tr.agent_id)
    if a is None or a.key not in p.agent_keys:
        raise HTTPException(status_code=404, detail="Session not found")
    return tr


@router.post("/sessions/{cid}/events")
def session_event(cid: str, body: SessionEventIn, p: Principal = Depends(require("sentinel:ingest", True)),
                  db: Session = Depends(get_db)):
    tr = _trace_for(db, p, cid)
    _small_json(body.payload, 16000, "payload")
    ids = dict(correlation_id=tr.id, run_id=tr.run_id, agent_id=tr.agent_id)
    db.commit()
    ev = event_bus.publish(body.event_type, body.summary, severity=body.severity,
                           payload={**body.payload, "source": p.username}, **ids)
    return {"seq": ev["seq"], "event_id": ev["event_id"]}


class CompleteIn(BaseModel):
    final_output: Optional[str] = Field(default=None, max_length=20000)
    error_kind: Optional[str] = Field(default=None, max_length=32)
    error: Optional[str] = Field(default=None, max_length=1000)
    usage: Optional[Dict[str, int]] = None
    latency_ms: Optional[float] = Field(default=None, ge=0)


@router.post("/sessions/{cid}/complete")
def complete_session(cid: str, body: CompleteIn, p: Principal = Depends(require("sentinel:ingest", True)),
                     db: Session = Depends(get_db)):
    tr = _trace_for(db, p, cid)
    if tr.kind == "test":
        raise HTTPException(status_code=409, detail="Test-run traces are completed by the AEGIS runner")
    if tr.status != "running":
        raise HTTPException(status_code=409, detail="Session already completed")
    missing = body.error_kind is None and not (body.final_output or "").strip()
    tr.status = "error" if body.error_kind else "completed"
    tr.ended_at = utcnow()
    tr.latency_ms = body.latency_ms
    tr.error = redact_text(body.error or "", 1000) if body.error_kind else None
    tr.final_output_missing = missing
    tr.final_output_redacted = redact_text(body.final_output, 4000) if body.final_output else None
    tr.usage = body.usage
    ids = dict(correlation_id=tr.id, run_id=tr.run_id, agent_id=tr.agent_id)
    db.commit()
    if body.error_kind:
        event_bus.publish("agent.error", f"Agent error: {body.error_kind}", severity="error",
                          payload={"error_kind": body.error_kind, "error": body.error, "latency_ms": body.latency_ms}, **ids)
    else:
        event_bus.publish("agent.output", "Agent returned no final output (recorded as missing)" if missing else "Agent produced a final answer",
                          severity="warning" if missing else "info",
                          payload={"final_output": body.final_output, "missing": missing, "latency_ms": body.latency_ms,
                                   "usage": body.usage, "usage_note": None if body.usage else "Not reported by the provider"}, **ids)
    return {"ok": True, "status": tr.status}


# ------------------------------------------------------------------ decisions
class EvaluateIn(BaseModel):
    correlation_id: str = Field(pattern=CID)
    agent_key: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=64)
    args: Any = None
    resource: Dict[str, Any] = Field(default_factory=dict)
    session_subject: Optional[str] = Field(default=None, max_length=64)
    approval_id: Optional[str] = Field(default=None, pattern=r"^apr_[a-f0-9]{20}$")
    origin: Literal["user_task", "tool_output", "approval_resubmit", "direct"] = "user_task"


@router.post("/evaluate")
def evaluate_tool_call(body: EvaluateIn, p: Principal = Depends(require("sentinel:evaluate", True))):
    _small_json(body.resource, 4000, "resource")
    with db_session() as db:
        a = _agent_for(db, p, body.agent_key)
        tr = _trace_for(db, p, body.correlation_id)
        if tr.agent_id != a.id:
            raise HTTPException(status_code=409, detail="Correlation ID belongs to another agent")
        if tr.status != "running":
            raise HTTPException(status_code=409, detail="Session is no longer running")
        sess = _session_out(tr, db)
        view = agent_view(a)
    ns_ok = any(body.tool.startswith(ns + ".") for ns in p.tool_namespaces)
    meta = remote_meta(body.tool) if ns_ok else None
    if meta is not None and meta.args_schema is not None:
        args_dict, args_error = validate_with_schema(meta.args_schema, body.args if body.args is not None else {})
    else:
        args_dict = body.args if isinstance(body.args, dict) else {}
        args_error = None if isinstance(body.args, (dict, type(None))) else "(root): must be an object"
    out = decide(
        correlation_id=body.correlation_id, run_id=sess["run_id"], agent=view, tool_name=body.tool, meta=meta,
        args_dict=args_dict, args_error=args_error, resource=redact_value(body.resource),
        session_subject=body.session_subject, approval_id=body.approval_id, origin=body.origin, enforce=sess["enforce"],
    )
    return {**out.public(), "grant": out.grant, "args_valid": args_error is None, "args_error": args_error}


class ResultIn(BaseModel):
    status: Literal["executed", "failed"]
    result_summary: Dict[str, Any] = Field(default_factory=dict)
    result_preview: str = Field(default="", max_length=8000)
    classification: Optional[Literal["public", "internal", "confidential", "untrusted_external"]] = None
    untrusted: bool = False
    error_class: Optional[str] = Field(default=None, max_length=32)
    error_message: Optional[str] = Field(default=None, max_length=500)
    latency_ms: Optional[float] = Field(default=None, ge=0)
    exec_latency_ms: Optional[float] = Field(default=None, ge=0)


@router.post("/tool-calls/{call_id}/result")
def report_result(call_id: str, body: ResultIn, p: Principal = Depends(require("sentinel:evaluate", True))):
    _small_json(body.result_summary, 8000, "result_summary")
    with db_session() as db:
        tc = db.get(ToolCall, call_id) if len(call_id) <= 40 else None
        a = db.get(Agent, tc.agent_id) if tc else None
        if tc is None or a is None or a.key not in p.agent_keys:
            raise HTTPException(status_code=404, detail="Tool call not found")
    try:
        res = record_result(
            tool_call_id=call_id, succeeded=body.status == "executed", summary=body.result_summary,
            classification=body.classification, untrusted=body.untrusted, text_for_scan=body.result_preview,
            error_class=body.error_class, error_message=body.error_message, total_latency_ms=body.latency_ms,
            exec_latency_ms=body.exec_latency_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"tool_call_id": call_id, **res}


@router.get("/sessions/{cid}")
def get_session(cid: str, p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    """Session summary for the calling application (its own agents only when called by a service)."""
    tr = db.get(Trace, cid) if len(cid) <= 48 else None
    if tr is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if p.is_service:
        a = db.get(Agent, tr.agent_id)
        if a is None or a.key not in p.agent_keys:
            raise HTTPException(status_code=404, detail="Session not found")
    return {**_session_out(tr, db), "status": tr.status, "started_at": iso(tr.started_at)}


# ------------------------------------------------------------------ bound read/test/approval access
# These give a calling application what its own operators need (its Agent Workbench) without
# opening the general AEGIS API to service tokens. Every query is restricted to bound agents.
from app.aegis.scenario_runner import runner  # noqa: E402
from app.models import ApprovalRequest, Policy, PolicyDecision, Scenario, ScenarioResult, TestSuite  # noqa: E402
from app.models import model_to_dict  # noqa: E402
from app.sentinel.approval import ApprovalService, approval_service  # noqa: E402


def _bound_agent_ids(db: Session, p: Principal) -> Dict[str, str]:
    rows = db.execute(select(Agent).where(Agent.key.in_(list(p.agent_keys) or ["-"]))).scalars().all()
    return {a.id: a.key for a in rows}


@router.get("/sessions/{cid}/evaluation")
def session_evaluation(cid: str, p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    tr = _trace_for(db, p, cid)
    res = db.execute(select(ScenarioResult).where(ScenarioResult.correlation_id == cid)).scalar_one_or_none()
    sc = db.get(Scenario, res.scenario_id) if res else None
    run = db.get(TestRun, tr.run_id) if tr.run_id else None
    calls = db.execute(select(ToolCall).where(ToolCall.correlation_id == cid).order_by(ToolCall.created_at)).scalars().all()
    decisions = {d.tool_call_id: d for d in db.execute(select(PolicyDecision).where(PolicyDecision.correlation_id == cid)).scalars()}
    return {
        "trace": {"correlation_id": tr.id, "kind": tr.kind, "status": tr.status, "run_id": tr.run_id,
                  "scenario_key": tr.scenario_key, "mode": run.mode if run else "live"},
        "scenario": {"key": sc.key, "name": sc.name, "category": sc.category, "purpose": sc.purpose, "risk": sc.risk,
                     "ground_truth": sc.ground_truth, "expected": sc.expected} if sc else None,
        "result": {"outcome": res.outcome, "reason": res.reason, "checks": res.checks, "attack_attempted": res.attack_attempted,
                   "attack_succeeded": res.attack_succeeded, "finished_at": iso(res.finished_at)} if res else None,
        "tool_calls": [{"id": c.id, "tool": c.tool_name, "status": c.status, "decision": c.decision, "enforced": c.enforced,
                        "matched_policy": decisions[c.id].matched_policy_key if c.id in decisions else None,
                        "flags": c.flags, "created_at": iso(c.created_at)} for c in calls],
    }


@router.get("/policies/{key}")
def policy_detail(key: str, p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    pol = db.execute(select(Policy).where(Policy.key == key[:32])).scalar_one_or_none()
    if pol is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return model_to_dict(pol)


@router.get("/scenarios")
def bound_scenarios(p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    from app.models import TestSuiteScenario
    tools = set()
    for a in db.execute(select(Agent).where(Agent.key.in_(list(p.agent_keys) or ["-"]))).scalars():
        tools.update(a.enabled_tools or [])
    suites: Dict[str, List[str]] = {}
    for sid, key in db.execute(select(TestSuiteScenario.scenario_id, TestSuite.key).join(TestSuite, TestSuite.id == TestSuiteScenario.suite_id)):
        suites.setdefault(sid, []).append(key)
    out = [{"key": s.key, "name": s.name, "category": s.category, "purpose": s.purpose, "risk": s.risk,
            "ground_truth": s.ground_truth, "suites": sorted(suites.get(s.id, []))}
           for s in db.execute(select(Scenario).order_by(Scenario.key)).scalars()
           if s.required_tools and set(s.required_tools) <= tools]
    return {"items": out}


class BoundRunIn(BaseModel):
    agent_key: str = Field(min_length=1, max_length=64)
    suite_key: Optional[str] = Field(default=None, max_length=64)
    scenario_keys: Optional[List[str]] = Field(default=None, max_length=100)
    mode: Literal["protected", "baseline"] = "protected"
    label: str = Field(default="", max_length=160)


@router.post("/test-runs", status_code=202)
async def bound_start_run(body: BoundRunIn, p: Principal = Depends(require("tests:run", True))):
    with db_session() as db:
        a = _agent_for(db, p, body.agent_key)
        agent_id = a.id
    if not body.suite_key and not body.scenario_keys:
        raise HTTPException(status_code=422, detail="Choose a suite or scenarios")
    try:
        run_id = runner.create_run(agent_id=agent_id, suite_key=body.suite_key, scenario_keys=body.scenario_keys,
                                   label=body.label or f"{body.mode} run from {p.username}", triggered_by=p.username,
                                   mode=body.mode)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    runner.start(run_id)
    return {"run_id": run_id, "status": "queued", "mode": body.mode}


@router.get("/test-runs")
def bound_runs(p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    ids = _bound_agent_ids(db, p)
    rows = db.execute(select(TestRun).where(TestRun.agent_id.in_(list(ids) or ["-"])).order_by(TestRun.created_at.desc()).limit(30)).scalars()
    return {"items": [{"id": r.id, "label": r.label, "status": r.status, "mode": r.mode, "agent_key": ids[r.agent_id],
                       "suite_key": r.suite.key if r.suite else None, "completed": r.completed_scenarios,
                       "total": r.total_scenarios, "counts": (r.summary or {}).get("counts"),
                       "created_at": iso(r.created_at), "finished_at": iso(r.finished_at)} for r in rows]}


@router.get("/test-runs/{run_id}")
def bound_run(run_id: str, p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    ids = _bound_agent_ids(db, p)
    r = db.get(TestRun, run_id) if len(run_id) <= 40 else None
    if r is None or r.agent_id not in ids:
        raise HTTPException(status_code=404, detail="Run not found")
    results = db.execute(select(ScenarioResult).where(ScenarioResult.run_id == run_id).order_by(ScenarioResult.started_at)).scalars()
    return {"id": r.id, "label": r.label, "status": r.status, "mode": r.mode, "counts": (r.summary or {}).get("counts"),
            "metrics": (r.summary or {}).get("metrics"), "completed": r.completed_scenarios, "total": r.total_scenarios,
            "results": [{"scenario_key": x.scenario_key, "outcome": x.outcome, "reason": x.reason,
                         "correlation_id": x.correlation_id, "attack_attempted": x.attack_attempted,
                         "attack_succeeded": x.attack_succeeded} for x in results]}


@router.get("/approvals")
def bound_approvals(status_: Optional[str] = Query(None, alias="status"), p: Principal = Depends(require("read", True)), db: Session = Depends(get_db)):
    ApprovalService.expire_stale(db)
    ids = _bound_agent_ids(db, p)
    stmt = select(ApprovalRequest).where(ApprovalRequest.agent_id.in_(list(ids) or ["-"])).order_by(ApprovalRequest.created_at.desc())
    if status_:
        stmt = stmt.where(ApprovalRequest.status == status_)
    rows = db.execute(stmt.limit(50)).scalars().all()
    db.commit()
    return {"items": [model_to_dict(a) for a in rows]}


class BoundDecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=400)
    on_behalf_of: str = Field(min_length=1, max_length=120)


@router.post("/approvals/{approval_id}/decide")
def bound_decide(approval_id: str, body: BoundDecideIn, p: Principal = Depends(require("approvals:decide", True)),
                 db: Session = Depends(get_db)):
    ids = _bound_agent_ids(db, p)
    a = db.get(ApprovalRequest, approval_id) if len(approval_id) <= 40 else None
    if a is None or a.agent_id not in ids:
        raise HTTPException(status_code=404, detail="Approval not found")
    who = f"{body.on_behalf_of} via {p.username}"
    appr, st = approval_service.decide(db, approval_id, body.approve, who, body.note)
    if st != "ok":
        raise HTTPException(status_code=409, detail=f"Approval cannot be decided ({st.replace('not_pending:', 'already ')})")
    out = model_to_dict(appr)
    db.commit()
    event_bus.publish("approval.decided", f"Approval {appr.id} {'approved' if body.approve else 'denied'} by {who}", severity="notice",
                      payload={"approval_id": appr.id, "approved": body.approve, "by": who, "tool": appr.tool_name},
                      correlation_id=appr.correlation_id, run_id=appr.run_id, agent_id=appr.agent_id)
    return out
