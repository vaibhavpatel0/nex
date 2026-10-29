"""Builds execution traces from persisted events, tool calls and policy decisions.

The timeline is ordered by event sequence. Each tool call is shown with a clear
distinction between *attempted* (requested) and *executed* (reached the tool).
Stages with no recorded data are listed in ``missing`` instead of being filled in.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import iso
from app.models import PolicyDecision, RuntimeEvent, ScenarioResult, ToolCall, Trace, model_to_dict
from app.websocket.event_bus import event_to_dict

STAGES = [
    ("request", "Request received", ("agent.request",)),
    ("model", "Model response", ("model.response",)),
    ("tool_request", "Tool call requested", ("tool.requested",)),
    ("decision", "Policy decision", ("policy.decision",)),
    ("execution", "Tool execution or block", ("tool.executed", "tool.blocked", "tool.failed", "approval.requested")),
    ("result", "Final result", ("agent.output", "agent.error")),
    ("evaluation", "Evaluation", ("scenario.completed",)),
]


def tool_calls_for(db: Session, correlation_id: str) -> List[ToolCall]:
    return list(db.execute(
        select(ToolCall).where(ToolCall.correlation_id == correlation_id).order_by(ToolCall.created_at, ToolCall.id)
    ).scalars())


def build_trace(db: Session, correlation_id: str) -> Optional[Dict[str, Any]]:
    tr = db.get(Trace, correlation_id)
    if tr is None:
        return None
    events = list(db.execute(
        select(RuntimeEvent).where(RuntimeEvent.correlation_id == correlation_id).order_by(RuntimeEvent.seq)
    ).scalars())
    calls = tool_calls_for(db, correlation_id)
    decisions = {d.tool_call_id: d for d in db.execute(
        select(PolicyDecision).where(PolicyDecision.correlation_id == correlation_id)
    ).scalars()}
    result = None
    if tr.run_id:
        result = db.execute(
            select(ScenarioResult).where(ScenarioResult.correlation_id == correlation_id)
        ).scalar_one_or_none()

    present = {e.event_type for e in events}
    stages = []
    missing = []
    for key, label, types in STAGES:
        hit = any(t in present for t in types)
        if key == "evaluation" and tr.kind != "test":
            stages.append({"key": key, "label": label, "status": "not_applicable"})
            continue
        if key in ("tool_request", "decision", "execution") and not calls:
            stages.append({"key": key, "label": label, "status": "none", "note": "The agent made no tool calls"})
            continue
        stages.append({"key": key, "label": label, "status": "recorded" if hit else "missing"})
        if not hit:
            missing.append(label)

    call_rows = []
    for c in calls:
        d = decisions.get(c.id)
        call_rows.append({
            **model_to_dict(c),
            "attempted": True,
            "executed": c.status in ("executed", "failed"),
            "decision_record": model_to_dict(d) if d else None,
        })

    return {
        "trace": {**model_to_dict(tr)},
        "stages": stages,
        "missing": missing,
        "events": [event_to_dict(e) for e in events],
        "tool_calls": call_rows,
        "evaluation": model_to_dict(result) if result else None,
        "usage_note": None if tr.usage else "Token usage not reported by this agent type",
        "generated_at": iso(None),
    }
