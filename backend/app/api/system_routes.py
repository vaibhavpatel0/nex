from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config.settings import SOFTWARE_VERSION, get_settings
from app.core.timeutil import iso, utcnow
from app.database.base import get_db
from app.integrations.garak.status import garak_status
from app.integrations.ollama.client import OllamaProvider
from app.integrations.providers.openai_compatible import OpenAICompatibleProvider
from app.models import (
    Agent, ApprovalRequest, IntegrationConfig, PolicyDecision, RuntimeEvent, TestRun, ToolCall, Trace, model_to_dict,
)
from app.security.authentication import Principal
from app.security.authorization import require
from app.websocket.event_bus import event_bus

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unavailable",
            "version": SOFTWARE_VERSION, "time": iso(utcnow())}


# ------------------------------------------------------------------ integrations
def _models_needed(db: Session, provider: str) -> list:
    return sorted({a.model_id for a in db.execute(select(Agent).where(Agent.provider == provider)).scalars() if a.model_id})


@router.get("/integrations")
def list_integrations(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    s = get_settings()
    rows = {r.key: model_to_dict(r) for r in db.execute(select(IntegrationConfig)).scalars()}
    rows.setdefault("ollama", {"key": "ollama"})["config"] = {
        "base_url": s.ollama_base_url, "mode": "cloud" if s.ollama_base_url.startswith("https://ollama.com") else "local",
        "models_used_by_agents": _models_needed(db, "ollama")}
    rows.setdefault("hosted", {"key": "hosted"})["config"] = {"base_url": s.hosted_base_url or None,
                                                             "api_key_env": s.hosted_api_key_env,
                                                             "models_used_by_agents": _models_needed(db, "openai_compatible")}
    rows.setdefault("garak", {"key": "garak"})["config"] = garak_status()
    rows.setdefault("ecommerce", {"key": "ecommerce"})["config"] = {
        "base_url": s.ecommerce_base_url, "ui_url": s.ecommerce_ui_url, "token_configured": bool(s.ecommerce_token),
        "token_env": "AEGIS_ECOMMERCE_TOKEN"}
    return {"items": list(rows.values())}


@router.post("/integrations/{key}/check")
async def check_integration(key: str, p: Principal = Depends(require("tests:run")), db: Session = Depends(get_db)):
    ic = db.get(IntegrationConfig, key) if key in ("ollama", "hosted", "garak", "ecommerce") else None
    if ic is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    if key == "ollama":
        models = _models_needed(db, "ollama")
        detail = await OllamaProvider().status(models[0] if models else None)
        ic.status = "ok" if detail.get("reachable") else "unreachable"
    elif key == "hosted":
        models = _models_needed(db, "openai_compatible")
        detail = await OpenAICompatibleProvider().status(models[0] if models else None)
        ic.status = "ok" if detail.get("configured") and detail.get("api_key_present") else "not_configured"
    elif key == "ecommerce":
        from app.agents.remote_http import RemoteHTTPAgent
        s = get_settings()
        ok, msg = await RemoteHTTPAgent(s.ecommerce_base_url, s.ecommerce_token, 10).check_available()
        detail = {"base_url": s.ecommerce_base_url, "detail": msg, "token_configured": bool(s.ecommerce_token)}
        if not ok:
            detail["error"] = msg
        ic.status = "ok" if ok else "unreachable"
    else:
        detail = garak_status()
        ic.status = "installed" if detail["installed"] else "not_installed"
    ic.detail = detail
    ic.last_checked_at = utcnow()
    db.commit()  # release the write lock before the event bus writes
    event_bus.publish("integration.checked", f"{ic.name}: {ic.status}", payload={"integration": key, "status": ic.status})
    return model_to_dict(ic)


# ------------------------------------------------------------------ data retention
class PurgeIn(BaseModel):
    older_than_days: int = Field(ge=0, le=3650)
    confirm: str = Field(pattern=r"^DELETE$")


@router.get("/settings/data")
def data_info(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    s = get_settings()
    from sqlalchemy import func
    return {
        "database": s.resolved_database_url.split("///")[-1] if s.resolved_database_url.startswith("sqlite") else "configured",
        "counts": {
            "runtime_events": db.execute(select(func.count(RuntimeEvent.seq))).scalar_one(),
            "tool_calls": db.execute(select(func.count(ToolCall.id))).scalar_one(),
            "test_runs": db.execute(select(func.count(TestRun.id))).scalar_one(),
            "traces": db.execute(select(func.count(Trace.id))).scalar_one(),
        },
        "retention": "Data is kept until an admin purges it. Purging removes test runs, traces, tool calls, decisions, "
                     "approvals and events older than the chosen age. Reports and investigations attached to purged runs are removed too.",
        "approval_ttl_seconds": s.approval_ttl_seconds,
        "session_ttl_minutes": s.session_ttl_minutes,
    }


@router.post("/settings/data/purge")
def purge(body: PurgeIn, p: Principal = Depends(require("data:delete")), db: Session = Depends(get_db)):
    cutoff = utcnow() - timedelta(days=body.older_than_days)
    runs = [r for (r,) in db.execute(select(TestRun.id).where(TestRun.created_at <= cutoff,
                                                               TestRun.status.notin_(("queued", "running"))))]
    removed = {"test_runs": len(runs)}
    for model, col in ((PolicyDecision, PolicyDecision.created_at), (ApprovalRequest, ApprovalRequest.created_at)):
        removed[model.__tablename__] = db.execute(delete(model).where(col <= cutoff)).rowcount or 0
    removed["tool_calls"] = db.execute(delete(ToolCall).where(ToolCall.created_at <= cutoff)).rowcount or 0
    removed["traces"] = db.execute(delete(Trace).where(Trace.started_at <= cutoff, Trace.status != "running")).rowcount or 0
    if runs:
        for rid in runs:
            db.execute(text("UPDATE test_runs SET replay_of_run_id = NULL WHERE replay_of_run_id = :r"), {"r": rid})
            db.execute(delete(TestRun).where(TestRun.id == rid))
    removed["runtime_events"] = db.execute(delete(RuntimeEvent).where(RuntimeEvent.created_at <= cutoff)).rowcount or 0
    db.flush()
    db.commit()  # release the write lock before the event bus writes
    event_bus.publish("system.data_purged", f"Data older than {body.older_than_days} day(s) purged by {p.username}",
                      severity="notice", payload=removed)
    return {"removed": removed}
