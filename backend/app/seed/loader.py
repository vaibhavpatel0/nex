"""Idempotent seeding of tools, profiles, agents, policies, scenarios and integrations."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.tools.registry import TOOL_SPECS
from app.aegis.scenario_registry import seed_scenarios
from app.core.timeutil import new_id, utcnow
from app.models import Agent, AgentVersion, IntegrationConfig, PermissionProfile, Policy, PolicyVersion, ToolDefinition
from app.seed.catalog import AGENTS, POLICIES, PROFILES


def policy_snapshot_dict(p: Policy) -> dict:
    return {k: getattr(p, k) for k in ("key", "name", "description", "rule_type", "effect", "priority", "enabled",
                                       "locked", "applies_to", "params", "risk", "version")}


def agent_config_dict(a: Agent, profile_key: str) -> dict:
    return {k: getattr(a, k) for k in ("key", "name", "description", "agent_type", "behavior", "provider", "model_id",
                                       "system_prompt", "enabled_tools", "enabled", "revision")} | {"permission_profile": profile_key}


def seed_all(db: Session) -> dict:
    created = {"tools": 0, "profiles": 0, "agents": 0, "policies": 0, "scenarios": 0}
    for spec in TOOL_SPECS.values():
        row = db.get(ToolDefinition, spec.name)
        if row is None:
            row = ToolDefinition(name=spec.name)
            db.add(row)
            created["tools"] += 1
        row.description, row.operation, row.sensitivity = spec.description, spec.operation, spec.sensitivity
        row.resource_kind, row.args_schema = spec.resource_kind, spec.json_schema()
        row.sensitive_fields, row.simulated = list(spec.sensitive_fields), True

    profiles = {}
    for p in PROFILES:
        row = db.execute(select(PermissionProfile).where(PermissionProfile.key == p["key"])).scalar_one_or_none()
        if row is None:
            row = PermissionProfile(id=new_id("prf"), key=p["key"], name=p["name"], description=p["description"],
                                    grants=p["grants"], revision=1)
            db.add(row)
            created["profiles"] += 1
        profiles[p["key"]] = row
    db.flush()

    for a in AGENTS:
        if db.execute(select(Agent).where(Agent.key == a["key"])).scalar_one_or_none() is not None:
            continue
        row = Agent(
            id=new_id("agt"), key=a["key"], name=a["name"], description=a["description"], agent_type=a["agent_type"],
            behavior=a["behavior"], provider=a["provider"], model_id=a["model_id"], system_prompt=a.get("system_prompt"),
            enabled_tools=a["tools"], permission_profile_id=profiles[a["profile"]].id, revision=1, enabled=True,
            is_builtin=True,
        )
        db.add(row)
        db.flush()
        db.add(AgentVersion(id=new_id("agv"), agent_id=row.id, revision=1, snapshot=agent_config_dict(row, a["profile"]),
                            created_by="seed"))
        created["agents"] += 1

    for p in POLICIES:
        if db.execute(select(Policy).where(Policy.key == p["key"])).scalar_one_or_none() is not None:
            continue
        row = Policy(id=new_id("pol"), version=1, enabled=True, updated_by="seed", **p)
        db.add(row)
        db.flush()
        db.add(PolicyVersion(id=new_id("plv"), policy_id=row.id, version=1, snapshot=policy_snapshot_dict(row),
                             change_note="Initial built-in policy", changed_by="seed"))
        created["policies"] += 1

    created["scenarios"] = seed_scenarios(db)

    for key, name in (("ollama", "Ollama (local or cloud models)"), ("hosted", "OpenAI-compatible hosted provider"),
                      ("garak", "garak LLM vulnerability scanner"), ("ecommerce", "E-commerce agent platform")):
        if db.get(IntegrationConfig, key) is None:
            db.add(IntegrationConfig(key=key, name=name, enabled=key == "ollama", settings={}, status="unchecked", detail={}))
    db.flush()
    return created


def ensure_admin(db: Session, username: str, password: str) -> Optional[str]:
    from app.models import User
    from app.security.authentication import hash_password

    if db.execute(select(User).where(User.role == "admin")).first() is not None:
        return None
    u = User(id=new_id("usr"), username=username, password_hash=hash_password(password), role="admin", created_at=utcnow())
    db.add(u)
    db.flush()
    return u.id
