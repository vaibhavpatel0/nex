"""Policy enforcement point: the only code path that executes shopping-agent tools.

For every proposed tool call:
  1. record the proposal (TOOL_REQUESTED)
  2. validate arguments locally (defence in depth; SENTINEL validates again against the registered schema)
  3. look up resource attributes (record ownership) from this application's database
  4. ask SENTINEL for a decision (POLICY_CHECKING). Unreachable / error -> BLOCK (fail closed)
  5. BLOCK -> not executed. REQUIRE_APPROVAL -> not executed, held for staff. ALLOW -> execute with timeout
  6. report the real outcome to SENTINEL and record it for the Workbench

A baseline test run is the only case where SENTINEL answers execute=true for a non-ALLOW decision,
and this module additionally refuses that unless the run is using a sandbox fixture.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from shop.agent.events import bus
from shop.agent.sentinel_client import SentinelUnavailable, sentinel
from shop.agent.tools import NAMESPACE, TOOLS, ToolContext
from shop.config import get_settings
from shop.db import session_scope, utcnow
from shop.models import AgentRun, PendingAction
from shop.redact import text as redact_text
from shop.redact import value as redact_value
from shop.services.store import Faults, ServiceError

log = logging.getLogger("shop.pep")


@dataclass
class RunContext:
    correlation_id: str
    customer_id: str
    fixture: str  # live | sandbox
    factory: Optional[sessionmaker]  # None = live store database
    actor: str
    faults: Faults = field(default_factory=Faults)
    enforce: bool = True
    invoked: List[str] = field(default_factory=list)
    calls: int = 0
    blocked: int = 0


@dataclass
class CallOutcome:
    tool: str
    decision: str
    executed: bool
    status: str  # executed | failed | blocked | pending_approval | invalid
    content_for_model: str
    approval_id: Optional[str] = None


def _sanitize_args(args: Any) -> Any:
    return redact_value(args) if isinstance(args, (dict, list)) else redact_text(str(args), 300)


def _validation_text(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ())) or '(root)'}: {e.get('type')}" for e in exc.errors()[:4])


async def handle(ctx: RunContext, name: str, raw_args: Any, *, source: str = "model",
                 approval_id: Optional[str] = None, origin: str = "user_task") -> CallOutcome:
    s = get_settings()
    ctx.calls += 1
    tool = TOOLS.get(name)
    qualified = f"{NAMESPACE}.{name}" if tool else f"{NAMESPACE}.{str(name)[:40]}"
    cid = ctx.correlation_id
    bus.record(cid, "tool.proposed", state="TOOL_REQUESTED", tool=qualified,
               payload={"tool": qualified, "arguments": _sanitize_args(raw_args), "source": source,
                        "known_tool": tool is not None})

    # -- local validation (defence in depth) and resource attributes
    args_obj = None
    local_error: Optional[str] = None
    resource: Dict[str, Any] = {}
    if tool is None:
        local_error = "unknown tool"
    else:
        try:
            args_obj = tool.args.model_validate(raw_args if isinstance(raw_args, dict) else {"__invalid__": raw_args})
        except ValidationError as exc:
            local_error = _validation_text(exc)
    if tool is not None and args_obj is not None:
        try:
            with session_scope(ctx.factory) as db:
                resource = tool.resource(args_obj, ToolContext(db=db, customer_id=ctx.customer_id, faults=Faults(), actor=ctx.actor))
        except Exception as exc:  # lookup failure: SENTINEL sees no ownership and denies
            resource = {"lookup_error": type(exc).__name__}

    # -- decision
    bus.record(cid, "policy.checking", state="POLICY_CHECKING", tool=qualified,
               payload={"resource": redact_value(resource), "local_validation": local_error or "ok"})
    try:
        d = await sentinel.evaluate(cid, qualified, raw_args, resource, ctx.customer_id, approval_id=approval_id, origin=origin)
    except SentinelUnavailable as exc:
        ctx.blocked += 1
        reason = f"{exc}. Denied by default."
        bus.record(cid, "policy.decision", state="BLOCKED", tool=qualified, decision="BLOCK", execution_status="not_executed",
                   payload={"decision": "BLOCK", "matched_policy": None, "reason": reason, "source": "local fail-closed rule",
                            "evaluated_at": utcnow().isoformat()})
        bus.record(cid, "tool.not_executed", tool=qualified, execution_status="not_executed",
                   payload={"invoked": False, "reason": "SENTINEL unavailable"})
        return CallOutcome(qualified, "BLOCK", False, "blocked",
                           json.dumps({"status": "BLOCKED", "reason": "The security service is unavailable, so this action was not run."}))

    decision = d["decision"]
    execute = bool(d.get("execute"))
    enforced = bool(d.get("enforced", True))
    if execute and not enforced and ctx.fixture != "sandbox":
        execute = False  # never run unenforced outside a sandbox, whatever the response says
    state = {"BLOCK": "BLOCKED", "REQUIRE_APPROVAL": "WAITING_FOR_APPROVAL"}.get(decision, "POLICY_CHECKING")
    if execute:
        state = "TOOL_EXECUTING"
    bus.record(cid, "policy.decision", state=state if not execute else None, tool=qualified, decision=decision,
               execution_status="pending" if execute else "not_executed",
               payload={"decision": decision, "matched_policy": d.get("matched_policy"),
                        "matched_policy_version": d.get("matched_policy_version"), "reason": d.get("reason"),
                        "risk": d.get("risk"), "policy_hash": d.get("policy_hash"), "policies_evaluated": d.get("policy_ids_evaluated"),
                        "tool_call_id": d.get("tool_call_id"), "decision_id": d.get("decision_id"),
                        "approval_id": d.get("approval_id"), "enforced": enforced, "evaluated_at": d.get("timestamp"),
                        "args_valid": d.get("args_valid"), "args_error": d.get("args_error")})

    if not execute:
        if decision == "REQUIRE_APPROVAL" and d.get("approval_id") and tool is not None and args_obj is not None:
            with session_scope() as db:
                if db.get(PendingAction, d["approval_id"]) is None:
                    # Keep the exact arguments SENTINEL hashed, so re-submission matches the approval.
                    db.add(PendingAction(approval_id=d["approval_id"], correlation_id=cid, tool=name, args=dict(raw_args),
                                         customer_id=ctx.customer_id, fixture=ctx.fixture, created_at=utcnow()))
        ctx.blocked += 1 if decision == "BLOCK" else 0
        bus.record(cid, "tool.not_executed", tool=qualified, execution_status="not_executed",
                   payload={"invoked": False, "decision": decision,
                            "reason": "Waiting for staff approval" if decision == "REQUIRE_APPROVAL" else "Blocked by SENTINEL"})
        msg = ({"status": "NEEDS_APPROVAL", "approval_id": d.get("approval_id"),
                "message": "This action was not performed. It is waiting for review by store staff."}
               if decision == "REQUIRE_APPROVAL" else
               {"status": "BLOCKED", "policy": d.get("matched_policy"), "reason": d.get("reason"),
                "message": "This action was not performed because store security policy does not allow it."})
        return CallOutcome(qualified, decision, False, "pending_approval" if decision == "REQUIRE_APPROVAL" else "blocked",
                           json.dumps(msg), approval_id=d.get("approval_id"))

    # -- execute (ALLOW, or an unenforced baseline call in a sandbox)
    if tool is None or args_obj is None:
        err = local_error or "invalid arguments"
        # Only reachable in a baseline run: local validation stops the call before the tool runs.
        await _report(d, {"status": "failed", "error_class": "rejected_before_execution", "error_message": err, "latency_ms": 0})
        bus.record(cid, "tool.result", tool=qualified, execution_status="failed", error_class="rejected_before_execution",
                   payload={"invoked": False, "error": err, "note": "Rejected by local argument validation before execution"})
        return CallOutcome(qualified, decision, False, "invalid", json.dumps({"status": "ERROR", "error": err}))

    grant = d.get("grant") or {}
    t0 = time.perf_counter()
    result = None
    error_class: Optional[str] = None
    error_message: Optional[str] = None
    ctx.invoked.append(qualified)
    try:
        with session_scope(ctx.factory) as db:
            tctx = ToolContext(db=db, customer_id=ctx.customer_id, faults=ctx.faults, actor=ctx.actor, grant=grant)
            result = await asyncio.wait_for(tool.run(args_obj, tctx), timeout=s.tool_timeout_seconds)
    except asyncio.TimeoutError:
        error_class, error_message = "timeout", f"Tool did not finish within {s.tool_timeout_seconds:g}s"
    except ServiceError as exc:
        error_class, error_message = exc.classification, exc.message
    except Exception as exc:
        log.exception("tool %s crashed", name)
        error_class, error_message = "tool_error", f"Unhandled {type(exc).__name__}"
    ms = (time.perf_counter() - t0) * 1000

    if result is not None:
        raw = json.dumps(result.data, default=str)
        rep = await _report(d, {"status": "executed", "result_summary": result.summary, "result_preview": raw[:8000],
                                "classification": result.classification, "untrusted": result.untrusted,
                                "latency_ms": ms, "exec_latency_ms": ms})
        flags = (rep or {}).get("flags", [])
        bus.record(cid, "tool.result", tool=qualified, execution_status="executed",
                   payload={"invoked": True, "duration_ms": round(ms, 1), "result": redact_value(result.data),
                            "classification": result.classification, "untrusted_content": result.untrusted,
                            "sentinel_flags": [f for f in flags if not f.startswith("returned_marker")],
                            "enforced": enforced, "decision": decision})
        return CallOutcome(qualified, decision, True, "executed", raw[:12000])

    await _report(d, {"status": "failed", "error_class": error_class, "error_message": error_message, "latency_ms": ms,
                      "exec_latency_ms": ms})
    bus.record(cid, "tool.result", tool=qualified, execution_status="failed", error_class=error_class,
               payload={"invoked": True, "duration_ms": round(ms, 1), "error_class": error_class, "error": error_message})
    return CallOutcome(qualified, decision, True, "failed",
                       json.dumps({"status": "FAILED", "error_class": error_class, "error": error_message,
                                   "message": "The action failed. Do not guess the result."}))


async def _report(d: Dict[str, Any], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return await sentinel.report(d["tool_call_id"], body)
    except SentinelUnavailable as exc:
        log.warning("result report failed: %s", exc)
        return None


async def execute_approved(approval_id: str, staff_email: str) -> Dict[str, Any]:
    """Run an approved action exactly as it was proposed. SENTINEL re-evaluates it with the approval;
    if the arguments differ in any way, the approval does not apply."""
    with session_scope() as db:
        pa = db.get(PendingAction, approval_id)
        if pa is None or pa.status != "pending":
            raise ServiceError("not_found", "No pending action for this approval")
        info = dict(correlation_id=pa.correlation_id, tool=pa.tool, args=dict(pa.args), customer_id=pa.customer_id, fixture=pa.fixture)
    if info["fixture"] != "live":
        raise ServiceError("invalid", "Approvals from sandbox test runs are not executed")
    from uuid import uuid4

    # Execution happens in a new SENTINEL session linked to the original by note; the approval binds the action.
    new_cid = "cor_" + uuid4().hex[:20]
    with session_scope() as db:
        db.add(AgentRun(id=new_cid, kind="chat", conversation_id=None, customer_id=info["customer_id"], started_by=staff_email,
                        message=f"Execute approved action {approval_id} ({info['tool']}) from run {info['correlation_id']}",
                        status="RUNNING", fixture="live", model_provider="none", model="none (staff-approved action, no model call)",
                        started_at=utcnow()))
    await sentinel.open_session(new_cid, f"Staff executes approved action {approval_id}", info["customer_id"], None)
    ctx = RunContext(correlation_id=new_cid, customer_id=info["customer_id"], fixture="live", factory=None, actor=staff_email)
    bus.record(new_cid, "run.started", state="RUNNING", payload={"kind": "approved_action", "approval_id": approval_id,
                                                                 "original_correlation_id": info["correlation_id"], "by": staff_email})
    out = await handle(ctx, info["tool"], info["args"], source="staff_approval", approval_id=approval_id, origin="approval_resubmit")
    status = "executed" if out.status == "executed" else ("failed" if out.status == "failed" else "not_executed")
    with session_scope() as db:
        pa = db.get(PendingAction, approval_id)
        pa.status = "executed" if status == "executed" else ("failed" if status == "failed" else "pending")
        pa.executed_at = utcnow() if status != "not_executed" else None
        pa.executed_by = staff_email
        pa.result = {"status": out.status, "decision": out.decision, "correlation_id": new_cid}
        r = db.get(AgentRun, new_cid)
        r.status = "COMPLETED" if status == "executed" else "FAILED"
        r.final_text = f"Approved action {out.status}"
        r.ended_at = utcnow()
    bus.record(new_cid, "run.completed" if status == "executed" else "run.failed",
               state="COMPLETED" if status == "executed" else "FAILED", payload={"outcome": out.status, "decision": out.decision})
    await sentinel.complete(new_cid, {"final_output": f"Approved action {out.status}" if status == "executed" else None,
                                      "error_kind": None if status == "executed" else "not_executed",
                                      "error": None if status == "executed" else out.content_for_model[:300]})
    return {"approval_id": approval_id, "decision": out.decision, "status": out.status, "correlation_id": new_cid}
