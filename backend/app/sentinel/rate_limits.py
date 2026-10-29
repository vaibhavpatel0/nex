"""Call-history queries backing the rate-limit and repeated-failure policies.

Counts come from persisted ``tool_calls`` rows rather than in-memory counters, so limits
survive backend restarts and apply across concurrent requests in the same process.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models import ToolCall
from app.security.policy_engine import HistoryItem, PolicyView


def session_history(db: Session, correlation_id: str, exclude_call_id: str) -> List[HistoryItem]:
    rows = db.execute(
        select(ToolCall)
        .where(ToolCall.correlation_id == correlation_id, ToolCall.id != exclude_call_id)
        .order_by(ToolCall.created_at, ToolCall.id)
    ).scalars().all()
    return [
        HistoryItem(
            tool_name=r.tool_name, operation=r.operation, status=r.status,
            classification=r.result_classification, created_at=r.created_at,
            flags=tuple(r.flags or ()),
        )
        for r in rows
    ]


def agent_window_seconds(policies: Sequence[PolicyView]) -> float:
    windows = [
        float(p.params.get("window_seconds", 60))
        for p in policies
        if p.enabled and p.rule_type == "rate_limit" and p.params.get("scope") == "agent"
    ]
    return max(windows) if windows else 0.0


def agent_recent_calls(db: Session, agent_id: str, tool_name: str, window_seconds: float, exclude_call_id: str,
                       run_id: "str | None" = None) -> Dict[str, int]:
    """Calls by this agent inside the window. Test-run traffic is counted per run and runtime
    traffic separately, so an AEGIS suite cannot exhaust the limit that protects live sessions."""
    if window_seconds <= 0:
        return {"*": 0, tool_name: 0}
    since = utcnow() - timedelta(seconds=window_seconds)
    base = select(func.count(ToolCall.id)).where(
        ToolCall.agent_id == agent_id, ToolCall.created_at >= since, ToolCall.id != exclude_call_id,
        ToolCall.run_id == run_id if run_id else ToolCall.run_id.is_(None),
    )
    total = db.execute(base).scalar_one()
    per_tool = db.execute(base.where(ToolCall.tool_name == tool_name)).scalar_one()
    return {"*": int(total), tool_name: int(per_tool)}
