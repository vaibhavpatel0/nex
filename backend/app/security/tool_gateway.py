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
from app.security.input_validation import validate_tool_args
from app.security.policy_engine import AgentView
from app.sentinel.decision_point import builtin_meta, decide, record_result

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
    # False only for baseline test runs (sandbox fixtures): decisions recorded but not enforced.
    enforce: bool = True


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
    enforced: bool = True

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
            "enforced": self.enforced,
            "timestamp": self.timestamp,
        }


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
        settings = get_settings()
        tool_name = str(tool_name)[:64]
        spec = TOOL_SPECS.get(tool_name)
        impl = _TOOLS.get(tool_name)
        args_obj, args_dict, args_error = validate_tool_args(spec, raw_args)
        tool_ctx = ToolContext(
            sandbox=ctx.sandbox, correlation_id=ctx.correlation_id, agent_id=ctx.agent.id,
            session_subject=ctx.session_subject, faults=ctx.faults, fault_state=ctx.fault_state,
        )
        resource: Dict[str, Any] = {}
        if impl is not None and args_obj is not None:
            try:
                resource = impl.resource_attributes(args_obj, tool_ctx)
            except Exception as exc:  # resource lookup failure -> rules see empty attrs (deny)
                log.warning("resource attribute lookup failed for %s: %s", tool_name, type(exc).__name__)
                resource = {"_lookup_error": type(exc).__name__}

        out = decide(
            correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent=ctx.agent, tool_name=tool_name,
            meta=builtin_meta(tool_name), args_dict=args_dict, args_error=args_error, resource=resource,
            session_subject=ctx.session_subject, approval_id=approval_id, origin=origin, enforce=ctx.enforce,
            approval_context={"sandbox_root": str(ctx.sandbox.root), "faults": ctx.faults},
        )
        base = dict(
            tool_call_id=out.tool_call_id, decision_id=out.decision_id, tool_name=tool_name, decision=out.decision,
            reason=out.reason, matched_policy_key=out.matched_policy_key, risk=out.risk,
            evaluated_policy_keys=out.evaluated_policy_keys, timestamp=out.timestamp, agent_id=ctx.agent.id,
            enforced=out.enforced,
        )
        if not out.execute:
            status = "blocked" if out.decision == "BLOCK" else "pending_approval"
            return GatewayResult(status=status, approval_id=out.approval_id,
                                 latency_ms=(time.perf_counter() - out.started_perf) * 1000, **base)
        if impl is None or args_obj is None:
            # Only reachable in a baseline run: nothing executable exists for an unknown tool or invalid
            # arguments, so record an honest failure instead of pretending it ran.
            record_result(tool_call_id=out.tool_call_id, succeeded=False, error_class="rejected_before_execution",
                          error_message="No executable tool for this request",
                          total_latency_ms=(time.perf_counter() - out.started_perf) * 1000, exec_latency_ms=0)
            return GatewayResult(status="failed", error_class="rejected_before_execution", error_message="No executable tool for this request",
                                 latency_ms=(time.perf_counter() - out.started_perf) * 1000, **base)

        # ---------------------------------------------------------------- execute
        timeout = float(out.constraints.get("timeout_seconds", settings.default_tool_timeout_seconds))
        tool_ctx.grant = out.grant
        tool_ctx.constraints = out.constraints
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
        total_ms = (time.perf_counter() - out.started_perf) * 1000

        if output is not None:
            record_result(tool_call_id=out.tool_call_id, succeeded=True, summary=output.summary,
                          classification=output.classification, untrusted=output.untrusted, text_for_scan=output.text,
                          total_latency_ms=total_ms, exec_latency_ms=exec_ms)
            return GatewayResult(status="executed", output_text=output.text, data=output.data, latency_ms=total_ms, **base)
        record_result(tool_call_id=out.tool_call_id, succeeded=False, error_class=error_class, error_message=error_message,
                      total_latency_ms=total_ms, exec_latency_ms=exec_ms)
        return GatewayResult(status="failed", error_class=error_class, error_message=error_message,
                             latency_ms=total_ms, **base)


gateway = ToolGateway()
