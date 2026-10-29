from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.tools.registry import TOOL_SPECS
from app.api.common import get_or_404
from app.core.timeutil import iso, new_id, utcnow
from app.database.base import get_db
from app.models import Agent, AgentVersion, IntegrationConfig, PermissionProfile, RuntimeEvent, TestRun, ToolDefinition, model_to_dict
from app.security.authentication import Principal
from app.security.authorization import require
from app.seed.loader import agent_config_dict

router = APIRouter(tags=["agents"])

TYPES = {"simulated": "deterministic", "local_model": "ollama", "hosted_model": "openai_compatible"}


def connection_status(db: Session, a: Agent) -> dict:
    if not a.enabled:
        return {"state": "disabled", "detail": "Agent is disabled"}
    if a.agent_type == "simulated":
        return {"state": "ready", "detail": "Rule-based simulated agent (no language model, no external dependency)"}
    key = {"ollama": "ollama", "ecommerce_http": "ecommerce"}.get(a.provider, "hosted")
    ic = db.get(IntegrationConfig, key)
    if ic is None or ic.status == "unchecked":
        return {"state": "unknown", "detail": "Integration not checked yet. Open Integrations and run a check."}
    models = ic.detail.get("models", []) if isinstance(ic.detail, dict) else []
    if ic.status != "ok":
        return {"state": "unavailable", "detail": ic.detail.get("error") or ic.detail.get("detail") or ic.status,
                "checked_at": iso(ic.last_checked_at)}
    if key == "ecommerce":
        return {"state": "ready", "detail": ic.detail.get("detail", "E-commerce backend reachable at last check"),
                "checked_at": iso(ic.last_checked_at)}
    if key == "ollama" and a.model_id and a.model_id not in models and f"{a.model_id}:latest" not in models \
            and not a.model_id.endswith(":cloud"):
        return {"state": "unavailable", "detail": f"Model {a.model_id} is not installed (ollama pull {a.model_id})",
                "checked_at": iso(ic.last_checked_at)}
    return {"state": "ready", "detail": "Integration reachable at last check", "checked_at": iso(ic.last_checked_at)}


def agent_out(db: Session, a: Agent) -> dict:
    last_run = db.execute(select(TestRun).where(TestRun.agent_id == a.id).order_by(TestRun.created_at.desc()).limit(1)).scalar_one_or_none()
    last_ev = db.execute(select(RuntimeEvent).where(RuntimeEvent.agent_id == a.id).order_by(RuntimeEvent.seq.desc()).limit(1)).scalar_one_or_none()
    d = model_to_dict(a)
    d["permission_profile"] = {"id": a.permission_profile.id, "key": a.permission_profile.key, "name": a.permission_profile.name}
    d["connection"] = connection_status(db, a)
    d["label"] = {"simulated": "Simulated (rule-based)", "local_model": "Local model", "hosted_model": "External hosted model",
                  "remote_agent": "Remote agent (e-commerce platform)"}.get(a.agent_type, a.agent_type)
    d["last_run"] = {"id": last_run.id, "status": last_run.status, "created_at": iso(last_run.created_at),
                     "counts": (last_run.summary or {}).get("counts")} if last_run else None
    d["last_event"] = {"seq": last_ev.seq, "event_type": last_ev.event_type, "summary": last_ev.summary,
                       "created_at": iso(last_ev.created_at)} if last_ev else None
    return d


