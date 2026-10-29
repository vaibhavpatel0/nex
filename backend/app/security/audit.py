"""Audit helpers: loading policy views and persisting decision records."""
from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, Policy
from app.security.policy_engine import AgentView, PolicyView, compute_policy_hash


def load_policy_views(db: Session) -> List[PolicyView]:
    rows = db.execute(select(Policy)).scalars().all()
    return [
        PolicyView(
            key=p.key, name=p.name, rule_type=p.rule_type, effect=p.effect, priority=p.priority,
            enabled=p.enabled, applies_to=tuple(p.applies_to or []), params=dict(p.params or {}),
            risk=p.risk, version=p.version,
        )
        for p in rows
    ]


def policy_snapshot(db: Session) -> dict:
    views = load_policy_views(db)
    return {
        "hash": compute_policy_hash(views),
        "policies": {v.key: {"version": v.version, "enabled": v.enabled} for v in sorted(views, key=lambda x: x.key)},
    }


def agent_view(agent: Agent) -> AgentView:
    grants = agent.permission_profile.grants if agent.permission_profile else {}
    return AgentView(
        id=agent.id, key=agent.key, enabled_tools=tuple(agent.enabled_tools or []),
        grants=dict(grants or {}), revision=agent.revision,
    )
