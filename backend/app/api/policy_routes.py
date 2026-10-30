"""Policy administration. Every change creates a new policy version with author and note."""
from __future__ import annotations

from app.database.base import CommitBeforeResponseRoute

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.timeutil import new_id, utcnow
from app.database.base import get_db
from app.models import Policy, PolicyVersion, model_to_dict
from app.security.audit import load_policy_views
from app.security.authentication import Principal
from app.security.authorization import require
from app.security.policy_engine import RULES, compute_policy_hash
from app.seed.loader import policy_snapshot_dict
from app.websocket.event_bus import event_bus

router = APIRouter(route_class=CommitBeforeResponseRoute, tags=["policies"])

RULE_DOCS = {
    "unknown_tool": "Triggers when the requested tool is not registered.",
    "tool_not_granted": "Triggers when the tool is not enabled on the agent or not granted by its profile.",
    "invalid_arguments": "Triggers when arguments fail the tool's strict schema.",
    "path_scope": "Params: deny_globs. Triggers when a path escapes the store, matches a deny glob or is outside granted paths.",
    "customer_scope": "Triggers when a record's owner differs from the session customer.",
    "db_table_scope": "Params: restricted_tables. Triggers for restricted tables or tables/columns outside the grant.",
    "recipient_allowlist": "Params: always_allowed_domains. Triggers for recipient domains outside the grant.",
    "attachment_restriction": "Params: blocked_classifications. Triggers for disallowed or confidential attachments.",
    "sensitive_egress": "Params: fields. Triggers when outbound content contains credentials, card numbers, tax IDs or markers.",
    "suspicious_sequence": "Params: prior_classifications, internal_domains. Triggers on an external send after a sensitive read.",
    "approval_required": "Params: when (always | external_recipients | grant_flag), internal_domains.",
    "rate_limit": "Params: max_calls, window_seconds, scope (session | agent), per_tool.",
    "failure_limit": "Params: max_consecutive_failures.",
    "url_allowlist": "Params: allowed_schemes, allowed_hosts. Always blocks private, loopback, link-local and IP-literal hosts.",
    "granted_by_profile": "The ALLOW source: triggers when the tool is granted.",
    "execution_timeout": "Params: timeout_seconds. CONSTRAIN effect only.",
}


def policy_out(p: Policy) -> dict:
    d = model_to_dict(p)
    d["rule_doc"] = RULE_DOCS.get(p.rule_type, "")
    return d


@router.get("/policies")
def list_policies(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    rows = db.execute(select(Policy).order_by(Policy.priority.desc(), Policy.key)).scalars().all()
    return {"items": [policy_out(r) for r in rows], "policy_hash": compute_policy_hash(load_policy_views(db)),
            "rule_types": [{"rule_type": k, "doc": RULE_DOCS.get(k, "")} for k in RULES],
            "precedence": ["BLOCK overrides everything (deny overrides)", "REQUIRE_APPROVAL", "ALLOW",
                           "Default: BLOCK when nothing allows the action", "Evaluation errors are treated as BLOCK"]}


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    pol = get_or_404(db, Policy, policy_id, "Policy")
    versions = db.execute(select(PolicyVersion).where(PolicyVersion.policy_id == pol.id).order_by(PolicyVersion.version.desc())).scalars()
    return {**policy_out(pol), "versions": [model_to_dict(v) for v in versions]}


def _validate_params(rule_type: str, params: Dict[str, Any]) -> None:
    import json
    if len(json.dumps(params)) > 8000:
        raise HTTPException(status_code=422, detail="params too large")
    numeric = {"max_calls", "window_seconds", "max_consecutive_failures", "timeout_seconds"}
    for k in numeric & set(params):
        if not isinstance(params[k], (int, float)) or params[k] <= 0 or params[k] > 100000:
            raise HTTPException(status_code=422, detail=f"{k} must be a positive number")
    if rule_type == "execution_timeout" and float(params.get("timeout_seconds", 1)) > 120:
        raise HTTPException(status_code=422, detail="timeout_seconds is limited to 120")
    for k, v in params.items():
        if isinstance(v, list) and not all(isinstance(x, str) and len(x) <= 200 for x in v):
            raise HTTPException(status_code=422, detail=f"{k} must be a list of strings")


class PolicyPatch(BaseModel):
    enabled: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=1, le=10000)
    params: Optional[Dict[str, Any]] = None
    applies_to: Optional[List[str]] = Field(default=None, min_length=1, max_length=32)
    description: Optional[str] = Field(default=None, max_length=2000)
    risk: Optional[str] = Field(default=None, pattern=r"^(low|medium|high|critical)$")
    change_note: str = Field(min_length=3, max_length=500)


