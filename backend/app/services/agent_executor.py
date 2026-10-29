"""Runs an agent against a sandbox with every tool call routed through SENTINEL.

Used by AEGIS test runs and by interactive runtime sessions so that both share exactly the
same enforcement path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.adapters.tools.registry import TOOL_SPECS
from app.adapters.tools.sandbox import Sandbox
from app.agents.base import AgentFailure, AgentOutput, AgentTask, AgentUnavailable, ToolDescriptor, ToolResult
from app.agents.factory import build_agent
from app.config.settings import get_settings
from app.core.timeutil import utcnow
from app.database.base import db_session
from app.models import Trace
from app.security.policy_engine import AgentView
from app.security.redaction import find_canaries, redact_text
from app.security.tool_gateway import InvocationContext, gateway
from app.websocket.event_bus import event_bus

log = logging.getLogger("aegis.executor")


class GatewayToolClient:
    """The only tool interface agents receive."""

    def __init__(self, ctx: InvocationContext) -> None:
        self._ctx = ctx

    async def call(self, tool_name: str, args: Any, origin: str = "user_task") -> ToolResult:
        r = await gateway.invoke(self._ctx, tool_name, args, origin=origin)
        return ToolResult(
            tool_name=r.tool_name, status=r.status, decision=r.decision, reason=r.reason,
            matched_policy=r.matched_policy_key, approval_id=r.approval_id, output_text=r.output_text,
            data=r.data, error_class=r.error_class,
        )

    def available_tools(self) -> List[ToolDescriptor]:
        out = []
        for name in self._ctx.agent.enabled_tools:
            spec = TOOL_SPECS.get(name)
            if spec:
                out.append(ToolDescriptor(name=name, description=spec.description, parameters=spec.json_schema()))
        return out


@dataclass
class ExecutionRecord:
    correlation_id: str
    status: str  # completed | error
    error_kind: Optional[str]  # unavailable | failure | timeout | None
    error: Optional[str]
    output: Optional[AgentOutput]
    final_text: Optional[str]
    final_missing: bool
    latency_ms: float
    context: InvocationContext


async def execute_agent(
    *,
    snapshot: Dict[str, Any],
    agent_view: AgentView,
    task: AgentTask,
    sandbox: Sandbox,
    correlation_id: str,
    kind: str,
    run_id: Optional[str] = None,
    scenario_key: Optional[str] = None,
    faults: Optional[Dict[str, str]] = None,
    triggered_by: Optional[str] = None,
    enforce: bool = True,
) -> ExecutionRecord:
    settings = get_settings()
    ctx = InvocationContext(
        correlation_id=correlation_id, agent=agent_view, sandbox=sandbox, run_id=run_id,
        session_subject=task.session_subject, faults=dict(faults or {}), triggered_by=triggered_by,
        enforce=enforce,
    )
    task.correlation_id = correlation_id
    task.run_id = run_id
    task.scenario_key = scenario_key
    task.faults = dict(faults or {})
    timeout = settings.remote_agent_timeout_seconds if snapshot.get("agent_type") == "remote_agent" else settings.agent_run_timeout_seconds
    with db_session() as db:
        db.add(Trace(
            id=correlation_id, kind=kind, run_id=run_id, scenario_key=scenario_key, agent_id=agent_view.id,
            agent_revision=agent_view.revision, status="running", task_redacted=redact_text(task.task, 2000),
            started_at=utcnow(), triggered_by=triggered_by,
        ))
    event_bus.publish(
        "agent.request", f"{agent_view.key} received a task" + (f" ({scenario_key})" if scenario_key else ""),
        payload={"task": task.task, "session_subject": task.session_subject, "scenario": scenario_key,
                 "model_fault": task.model_fault, "faults": faults or {}, "enforced": enforce,
                 "direct_tool_call": task.direct_tool_call},
        correlation_id=correlation_id, run_id=run_id, agent_id=agent_view.id,
    )

    def emit(event_type: str, summary: str, payload: Dict[str, Any]) -> None:
        event_bus.publish(event_type, summary, payload=payload, correlation_id=correlation_id,
                          run_id=run_id, agent_id=agent_view.id)

    started = time.perf_counter()
    output: Optional[AgentOutput] = None
    error_kind: Optional[str] = None
    error: Optional[str] = None
    try:
        adapter = build_agent(snapshot)
        output = await asyncio.wait_for(
            adapter.run(task, GatewayToolClient(ctx), emit), timeout=timeout
        )
        if output is not None and output.error_kind:
            # The agent ran and reported its own failure (for example a model timeout).
            error_kind, error = output.error_kind, output.error or output.error_kind
    except AgentUnavailable as exc:
        error_kind, error = "unavailable", str(exc)
    except AgentFailure as exc:
        error_kind, error = "failure", str(exc)
    except asyncio.TimeoutError:
        error_kind, error = "timeout", f"Agent did not finish within {timeout:g}s"
    except Exception as exc:  # adapter bug: record honestly
        log.exception("agent crashed")
        error_kind, error = "failure", f"Agent raised {type(exc).__name__}"
    latency = (time.perf_counter() - started) * 1000

    final_text = output.final_text if output else None
    final_missing = output is not None and (final_text is None or not str(final_text).strip())
    status = "completed" if error_kind is None else "error"
    with db_session() as db:
        tr = db.get(Trace, correlation_id)
        tr.status = status
        tr.ended_at = utcnow()
        tr.latency_ms = latency
        tr.error = error
        tr.final_output_missing = final_missing
        tr.final_output_redacted = redact_text(final_text, 4000) if final_text else None
        tr.usage = output.usage if output else None

    if error_kind:
        event_bus.publish(
            "agent.error", f"{agent_view.key} error: {error}", severity="error",
            payload={"error_kind": error_kind, "error": error, "latency_ms": round(latency, 1)},
            correlation_id=correlation_id, run_id=run_id, agent_id=agent_view.id,
        )
    else:
        markers = find_canaries(final_text or "")
        event_bus.publish(
            "agent.output",
            "Agent returned no final output (recorded as missing)" if final_missing else f"{agent_view.key} produced a final answer",
            severity="warning" if (final_missing or markers) else "info",
            payload={"final_output": final_text, "missing": final_missing, "latency_ms": round(latency, 1),
                     "markers_in_output": len(markers), "usage": output.usage if output else None,
                     "usage_note": None if (output and output.usage) else "Not reported by this agent type"},
            correlation_id=correlation_id, run_id=run_id, agent_id=agent_view.id,
        )
    return ExecutionRecord(
        correlation_id=correlation_id, status=status, error_kind=error_kind, error=error, output=output,
        final_text=final_text, final_missing=final_missing, latency_ms=latency, context=ctx,
    )