@router.get("/agents")
def list_agents(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    return {"items": [agent_out(db, a) for a in db.execute(select(Agent).order_by(Agent.is_builtin.desc(), Agent.created_at)).scalars()]}


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    a = get_or_404(db, Agent, agent_id, "Agent")
    out = agent_out(db, a)
    out["versions"] = [model_to_dict(v) for v in db.execute(
        select(AgentVersion).where(AgentVersion.agent_id == a.id).order_by(AgentVersion.revision.desc())).scalars()]
    return out


class AgentIn(BaseModel):
    key: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    agent_type: str = Field(pattern=r"^(simulated|local_model|hosted_model)$")
    behavior: Optional[str] = Field(default=None, pattern=r"^(naive|guarded)$")
    model_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    system_prompt: Optional[str] = Field(default=None, max_length=8000)
    enabled_tools: List[str] = Field(default_factory=list, max_length=32)
    permission_profile_key: str = Field(min_length=1, max_length=64)

    @field_validator("enabled_tools")
    @classmethod
    def _tools_known(cls, v: List[str]) -> List[str]:
        unknown = [t for t in v if t not in TOOL_SPECS]
        if unknown:
            raise ValueError("unknown tools: " + ", ".join(unknown))
        return sorted(set(v))


class AgentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    behavior: Optional[str] = Field(default=None, pattern=r"^(naive|guarded)$")
    model_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    system_prompt: Optional[str] = Field(default=None, max_length=8000)
    enabled_tools: Optional[List[str]] = Field(default=None, max_length=32)
    permission_profile_key: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("enabled_tools")
    @classmethod
    def _tools_known(cls, v):
        if v is None:
            return v
        unknown = [t for t in v if t not in TOOL_SPECS]
        if unknown:
            raise ValueError("unknown tools: " + ", ".join(unknown))
        return sorted(set(v))


def _profile(db: Session, key: str) -> PermissionProfile:
    prof = db.execute(select(PermissionProfile).where(PermissionProfile.key == key)).scalar_one_or_none()
    if prof is None:
        raise HTTPException(status_code=422, detail="Unknown permission profile")
    return prof


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    if db.execute(select(Agent).where(Agent.key == body.key)).first():
        raise HTTPException(status_code=409, detail="Agent key already exists")
    if body.agent_type == "simulated" and not body.behavior:
        raise HTTPException(status_code=422, detail="Simulated agents need a behavior (naive or guarded)")
    if body.agent_type != "simulated" and not body.model_id:
        raise HTTPException(status_code=422, detail="Model agents need a model identifier")
    prof = _profile(db, body.permission_profile_key)
    a = Agent(id=new_id("agt"), key=body.key, name=body.name, description=body.description, agent_type=body.agent_type,
              behavior=body.behavior if body.agent_type == "simulated" else None, provider=TYPES[body.agent_type],
              model_id=body.model_id if body.agent_type != "simulated" else None, system_prompt=body.system_prompt,
              enabled_tools=body.enabled_tools, permission_profile_id=prof.id, revision=1, is_builtin=False)
    db.add(a)
    db.flush()
    db.add(AgentVersion(id=new_id("agv"), agent_id=a.id, revision=1, snapshot=agent_config_dict(a, prof.key), created_by=p.username))
    db.flush()
    db.refresh(a)
    return agent_out(db, a)


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: str, body: AgentPatch, p: Principal = Depends(require("agents:write")), db: Session = Depends(get_db)):
    a = get_or_404(db, Agent, agent_id, "Agent")
    changes = body.model_dump(exclude_unset=True)
    if "permission_profile_key" in changes:
        a.permission_profile_id = _profile(db, changes.pop("permission_profile_key")).id
    if a.agent_type == "simulated":
        changes.pop("model_id", None)
    for k, v in changes.items():
        setattr(a, k, v)
    a.revision += 1
    a.updated_at = utcnow()
    db.flush()
    db.refresh(a)
    db.add(AgentVersion(id=new_id("agv"), agent_id=a.id, revision=a.revision,
                        snapshot=agent_config_dict(a, a.permission_profile.key), created_by=p.username))
    return agent_out(db, a)


@router.get("/tools")
def list_tools(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    return {"items": [model_to_dict(t) for t in db.execute(select(ToolDefinition).order_by(ToolDefinition.name)).scalars()]}


@router.get("/permission-profiles")
def list_profiles(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    rows = db.execute(select(PermissionProfile).order_by(PermissionProfile.key)).scalars().all()
    usage = dict(db.execute(select(Agent.permission_profile_id, func.count(Agent.id)).group_by(Agent.permission_profile_id)).all())
    return {"items": [{**model_to_dict(r), "agents": usage.get(r.id, 0)} for r in rows]}