class PolicyIn(BaseModel):
    key: str = Field(pattern=r"^CUS-\d{3}$", description="Custom policies use keys CUS-001 to CUS-999")
    name: str = Field(min_length=3, max_length=128)
    description: str = Field(min_length=3, max_length=2000)
    rule_type: str
    effect: str = Field(pattern=r"^(BLOCK|REQUIRE_APPROVAL|ALLOW|CONSTRAIN)$")
    priority: int = Field(ge=1, le=10000)
    applies_to: List[str] = Field(min_length=1, max_length=32)
    params: Dict[str, Any] = Field(default_factory=dict)
    risk: str = Field(default="medium", pattern=r"^(low|medium|high|critical)$")

    @field_validator("rule_type")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in RULES:
            raise ValueError("unknown rule_type")
        return v


def _version(db: Session, pol: Policy, who: str, note: str) -> None:
    db.add(PolicyVersion(id=new_id("plv"), policy_id=pol.id, version=pol.version, snapshot=policy_snapshot_dict(pol),
                         change_note=note, changed_by=who))


@router.patch("/policies/{policy_id}")
def patch_policy(policy_id: str, body: PolicyPatch, p: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)):
    pol = get_or_404(db, Policy, policy_id, "Policy")
    changes = body.model_dump(exclude_unset=True, exclude={"change_note"})
    if pol.locked and (changes.get("enabled") is False or "applies_to" in changes or "params" in changes):
        raise HTTPException(status_code=403, detail="Locked core policies cannot be disabled or re-scoped")
    if "params" in changes:
        _validate_params(pol.rule_type, changes["params"])
    if pol.effect == "ALLOW" and "applies_to" in changes:
        pass  # allowed: an ALLOW can never override a BLOCK
    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")
    for k, v in changes.items():
        setattr(pol, k, v)
    pol.version += 1
    pol.updated_at = utcnow()
    pol.updated_by = p.username
    _version(db, pol, p.username, body.change_note)
    db.flush()
    db.commit()  # release the write lock before the event bus writes
    event_bus.publish("policy.changed", f"{pol.key} changed by {p.username} (v{pol.version})", severity="notice",
                      payload={"policy": pol.key, "version": pol.version, "fields": sorted(changes), "note": body.change_note})
    return policy_out(pol)


@router.post("/policies", status_code=201)
def create_policy(body: PolicyIn, p: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)):
    if db.execute(select(Policy).where(Policy.key == body.key)).first():
        raise HTTPException(status_code=409, detail="Policy key already exists")
    if body.effect == "CONSTRAIN" and body.rule_type != "execution_timeout":
        raise HTTPException(status_code=422, detail="Only execution_timeout policies may use CONSTRAIN")
    if body.rule_type == "granted_by_profile" and body.effect != "ALLOW":
        raise HTTPException(status_code=422, detail="granted_by_profile must use ALLOW")
    _validate_params(body.rule_type, body.params)
    pol = Policy(id=new_id("pol"), version=1, enabled=True, locked=False, updated_by=p.username, **body.model_dump())
    db.add(pol)
    db.flush()
    _version(db, pol, p.username, "Created")
    db.commit()  # release the write lock before the event bus writes
    event_bus.publish("policy.changed", f"{pol.key} created by {p.username}", severity="notice", payload={"policy": pol.key})
    return policy_out(pol)
