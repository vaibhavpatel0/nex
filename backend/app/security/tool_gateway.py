"""SENTINEL tool gateway: the only supported execution path for protected tools.

Pipeline for every call (see docs/ARCHITECTURE.md, "Runtime event pipeline"):
 1. correlation id propagated from the invocation context
 2. arguments validated against the tool schema
 3. agent identity and permission profile loaded
 4. policies evaluated deterministically (fail closed on error)
 5. decision persisted
 6. ALLOW -> execute through the protected adapter with a timeout
    BLOCK -> never reaches the tool
    REQUIRE_APPROVAL -> approval request created, tool not executed
 7. result / failure persisted with classification and timing
 8. events persisted and streamed to dashboards
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.adapters.tools.base import ToolContext, ToolError, issue_gateway_capability
from app.adapters.tools.registry import TOOL_SPECS, _build_implementations
from app.adapters.tools.sandbox import Sandbox
from app.config.settings import get_settings
from app.core.timeutil import new_id, utcnow
from app.database.base import db_session
from app.models import PolicyDecision, ToolCall
from app.security.audit import load_policy_views
from app.security.input_validation import canonical_args_hash, validate_tool_args
from app.security.policy_engine import (
    AgentView,
    EvaluationInput,
    PolicyEvaluation,
    compute_policy_hash,
    evaluate,
    grant_for,
)
from app.security.redaction import find_canaries, find_sensitive, redact_args, redact_text, redact_value
from app.sentinel.approval import approval_service
from app.sentinel.enforcement import enforce
from app.sentinel.rate_limits import agent_recent_calls, agent_window_seconds, session_history
from app.websocket.event_bus import event_bus

log = logging.getLogger("aegis.sentinel")

# The capability is module-private. Nothing else in the application receives it.
_CAPABILITY = issue_gateway_capability()
_TOOLS = _build_implementations()


@dataclass
class InvocationContext:
    correlation_id: str
    agent: AgentView
    sandbox: Sandbox
    run_id: Optional[str] = None
    session_subject: Optional[str] = None
    faults: Dict[str, str] = field(default_factory=dict)
    fault_state: Dict[str, int] = field(default_factory=dict)
    triggered_by: Optional[str] = None


@dataclass
class GatewayResult:
    tool_call_id: str
    decision_id: str
    tool_name: str
    decision: str
    status: str  # blocked | pending_approval | executed | failed
    reason: str
    matched_policy_key: Optional[str]
    risk: str
    evaluated_policy_keys: List[str]
    approval_id: Optional[str] = None
    output_text: Optional[str] = None  # returned to the agent only; never persisted in full
    data: Any = None
    error_class: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: str = ""
    agent_id: str = ""

    def public_dict(self) -> Dict[str, Any]:
        """Decision record safe to return through the API (no confidential values)."""
        return {
            "decision_id": self.decision_id,
            "tool_call_id": self.tool_call_id,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "decision": self.decision,
            "execution_status": self.status,
            "matched_policy": self.matched_policy_key,
            "policy_ids_evaluated": self.evaluated_policy_keys,
            "reason": self.reason,
            "risk": self.risk,
            "approval_id": self.approval_id,
            "error_class": self.error_class,
            "timestamp": self.timestamp,
        }


_SEVERITY_FOR = {"BLOCK": "warning", "REQUIRE_APPROVAL": "notice", "ALLOW": "info"}


class ToolGateway:
    async def invoke(
        self,
        ctx: InvocationContext,
        tool_name: str,
        raw_args: Any,
        *,
        approval_id: Optional[str] = None,
        origin: str = "user_task",
    ) -> GatewayResult:
        started = time.perf_counter()
        settings = get_settings()
        tool_name = str(tool_name)[:64]
        spec = TOOL_SPECS.get(tool_name)
        impl = _TOOLS.get(tool_name)
        args_obj, args_dict, args_error = validate_tool_args(spec, raw_args)
        args_hash = canonical_args_hash(ctx.agent.id, tool_name, args_dict)
        sensitive_fields = spec.sensitive_fields if spec else ()
        redacted = redact_args(args_dict, sensitive_fields) if args_dict else {}
        flags: List[str] = []
        if spec and spec.operation == "egress":
            flags += [f"outbound_{c}" for c in find_sensitive(" ".join(str(v) for v in args_dict.values()))]

        call_id = new_id("call")
        origin = origin if origin in ("user_task", "tool_output", "approval_resubmit", "direct") else "user_task"
        with db_session() as db:
            db.add(ToolCall(
                id=call_id, correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
                tool_name=tool_name, operation=spec.operation if spec else None, args_redacted=redacted,
                args_hash=args_hash, status="requested", flags=flags, origin=origin, approval_id=approval_id,
                created_at=utcnow(),
            ))
        event_bus.publish(
            "tool.requested", f"{ctx.agent.key} requested {tool_name}",
            payload={"tool_call_id": call_id, "tool": tool_name, "args": redacted, "origin": origin,
                     "agent_reported_origin": True},
            correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
        )

        grant = {}
        tool_ctx = ToolContext(
            sandbox=ctx.sandbox, correlation_id=ctx.correlation_id, agent_id=ctx.agent.id,
            session_subject=ctx.session_subject, faults=ctx.faults, fault_state=ctx.fault_state,
        )

        # ---------------------------------------------------------------- evaluate (fail closed)
        evaluation: PolicyEvaluation
        try:
            resource: Dict[str, Any] = {}
            if impl is not None and args_obj is not None:
                try:
                    resource = impl.resource_attributes(args_obj, tool_ctx)
                except Exception as exc:  # resource lookup failure -> rules see empty attrs (deny)
                    log.warning("resource attribute lookup failed for %s: %s", tool_name, type(exc).__name__)
                    resource = {"_lookup_error": type(exc).__name__}
            with db_session() as db:
                policies = load_policy_views(db)
                history = session_history(db, ctx.correlation_id, call_id)
                recent = agent_recent_calls(db, ctx.agent.id, tool_name, agent_window_seconds(policies), call_id, ctx.run_id)
            inp = EvaluationInput(
                agent=ctx.agent, tool_name=tool_name,
                tool_operation=spec.operation if spec else None,
                tool_sensitivity=spec.sensitivity if spec else None,
                args=args_dict if args_error is None else None, args_error=args_error,
                resource=resource, session_subject=ctx.session_subject, session_history=history,
                agent_recent_calls=recent, now=utcnow(),
            )
            evaluation = evaluate(inp, policies)
            grant = grant_for(inp) or {}
        except Exception as exc:
            log.exception("policy evaluation failed")
            evaluation = PolicyEvaluation(
                decision="BLOCK", matched_policy_key=None,
                reason=f"Policy evaluation could not be completed ({type(exc).__name__}); denied by default",
                risk="high", evaluated=[], constraints={}, approval_policy_keys=[],
                policy_hash=compute_policy_hash([]),
            )

        # ---------------------------------------------------------------- approvals
        def _check(aid: str):
            with db_session() as db:
                return approval_service.check_binding(db, aid, ctx.agent.id, tool_name, args_hash)

        enforced = enforce(evaluation, approval_id, _check)
        decision_id = new_id("dec")
        evaluated_keys = [e["key"] for e in evaluation.evaluated if e.get("result") not in ("disabled",)]
        new_approval_id: Optional[str] = None

        with db_session() as db:
            status = {"BLOCK": "blocked", "REQUIRE_APPROVAL": "pending_approval", "ALLOW": "allowed"}[enforced.decision]
            if enforced.approval_used:
                if not approval_service.consume(db, enforced.approval_used, call_id):
                    # Lost a race with another consumer: fail closed.
                    enforced.decision = "REQUIRE_APPROVAL"
                    enforced.reason = "Approval was consumed by a concurrent request"
                    enforced.approval_used = None
                    status = "pending_approval"
            if enforced.decision == "REQUIRE_APPROVAL":
                appr = approval_service.create(
                    db, decision_id=decision_id, tool_call_id=call_id, correlation_id=ctx.correlation_id,
                    run_id=ctx.run_id, agent_id=ctx.agent.id, tool_name=tool_name, args_hash=args_hash,
                    args_redacted=redacted, raw_args=args_dict, reason=enforced.reason,
                    matched_policy_key=enforced.matched_policy_key, sandbox_root=str(ctx.sandbox.root),
                    session_subject=ctx.session_subject, faults=ctx.faults,
                )
                new_approval_id = appr.id
            db.add(PolicyDecision(
                id=decision_id, tool_call_id=call_id, correlation_id=ctx.correlation_id, run_id=ctx.run_id,
                agent_id=ctx.agent.id, tool_name=tool_name, decision=enforced.decision,
                evaluated=evaluation.evaluated, matched_policy_key=enforced.matched_policy_key,
                reason=enforced.reason, risk=evaluation.risk,
                execution_status="not_executed" if enforced.decision != "ALLOW" else "pending",
                policy_hash=evaluation.policy_hash, approval_id=enforced.approval_used or new_approval_id,
                created_at=utcnow(),
            ))
            tc = db.get(ToolCall, call_id)
            tc.decision = enforced.decision
            tc.decision_id = decision_id
            if enforced.decision != "ALLOW":
                tc.status = status
                tc.completed_at = utcnow()
                tc.latency_ms = (time.perf_counter() - started) * 1000
            if new_approval_id:
                tc.approval_id = new_approval_id
            elif enforced.approval_used:
                tc.approval_id = enforced.approval_used

        event_bus.publish(
            "policy.decision",
            f"{enforced.decision} {tool_name} for {ctx.agent.key}" + (f" ({enforced.matched_policy_key})" if enforced.matched_policy_key else ""),
            severity=_SEVERITY_FOR[enforced.decision] if not (enforced.decision == "BLOCK" and evaluation.risk == "critical") else "critical",
            payload={
                "decision_id": decision_id, "tool_call_id": call_id, "tool": tool_name,
                "decision": enforced.decision, "matched_policy": enforced.matched_policy_key,
                "reason": enforced.reason, "risk": evaluation.risk, "policies_evaluated": evaluated_keys,
                "approval_id": enforced.approval_used or new_approval_id,
                "policy_hash": evaluation.policy_hash[:12],
            },
            correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
        )

        base = dict(
            tool_call_id=call_id, decision_id=decision_id, tool_name=tool_name, decision=enforced.decision,
            reason=enforced.reason, matched_policy_key=enforced.matched_policy_key, risk=evaluation.risk,
            evaluated_policy_keys=evaluated_keys, timestamp=utcnow().isoformat(), agent_id=ctx.agent.id,
        )

        if enforced.decision == "BLOCK":
            event_bus.publish(
                "tool.blocked", f"Blocked {tool_name} before execution",
                severity="warning", payload={"tool_call_id": call_id, "tool": tool_name, "matched_policy": enforced.matched_policy_key},
                correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
            )
            return GatewayResult(status="blocked", latency_ms=(time.perf_counter() - started) * 1000, **base)

        if enforced.decision == "REQUIRE_APPROVAL":
            event_bus.publish(
                "approval.requested", f"Approval required for {tool_name}",
                severity="notice",
                payload={"approval_id": new_approval_id, "tool_call_id": call_id, "tool": tool_name,
                         "reason": enforced.reason, "args": redacted},
                correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
            )
            return GatewayResult(status="pending_approval", approval_id=new_approval_id,
                                 latency_ms=(time.perf_counter() - started) * 1000, **base)

        # ---------------------------------------------------------------- execute
        assert impl is not None and args_obj is not None  # ALLOW implies a known tool with valid args
        timeout = float(evaluation.constraints.get("timeout_seconds", settings.default_tool_timeout_seconds))
        tool_ctx.grant = grant
        tool_ctx.constraints = evaluation.constraints
        exec_started = time.perf_counter()
        output = None
        error_class: Optional[str] = None
        error_message: Optional[str] = None
        try:
            output = await asyncio.wait_for(impl.execute(args_obj, tool_ctx, capability=_CAPABILITY), timeout=timeout)
        except asyncio.TimeoutError:
            error_class, error_message = "timeout", f"Tool did not complete within {timeout:g}s"
        except ToolError as exc:
            error_class, error_message = exc.classification, exc.message
        except Exception as exc:
            log.exception("tool %s raised", tool_name)
            error_class, error_message = "tool_error", f"Unhandled {type(exc).__name__} in tool adapter"
        exec_ms = (time.perf_counter() - exec_started) * 1000
        total_ms = (time.perf_counter() - started) * 1000

        canaries = find_canaries(output.text) if output is not None else []
        with db_session() as db:
            tc = db.get(ToolCall, call_id)
            pd = db.get(PolicyDecision, decision_id)
            tc.completed_at = utcnow()
            tc.latency_ms = total_ms
            if output is not None:
                tc.status = "executed"
                tc.result_summary = redact_value(output.summary)
                tc.result_classification = output.classification
                tc.flags = list(tc.flags or []) + [f"returned_marker:{c}" for c in canaries] + (["untrusted_content"] if output.untrusted else [])
                pd.execution_status = "executed"
            else:
                tc.status = "failed"
                tc.error_class = error_class
                tc.error_message = redact_text(error_message or "", 300)
                pd.execution_status = "failed"

        if output is not None:
            event_bus.publish(
                "tool.executed", f"Executed {tool_name} in {exec_ms:.0f} ms",
                payload={"tool_call_id": call_id, "tool": tool_name, "latency_ms": round(exec_ms, 2),
                         "classification": output.classification, "summary": output.summary,
                         "untrusted_content": output.untrusted, "markers_returned": len(canaries)},
                correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
            )
            return GatewayResult(status="executed", output_text=output.text, data=output.data,
                                 latency_ms=total_ms, **base)

        event_bus.publish(
            "tool.failed", f"{tool_name} failed: {error_class}",
            severity="error" if error_class in ("tool_error",) else "warning",
            payload={"tool_call_id": call_id, "tool": tool_name, "error_class": error_class,
                     "message": error_message, "latency_ms": round(exec_ms, 2)},
            correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id,
        )
        return GatewayResult(status="failed", error_class=error_class, error_message=error_message,
                             latency_ms=total_ms, **base)


gateway = ToolGateway()
