"""SENTINEL policy decision point (PDP).

One implementation of "record the request, evaluate policies, apply approvals, persist the decision,
publish events" shared by:
- the in-process gateway (security/tool_gateway.py), which then executes built-in tools, and
- the remote decision API (api/sentinel_routes.py), used by applications such as the e-commerce
  backend, which execute their own tools only after SENTINEL returns ALLOW.

Fail-closed rules
- A tool unknown to SENTINEL is evaluated as unknown (SEN-001 blocks it).
- Any exception during evaluation produces BLOCK.
- In a baseline test run (enforce=False) the decision is recorded exactly as in a protected run, but
  the caller is told it may execute. Baseline is only reachable from AEGIS test runs, which always
  run against sandbox fixtures (see TestRun.mode).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.adapters.tools.registry import TOOL_SPECS
from app.core.timeutil import new_id, utcnow
from app.database.base import db_session
from app.models import Policy, PolicyDecision, ToolCall, ToolDefinition
from app.security.audit import load_policy_views
from app.security.content_scan import flags_for
from app.security.input_validation import MAX_ARGS_JSON_BYTES, canonical_args_hash
from app.security.policy_engine import AgentView, EvaluationInput, PolicyEvaluation, compute_policy_hash, evaluate, grant_for
from app.security.redaction import find_canaries, find_sensitive, redact_args, redact_text, redact_value
from app.sentinel.approval import approval_service
from app.sentinel.enforcement import enforce as apply_approval
from app.sentinel.rate_limits import agent_recent_calls, agent_window_seconds, session_history
from app.websocket.event_bus import event_bus

log = logging.getLogger("aegis.sentinel.pdp")

ORIGINS = ("user_task", "tool_output", "approval_resubmit", "direct")
_SEVERITY_FOR = {"BLOCK": "warning", "REQUIRE_APPROVAL": "notice", "ALLOW": "info"}


@dataclass(frozen=True)
class ToolMeta:
    name: str
    operation: str
    sensitivity: str
    sensitive_fields: Tuple[str, ...]
    provider: str  # builtin | remote:<namespace>
    args_schema: Optional[Dict[str, Any]] = None


def builtin_meta(name: str) -> Optional[ToolMeta]:
    spec = TOOL_SPECS.get(name)
    if spec is None:
        return None
    return ToolMeta(name=spec.name, operation=spec.operation, sensitivity=spec.sensitivity,
                    sensitive_fields=tuple(spec.sensitive_fields), provider="builtin")


def remote_meta(name: str) -> Optional[ToolMeta]:
    with db_session() as db:
        row = db.get(ToolDefinition, name)
        if row is None or row.provider == "builtin":
            return None
        return ToolMeta(name=row.name, operation=row.operation, sensitivity=row.sensitivity,
                        sensitive_fields=tuple(row.sensitive_fields or ()), provider=row.provider,
                        args_schema=dict(row.args_schema or {}))


def validate_with_schema(schema: Dict[str, Any], raw: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """Validate remote tool arguments against the registered JSON schema.

    Error text names locations and the failed keyword only, never the submitted value.
    """
    from jsonschema import Draft202012Validator

    if not isinstance(raw, dict):
        return {}, "(root): must be an object"
    try:
        encoded = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        return {}, "(root): not JSON serialisable"
    if len(encoded.encode()) > MAX_ARGS_JSON_BYTES:
        return {}, "(root): arguments exceed size limit"
    errors = sorted(Draft202012Validator(schema).iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        parts = []
        for e in errors[:5]:
            loc = ".".join(str(x) for x in e.path) or "(root)"
            extra = ""
            if e.validator == "additionalProperties":
                extra = " (unexpected field)"
            parts.append(f"{loc}: {e.validator}{extra}")
        return dict(raw), "; ".join(parts)
    return dict(raw), None


@dataclass
class DecisionOutcome:
    tool_call_id: str
    decision_id: str
    tool_name: str
    agent_id: str
    decision: str
    reason: str
    matched_policy_key: Optional[str]
    matched_policy_version: Optional[int]
    risk: str
    evaluated_policy_keys: List[str]
    policy_hash: str
    approval_id: Optional[str]
    constraints: Dict[str, Any]
    grant: Dict[str, Any]
    enforced: bool
    execute: bool
    args_redacted: Dict[str, Any]
    timestamp: str
    started_perf: float = field(default=0.0, repr=False)

    def public(self) -> Dict[str, Any]:
        """Structured decision without confidential argument values."""
        return {
            "decision_id": self.decision_id, "tool_call_id": self.tool_call_id, "agent_id": self.agent_id,
            "tool_name": self.tool_name, "decision": self.decision, "execute": self.execute,
            "enforced": self.enforced, "matched_policy": self.matched_policy_key,
            "matched_policy_version": self.matched_policy_version, "policy_ids_evaluated": self.evaluated_policy_keys,
            "policy_hash": self.policy_hash[:16], "reason": self.reason, "risk": self.risk,
            "approval_id": self.approval_id, "constraints": self.constraints, "timestamp": self.timestamp,
        }


def decide(
    *,
    correlation_id: str,
    run_id: Optional[str],
    agent: AgentView,
    tool_name: str,
    meta: Optional[ToolMeta],
    args_dict: Dict[str, Any],
    args_error: Optional[str],
    resource: Dict[str, Any],
    session_subject: Optional[str],
    approval_id: Optional[str] = None,
    origin: str = "user_task",
    enforce: bool = True,
    approval_context: Optional[Dict[str, Any]] = None,
) -> DecisionOutcome:
    started = time.perf_counter()
    tool_name = str(tool_name)[:64]
    origin = origin if origin in ORIGINS else "user_task"
    args_hash = canonical_args_hash(agent.id, tool_name, args_dict)
    redacted = redact_args(args_dict, meta.sensitive_fields if meta else ()) if args_dict else {}
    flags: List[str] = []
    if meta and meta.operation == "egress":
        flags += [f"outbound_{c}" for c in find_sensitive(" ".join(str(v) for v in args_dict.values()))]
    if not enforce:
        flags.append("not_enforced_baseline")

    call_id = new_id("call")
    with db_session() as db:
        db.add(ToolCall(
            id=call_id, correlation_id=correlation_id, run_id=run_id, agent_id=agent.id, tool_name=tool_name,
            operation=meta.operation if meta else None, args_redacted=redacted, args_hash=args_hash,
            status="requested", flags=flags, origin=origin, approval_id=approval_id, enforced=enforce,
            created_at=utcnow(),
        ))
    event_bus.publish(
        "tool.requested", f"{agent.key} requested {tool_name}",
        payload={"tool_call_id": call_id, "tool": tool_name, "args": redacted, "origin": origin,
                 "agent_reported_origin": True, "provider": meta.provider if meta else "unknown"},
        correlation_id=correlation_id, run_id=run_id, agent_id=agent.id,
    )

    # ------------------------------------------------------------------ evaluate (fail closed)
    grant: Dict[str, Any] = {}
    try:
        with db_session() as db:
            policies = load_policy_views(db)
            history = session_history(db, correlation_id, call_id)
            recent = agent_recent_calls(db, agent.id, tool_name, agent_window_seconds(policies), call_id, run_id)
        inp = EvaluationInput(
            agent=agent, tool_name=tool_name,
            tool_operation=meta.operation if meta else None,
            tool_sensitivity=meta.sensitivity if meta else None,
            args=args_dict if args_error is None else None, args_error=args_error,
            resource=resource, session_subject=session_subject, session_history=history,
            agent_recent_calls=recent, now=utcnow(),
        )
        evaluation = evaluate(inp, policies)
        grant = grant_for(inp) or {}
    except Exception as exc:
        log.exception("policy evaluation failed")
        evaluation = PolicyEvaluation(
            decision="BLOCK", matched_policy_key=None,
            reason=f"Policy evaluation could not be completed ({type(exc).__name__}); denied by default",
            risk="high", evaluated=[], constraints={}, approval_policy_keys=[], policy_hash=compute_policy_hash([]),
        )

    def _check(aid: str):
        with db_session() as db:
            return approval_service.check_binding(db, aid, agent.id, tool_name, args_hash)

    enforced = apply_approval(evaluation, approval_id if enforce else None, _check)
    decision_id = new_id("dec")
    evaluated_keys = [e["key"] for e in evaluation.evaluated if e.get("result") not in ("disabled",)]
    new_approval_id: Optional[str] = None
    matched_version: Optional[int] = None
    ctx = approval_context or {}

    with db_session() as db:
        if enforced.matched_policy_key:
            matched_version = db.execute(select(Policy.version).where(Policy.key == enforced.matched_policy_key)).scalar_one_or_none()
        if enforce and enforced.approval_used and not approval_service.consume(db, enforced.approval_used, call_id):
            enforced.decision = "REQUIRE_APPROVAL"
            enforced.reason = "Approval was consumed by a concurrent request"
            enforced.approval_used = None
        if enforce and enforced.decision == "REQUIRE_APPROVAL":
            appr = approval_service.create(
                db, decision_id=decision_id, tool_call_id=call_id, correlation_id=correlation_id, run_id=run_id,
                agent_id=agent.id, tool_name=tool_name, args_hash=args_hash, args_redacted=redacted,
                raw_args=args_dict, reason=enforced.reason, matched_policy_key=enforced.matched_policy_key,
                sandbox_root=str(ctx.get("sandbox_root", "")), session_subject=session_subject,
                faults=dict(ctx.get("faults", {})),
            )
            new_approval_id = appr.id
        execute = enforced.decision == "ALLOW" or not enforce
        db.add(PolicyDecision(
            id=decision_id, tool_call_id=call_id, correlation_id=correlation_id, run_id=run_id, agent_id=agent.id,
            tool_name=tool_name, decision=enforced.decision, evaluated=evaluation.evaluated,
            matched_policy_key=enforced.matched_policy_key, reason=enforced.reason, risk=evaluation.risk,
            execution_status="pending" if execute else "not_executed", policy_hash=evaluation.policy_hash,
            approval_id=enforced.approval_used or new_approval_id, created_at=utcnow(),
        ))
        tc = db.get(ToolCall, call_id)
        tc.decision = enforced.decision
        tc.decision_id = decision_id
        if not execute:
            tc.status = "blocked" if enforced.decision == "BLOCK" else "pending_approval"
            tc.completed_at = utcnow()
            tc.latency_ms = (time.perf_counter() - started) * 1000
        tc.approval_id = new_approval_id or enforced.approval_used or tc.approval_id

    sev = _SEVERITY_FOR[enforced.decision]
    if enforced.decision == "BLOCK" and evaluation.risk == "critical":
        sev = "critical"
    summary = f"{enforced.decision} {tool_name} for {agent.key}" + (f" ({enforced.matched_policy_key})" if enforced.matched_policy_key else "")
    if not enforce and enforced.decision != "ALLOW":
        summary += ": recorded, not enforced (baseline run)"
    event_bus.publish(
        "policy.decision", summary, severity=sev,
        payload={"decision_id": decision_id, "tool_call_id": call_id, "tool": tool_name, "decision": enforced.decision,
                 "matched_policy": enforced.matched_policy_key, "matched_policy_version": matched_version,
                 "reason": enforced.reason, "risk": evaluation.risk, "policies_evaluated": evaluated_keys,
                 "approval_id": enforced.approval_used or new_approval_id, "policy_hash": evaluation.policy_hash[:12],
                 "enforced": enforce},
        correlation_id=correlation_id, run_id=run_id, agent_id=agent.id,
    )
    if enforce and enforced.decision == "BLOCK":
        event_bus.publish("tool.blocked", f"Blocked {tool_name} before execution", severity="warning",
                          payload={"tool_call_id": call_id, "tool": tool_name, "matched_policy": enforced.matched_policy_key},
                          correlation_id=correlation_id, run_id=run_id, agent_id=agent.id)
    elif enforce and enforced.decision == "REQUIRE_APPROVAL":
        event_bus.publish("approval.requested", f"Approval required for {tool_name}", severity="notice",
                          payload={"approval_id": new_approval_id, "tool_call_id": call_id, "tool": tool_name,
                                   "reason": enforced.reason, "args": redacted},
                          correlation_id=correlation_id, run_id=run_id, agent_id=agent.id)

    return DecisionOutcome(
        tool_call_id=call_id, decision_id=decision_id, tool_name=tool_name, agent_id=agent.id,
        decision=enforced.decision, reason=enforced.reason, matched_policy_key=enforced.matched_policy_key,
        matched_policy_version=matched_version, risk=evaluation.risk, evaluated_policy_keys=evaluated_keys,
        policy_hash=evaluation.policy_hash, approval_id=enforced.approval_used or new_approval_id,
        constraints=dict(evaluation.constraints), grant=grant, enforced=enforce, execute=execute,
        args_redacted=redacted, timestamp=utcnow().isoformat(), started_perf=started,
    )


def record_result(
    *,
    tool_call_id: str,
    succeeded: bool,
    summary: Optional[Dict[str, Any]] = None,
    classification: Optional[str] = None,
    untrusted: bool = False,
    text_for_scan: str = "",
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    total_latency_ms: Optional[float] = None,
    exec_latency_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist the outcome of an execution that SENTINEL allowed (or did not enforce) and publish it.

    Returns {"flags": [...], "status": ...}. Raises ValueError if the call is not awaiting a result.
    """
    scan_flags = flags_for(text_for_scan) if succeeded else []
    canaries = find_canaries(text_for_scan) if succeeded else []
    with db_session() as db:
        tc = db.get(ToolCall, tool_call_id)
        if tc is None or tc.status != "requested" or tc.decision_id is None:
            raise ValueError("tool call is not awaiting a result")
        if tc.decision != "ALLOW" and tc.enforced:
            raise ValueError("tool call was not allowed; execution must not have happened")
        pd = db.get(PolicyDecision, tc.decision_id)
        tc.completed_at = utcnow()
        tc.latency_ms = total_latency_ms
        if succeeded:
            tc.status = "executed"
            tc.result_summary = redact_value(summary or {})
            tc.result_classification = classification
            tc.flags = list(tc.flags or []) + [f"returned_marker:{c}" for c in canaries] + \
                (["untrusted_content"] if untrusted else []) + scan_flags
            pd.execution_status = "executed"
        else:
            tc.status = "failed"
            tc.error_class = (error_class or "tool_error")[:32]
            tc.error_message = redact_text(error_message or "", 300)
            pd.execution_status = "failed"
        info = dict(correlation_id=tc.correlation_id, run_id=tc.run_id, agent_id=tc.agent_id, tool=tc.tool_name,
                    enforced=tc.enforced, decision=tc.decision, flags=list(tc.flags or []))
    note = "" if info["enforced"] else " (baseline run: SENTINEL did not enforce its decision)"
    if succeeded:
        event_bus.publish(
            "tool.executed", f"Executed {info['tool']}" + (f" in {exec_latency_ms:.0f} ms" if exec_latency_ms is not None else "") + note,
            severity="warning" if (not info["enforced"] and info["decision"] != "ALLOW") else "info",
            payload={"tool_call_id": tool_call_id, "tool": info["tool"], "latency_ms": round(exec_latency_ms or 0, 2),
                     "classification": classification, "summary": redact_value(summary or {}), "untrusted_content": untrusted,
                     "markers_returned": len(canaries), "suspected_injection": bool(scan_flags), "enforced": info["enforced"],
                     "decision": info["decision"]},
            correlation_id=info["correlation_id"], run_id=info["run_id"], agent_id=info["agent_id"],
        )
    else:
        event_bus.publish(
            "tool.failed", f"{info['tool']} failed: {error_class}" + note,
            severity="error" if error_class in (None, "tool_error") else "warning",
            payload={"tool_call_id": tool_call_id, "tool": info["tool"], "error_class": error_class,
                     "message": redact_text(error_message or "", 300), "latency_ms": round(exec_latency_ms or 0, 2)},
            correlation_id=info["correlation_id"], run_id=info["run_id"], agent_id=info["agent_id"],
        )
    return {"flags": info["flags"], "status": "executed" if succeeded else "failed"}
