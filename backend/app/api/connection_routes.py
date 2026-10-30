"""Agent Connection Center API.

Every route is authenticated. Connections are isolated per owner: non-admin users only ever see their own
(another owner's connection answers 404). Secrets are accepted in request bodies only and never returned.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.connect import service as S
from app.connect import verification as V
from app.connect.discovery import HINTS
from app.core.timeutil import iso
from app.database.base import CommitBeforeResponseRoute, get_db
from app.models import AgentConnection, ConnectionChallenge, ConnectionEvent
from app.security.authentication import Principal
from app.security.authorization import require

router = APIRouter(route_class=CommitBeforeResponseRoute, tags=["connections"])


def _err(exc: S.ConnectError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _get(db: Session, cid: str, p: Principal, write: bool = False) -> AgentConnection:
    try:
        return S.load(db, cid, p, write)
    except S.ConnectError as exc:
        raise _err(exc)


def _full(db: Session, c: AgentConnection, p: Principal, commit: bool = False) -> dict:
    if commit:
        # Commit here, in the thread that made the changes, so the SQLite write lock is never held while
        # this request waits for the event loop (background test runs write from the loop thread).
        db.commit()
    d = S.out(db, c, full=True)
    d["is_owner"] = c.owner_user_id == p.user_id
    return d


@router.get("/connect/meta")
def meta(p: Principal = Depends(require("read"))):
    s = get_settings()
    return {
        "protocols": [{"key": h, "name": {"auto": "Detect automatically", "a2a": "A2A Agent Card", "mcp": "MCP remote server",
                                          "openapi": "OpenAPI specification", "custom_http": "AEGIS agent API (custom HTTP)"}[h]}
                      for h in HINTS],
        "verification_methods": V.METHODS,
        "permissions": S.PERMISSIONS,
        "testable_suites": list(S.TESTABLE_SUITES),
        "credential_kinds": [
            {"key": "none", "name": "No credential (endpoint is public)"},
            {"key": "bearer", "name": "Scoped bearer token"},
            {"key": "header", "name": "Scoped token in a custom header"},
            {"key": "oauth2_client_credentials", "name": "OAuth 2.0 client credentials (token endpoint on the agent's host)"},
        ],
        "dev_exceptions": {"private_hosts": s.connect_private_allowlist, "http_hosts": s.connect_http_allowlist},
    }


class CreateIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    protocol: Literal["auto", "a2a", "mcp", "openapi", "custom_http"] = "auto"
    name: Optional[str] = Field(default=None, max_length=128)


@router.get("/connections")
def list_connections(state: Optional[str] = None, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    items = [S.out(db, c) for c in db.execute(S.visible_query(p)).scalars()]
    if state:
        items = [i for i in items if i["state"] == state]
    return {"items": items}


@router.post("/connections", status_code=201)
async def create(body: CreateIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    try:
        c = await S.create(db, p, body.url, body.protocol, body.name)
    except S.ConnectError as exc:
        raise _err(exc)
    return _full(db, c, p, commit=True)


@router.get("/connections/{cid}")
def get_one(cid: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    return _full(db, _get(db, cid, p), p)


@router.get("/connections/{cid}/events")
def events(cid: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    c = _get(db, cid, p)
    rows = db.execute(select(ConnectionEvent).where(ConnectionEvent.connection_id == c.id)
                      .order_by(ConnectionEvent.id.desc()).limit(200)).scalars()
    return {"items": [{"id": e.id, "actor": e.actor, "action": e.action, "outcome": e.outcome, "detail": e.detail,
                       "created_at": iso(e.created_at)} for e in rows]}


@router.post("/connections/{cid}/discover")
async def rediscover(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        await S.rediscover(db, p, c)
    except S.ConnectError as exc:
        raise _err(exc)
    return _full(db, c, p, commit=True)


class ChallengeIn(BaseModel):
    method: Literal["dns_txt", "https_file"]
    domain: Optional[str] = Field(default=None, max_length=255)


@router.post("/connections/{cid}/challenges", status_code=201)
def new_challenge(cid: str, body: ChallengeIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    if c.owner_user_id != p.user_id:
        raise HTTPException(status_code=403, detail="Only the connection owner can verify ownership")
    try:
        ch = V.issue(db, c, body.method, body.domain or c.host, p.username)
    except V.VerificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    S.audit(db, c, p.username, "challenge_issued", {"method": ch.method, "domain": ch.domain, "challenge": ch.id})
    return _full(db, c, p, commit=True)


@router.post("/connections/{cid}/challenges/{chid}/check")
async def check_challenge(cid: str, chid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    if c.owner_user_id != p.user_id:
        raise HTTPException(status_code=403, detail="Only the connection owner can verify ownership")
    ch = db.get(ConnectionChallenge, chid)
    if ch is None or ch.connection_id != c.id:
        raise HTTPException(status_code=404, detail="Challenge not found")
    try:
        ok = await S.check_challenge(db, p, c, ch)
    except S.ConnectError as exc:
        raise _err(exc)
    return {"verified": ok, "detail": ch.detail, "connection": _full(db, c, p, commit=True)}


class CredentialIn(BaseModel):
    kind: Literal["bearer", "header", "oauth2_client_credentials"]
    secret: str = Field(min_length=8, max_length=4096)
    header_name: Optional[str] = Field(default=None, max_length=64)
    token_url: Optional[str] = Field(default=None, max_length=2048)
    client_id: Optional[str] = Field(default=None, max_length=200)
    scopes: Optional[List[str]] = Field(default=None, max_length=10)
    expires_at: Optional[datetime] = None


class AuthorizeIn(BaseModel):
    accept: bool
    permissions: List[str] = Field(min_length=1, max_length=10)
    credential: Optional[CredentialIn] = None


@router.post("/connections/{cid}/authorize")
def authorize(cid: str, body: AuthorizeIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        S.authorize(db, p, c, accept=body.accept, permissions=body.permissions,
                    credential=body.credential.model_dump() if body.credential else None)
    except S.ConnectError as exc:
        raise _err(exc)
    return _full(db, c, p, commit=True)


@router.post("/connections/{cid}/revoke-credentials")
def revoke(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    n = S.revoke_credentials(db, p, c)
    return {"revoked": n, "connection": _full(db, c, p, commit=True)}


@router.post("/connections/{cid}/connect")
async def connect(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        h = await S.connect(db, p, c)
    except S.ConnectError as exc:
        raise _err(exc)
    return {"health": h, "connection": _full(db, c, p, commit=True)}


@router.post("/connections/{cid}/health")
async def health(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        h = await S.health(db, p, c)
    except S.ConnectError as exc:
        raise _err(exc)
    return {"health": h, "connection": _full(db, c, p, commit=True)}


class ModeIn(BaseModel):
    mode: Literal["monitoring", "protected"]


@router.post("/connections/{cid}/mode")
def set_mode(cid: str, body: ModeIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        token = S.set_mode(db, p, c, body.mode)
    except S.ConnectError as exc:
        raise _err(exc)
    out = {"connection": _full(db, c, p, commit=True)}
    if token:
        out["sentinel"] = {
            "token": token,
            "note": "Shown once. Store it in the agent's backend secret store, never in frontend code or Git.",
            "agent_key": f"conn-{c.namespace}", "tool_namespace": c.namespace,
            "base_url": "<this AEGIS server>/api/v1/sentinel",
            "steps": [
                f"PUT /api/v1/sentinel/tools with namespace '{c.namespace}' and tools named '{c.namespace}.<tool>'",
                f"POST /api/v1/sentinel/sessions with agent_key 'conn-{c.namespace}' and the correlation_id AEGIS sent (or a new one)",
                "POST /api/v1/sentinel/evaluate before every tool call; execute only when the response says execute=true",
                "POST /api/v1/sentinel/tool-calls/{id}/result after execution",
            ],
        }
    return out


class GrantsIn(BaseModel):
    levels: Dict[str, Literal["deny", "allow", "approval"]] = Field(max_length=200)


@router.put("/connections/{cid}/permissions")
def set_permissions(cid: str, body: GrantsIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        S.set_grants(db, p, c, dict(body.levels))
    except S.ConnectError as exc:
        raise _err(exc)
    return _full(db, c, p, commit=True)


class ScopeIn(BaseModel):
    suites: List[str] = Field(min_length=1, max_length=5)
    max_runs_per_day: int = Field(default=3, ge=1, le=20)
    days: int = Field(default=7, ge=1, le=30)
    environment: Literal["test", "staging", "production_test_data"]
    confirm: bool


@router.post("/connections/{cid}/test-scope")
def approve_scope(cid: str, body: ScopeIn, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    try:
        S.approve_scope(db, p, c, suites=body.suites, max_runs_per_day=body.max_runs_per_day, days=body.days,
                        environment=body.environment, confirm=body.confirm)
    except S.ConnectError as exc:
        raise _err(exc)
    return _full(db, c, p, commit=True)


@router.post("/connections/{cid}/test-scope/revoke")
def revoke_scope(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    from app.core.timeutil import utcnow
    from app.models import ConnectionTestScope

    c = _get(db, cid, p, write=True)
    n = 0
    for s in db.execute(select(ConnectionTestScope).where(ConnectionTestScope.connection_id == c.id,
                                                          ConnectionTestScope.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow()
        n += 1
    S.audit(db, c, p.username, "test_scope_revoked", {"count": n})
    return _full(db, c, p, commit=True)


class TestIn(BaseModel):
    mode: Literal["protected", "baseline"] = "protected"
    suite_key: str = "connected-agent"


@router.post("/connections/{cid}/tests", status_code=202)
async def start_test(cid: str, body: TestIn, p: Principal = Depends(require("tests:run")), db: Session = Depends(get_db)):
    from app.aegis.scenario_runner import runner

    c = _get(db, cid, p, write=True)
    if not c.agent_id:
        raise HTTPException(status_code=409, detail="Connect the agent first")
    try:
        S.authorize_test_run(db, p, c.agent_id, body.suite_key, None)
    except S.ConnectError as exc:
        raise _err(exc)
    db.commit()
    try:
        run_id = runner.create_run(agent_id=c.agent_id, suite_key=body.suite_key, scenario_keys=None,
                                   label=f"Connected agent test: {c.name}"[:160], triggered_by=p.username, mode=body.mode)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    runner.start(run_id)
    return {"run_id": run_id, "status": "queued"}


@router.post("/connections/{cid}/disconnect")
def disconnect(cid: str, p: Principal = Depends(require("connections:manage")), db: Session = Depends(get_db)):
    c = _get(db, cid, p, write=True)
    S.disconnect(db, p, c)
    return _full(db, c, p, commit=True)
