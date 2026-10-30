"""Interactive runtime sessions and direct gateway invocations (Runtime Protection page).

Runtime sessions use a persistent sandbox at ``data/sandboxes/runtime`` so that state
(sent messages, updated orders) is visible across sessions. It can be reset by an admin.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Set

from app.adapters.tools.sandbox import Sandbox
from app.aegis.scenario_runner import agent_snapshot, view_from_snapshot
from app.agents.base import AgentTask
from app.config.settings import get_settings
from app.core.timeutil import new_id
from app.database.base import db_session, spawn_detached
from app.models import Agent
from app.security.tool_gateway import GatewayResult, InvocationContext, gateway
from app.services.agent_executor import execute_agent

_tasks: Set[asyncio.Task] = set()


def runtime_sandbox() -> Sandbox:
    return Sandbox.open_or_create(get_settings().sandbox_dir / "runtime")


def reset_runtime_sandbox() -> None:
    root = get_settings().sandbox_dir / "runtime"
    shutil.rmtree(root, ignore_errors=True)
    Sandbox.create(root)


def _snapshot(agent_id: str) -> Dict[str, Any]:
    with db_session() as db:
        a = db.get(Agent, agent_id)
        if a is None:
            raise LookupError("agent not found")
        if not a.enabled:
            raise ValueError("agent is disabled")
        return agent_snapshot(a)


def start_session(agent_id: str, task: str, session_subject: Optional[str], triggered_by: str) -> str:
    snap = _snapshot(agent_id)
    correlation = new_id("cor")

    async def _go() -> None:
        await execute_agent(snapshot=snap, agent_view=view_from_snapshot(snap),
                            task=AgentTask(task=task, session_subject=session_subject), sandbox=runtime_sandbox(),
                            correlation_id=correlation, kind="runtime", triggered_by=triggered_by)

    t = spawn_detached(_go())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return correlation


async def direct_invoke(agent_id: str, tool: str, args: Any, session_subject: Optional[str],
                        correlation_id: Optional[str], approval_id: Optional[str], triggered_by: str) -> GatewayResult:
    from app.models import Trace
    from app.core.timeutil import utcnow

    snap = _snapshot(agent_id)
    view = view_from_snapshot(snap)
    correlation = correlation_id or new_id("cor")
    with db_session() as db:
        if db.get(Trace, correlation) is None:
            db.add(Trace(id=correlation, kind="direct", agent_id=view.id, agent_revision=view.revision, status="completed",
                         task_redacted=f"Direct tool call submitted by {triggered_by}", started_at=utcnow(),
                         ended_at=utcnow(), triggered_by=triggered_by))
        else:
            tr = db.get(Trace, correlation)
            if tr.agent_id != view.id:
                raise ValueError("correlation id belongs to a different agent")
    ctx = InvocationContext(correlation_id=correlation, agent=view, sandbox=runtime_sandbox(), session_subject=session_subject,
                            triggered_by=triggered_by)
    return await gateway.invoke(ctx, tool, args, approval_id=approval_id, origin="direct")


async def execute_approved(approval_id: str, triggered_by: str) -> GatewayResult:
    from app.sentinel.approval import approval_service

    pa = approval_service.pending_action(approval_id)
    if pa is None:
        raise LookupError("The original arguments are no longer held in memory (expired or the backend restarted). Re-run the agent.")
    snap = _snapshot(pa.agent_id)
    ctx = InvocationContext(correlation_id=pa.correlation_id, agent=view_from_snapshot(snap), sandbox=Sandbox.open_or_create(Path(pa.sandbox_root)), run_id=pa.run_id, session_subject=pa.session_subject,
        faults=pa.faults, triggered_by=triggered_by)
    return await gateway.invoke(ctx, pa.tool_name, dict(pa.args), approval_id=approval_id, origin="approval_resubmit")
