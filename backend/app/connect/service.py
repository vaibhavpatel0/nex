"""Connection lifecycle, state, audit, monitoring/protection evidence and test authorization.

States shown to users (computed, never stored as a single flag):
  pending       discovered; ownership not yet proven
  verified      domain control proven; the owner has not authorized access yet
  connected     authorized and healthy; neither monitoring nor protection enabled
  monitoring    AEGIS collects metadata, health, test observations and events the agent sends (no blocking)
  protected     SENTINEL enforcement observed: tools registered under the connection's namespace AND
                decisions recorded for this agent within the last 24 hours
  degraded      authorized but unhealthy, credential expired, or protection configured without recent evidence
  disconnected  the owner disconnected; credentials and the SENTINEL token are revoked
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connect import credentials as creds
from app.connect import protocols as P
from app.connect.discovery import discover
from app.connect.netguard import EgressError
from app.config.settings import get_settings
from app.core.timeutil import iso, new_id, utcnow
from app.models import (
    Agent, AgentConnection, AgentVersion, ConnectionChallenge, ConnectionCredential, ConnectionEvent,
    ConnectionTestScope, Finding, PermissionProfile, PolicyDecision, RuntimeEvent, ScenarioResult, ServiceToken,
    Scenario, TestRun, TestSuite, TestSuiteScenario, ToolDefinition, User,
)
from app.security.authentication import Principal, create_service_token
from app.websocket.event_bus import event_bus

CONNECTED_PROVIDERS = ("a2a", "custom_http")
INTERACTIVE = ("a2a", "custom_http")
PERMISSIONS = {
    "read_descriptor": "Read the agent's public descriptor (Agent Card, manifest, OpenAPI, MCP tool list)",
    "health_check": "Send one short connection-check message when the owner asks for a health check",
    "send_test_messages": "Send AEGIS test scenarios within an approved test scope",
    "receive_sentinel_events": "Accept execution events and tool-call decisions the agent sends to SENTINEL",
}
TESTABLE_SUITES = ("connected-agent",)
HEALTH_PROMPT = "AEGIS connection check. Reply with the single word OK."
SERVICE_SCOPES = ["sentinel:evaluate", "sentinel:ingest", "tools:register"]


class ConnectError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- rate limits (per user)
class _Limiter:
    def __init__(self) -> None:
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> None:
        now = time.monotonic()
        with self._lock:
            xs = [t for t in self._hits.get(key, []) if now - t < window]
            if len(xs) >= limit:
                raise ConnectError("Too many requests for this action. Wait a few minutes and try again.", 429)
            xs.append(now)
            self._hits[key] = xs

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = _Limiter()
LIMITS = {"discover": (20, 600), "verify": (30, 600), "network": (30, 600), "test": (10, 3600)}


def rate(p: Principal, action: str) -> None:
    n, w = LIMITS[action]
    limiter.hit(f"{p.user_id}:{action}", n, w)


# --------------------------------------------------------------------------- access (tenant isolation)
def visible_query(p: Principal):
    q = select(AgentConnection).order_by(AgentConnection.created_at.desc())
    if p.role != "admin":
        q = q.where(AgentConnection.owner_user_id == p.user_id)
    return q


def load(db: Session, conn_id: str, p: Principal, write: bool = False) -> AgentConnection:
    c = db.get(AgentConnection, conn_id)
    # Not-found and not-yours look the same, so connection IDs of other tenants are not disclosed.
    if c is None or (p.role != "admin" and c.owner_user_id != p.user_id):
        raise ConnectError("Connection not found", 404)
    if write and c.disconnected_at is not None:
        raise ConnectError("This connection was disconnected. Create a new connection to reconnect.", 409)
    return c


def audit(db: Session, c: AgentConnection, actor: str, action: str, detail: Optional[dict] = None, outcome: str = "ok") -> None:
    db.add(ConnectionEvent(connection_id=c.id, actor=actor, action=action, outcome=outcome, detail=detail or {},
                           created_at=utcnow()))
    c.updated_at = utcnow()
    # The live event is published after this transaction commits (the event bus writes in its own session;
    # publishing while this one holds the SQLite write lock would block).
    pending = db.info.setdefault("connection_events", [])
    pending.append(dict(event_type=f"connection.{action}",
                        summary=f"{c.name}: {action.replace('_', ' ')}" + ("" if outcome == "ok" else f" ({outcome})"),
                        severity="notice" if outcome == "ok" else "warning",
                        payload={"connection_id": c.id, "host": c.host, "outcome": outcome, **(detail or {})},
                        agent_id=c.agent_id))
    if not db.info.get("connection_events_hook"):
        db.info["connection_events_hook"] = True

        @sa_event.listens_for(db, "after_commit")
        def _publish(session) -> None:  # noqa: ANN001
            for ev in session.info.pop("connection_events", []):
                event_bus.publish(ev.pop("event_type"), ev.pop("summary"), **ev)

        @sa_event.listens_for(db, "after_rollback")
        def _drop(session) -> None:  # noqa: ANN001
            session.info.pop("connection_events", None)


# --------------------------------------------------------------------------- discovery
async def create(db: Session, p: Principal, url: str, hint: str, name: Optional[str]) -> AgentConnection:
    rate(p, "discover")
    try:
        d = await discover(url, hint)
    except EgressError as exc:
        raise ConnectError(exc.message, 422)
    c = AgentConnection(
        id=new_id("con"), owner_user_id=p.user_id, name=(name or d["identity"].get("name") or d["host"])[:128],
        submitted_url=d["submitted_url"], host=d["host"], protocol=d["protocol"], protocol_version=d.get("protocol_version"),
        interaction_endpoint=d.get("endpoint"), descriptor=d, capabilities=P.capability_matrix(d["protocol"], d),
        discovered_at=utcnow(), namespace="c" + secrets.token_hex(5), granted_permissions=[], created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(c)
    db.flush()
    audit(db, c, p.username, "discovered", {"protocol": c.protocol, "endpoint": c.interaction_endpoint,
                                            "warnings": len(d.get("warnings", []))})
    return c


async def rediscover(db: Session, p: Principal, c: AgentConnection) -> AgentConnection:
    rate(p, "discover")
    auth = {}
    if c.auth_status == "authorized" and c.protocol == "mcp":
        try:
            auth = await creds.headers_for(creds.active(db, c))
        except creds.CredentialError as exc:
            raise ConnectError(str(exc), 409)
    hint = c.protocol if c.protocol != "none" else "auto"
    try:
        d = await discover(c.submitted_url, hint, auth=auth or None)
    except EgressError as exc:
        audit(db, c, p.username, "rediscovered", {"error": exc.message}, outcome="failed")
        raise ConnectError(exc.message, 422)
    if c.protocol != "none" and d["protocol"] != c.protocol:
        audit(db, c, p.username, "rediscovered", {"error": f"protocol changed to {d['protocol']}"}, outcome="failed")
        raise ConnectError(f"The endpoint no longer presents {c.protocol} (found {d['protocol']}). Create a new connection.", 409)
    if c.verification_status == "verified" and d.get("endpoint") and d["endpoint"] != c.interaction_endpoint and c.interaction_endpoint:
        audit(db, c, p.username, "endpoint_changed", {"old": c.interaction_endpoint, "new": d["endpoint"]})
    c.protocol, c.protocol_version, c.interaction_endpoint = d["protocol"], d.get("protocol_version"), d.get("endpoint")
    c.descriptor, c.capabilities, c.discovered_at = d, P.capability_matrix(d["protocol"], d), utcnow()
    audit(db, c, p.username, "rediscovered", {"protocol": c.protocol, "tools": len(d.get("tools") or [])})
    return c


# --------------------------------------------------------------------------- verification
def expire_challenges(db: Session, c: AgentConnection) -> None:
    for ch in db.execute(select(ConnectionChallenge).where(ConnectionChallenge.connection_id == c.id,
                                                           ConnectionChallenge.status == "pending")).scalars():
        if ch.expires_at <= utcnow():
            ch.status = "expired"


async def check_challenge(db: Session, p: Principal, c: AgentConnection, ch: ConnectionChallenge) -> bool:
    from app.connect import verification as V

    rate(p, "verify")
    if ch.status != "pending":
        raise ConnectError(f"This challenge is {ch.status}", 409)
    try:
        ok, detail = await V.check(ch)
    except V.VerificationError as exc:
        ok, detail = False, str(exc)
    ch.checked_at = utcnow()
    ch.detail = detail
    if ch.expires_at <= utcnow():
        ch.status = "expired"
    if ok:
        ch.status = "verified"
        c.verification_status, c.verified_domain, c.verification_method = "verified", ch.domain, ch.method
        c.verified_at, c.verified_by = utcnow(), p.username
        audit(db, c, p.username, "ownership_verified", {"method": ch.method, "domain": ch.domain, "challenge": ch.id})
    else:
        audit(db, c, p.username, "verification_check", {"method": ch.method, "domain": ch.domain, "detail": detail}, outcome="failed")
    return ok


# --------------------------------------------------------------------------- authorization
def authorize(db: Session, p: Principal, c: AgentConnection, *, accept: bool, permissions: List[str], credential: Optional[dict]) -> None:
    if c.verification_status != "verified":
        raise ConnectError("Verify ownership of the domain before authorizing access", 409)
    if c.owner_user_id != p.user_id:
        raise ConnectError("Only the connection owner can authorize access to the agent", 403)
    if not accept:
        raise ConnectError("Authorization requires your explicit confirmation", 422)
    perms = sorted(set(permissions))
    bad = [x for x in perms if x not in PERMISSIONS]
    if bad or "read_descriptor" not in perms:
        raise ConnectError("Unknown permission or read_descriptor missing", 422)
    if credential:
        try:
            cred = creds.store(db, c, kind=credential.get("kind", ""), secret=credential.get("secret", ""),
                               header_name=credential.get("header_name"), token_url=credential.get("token_url"),
                               client_id=credential.get("client_id"), scopes=credential.get("scopes"),
                               expires_at=credential.get("expires_at"), actor=p.username)
        except creds.CredentialError as exc:
            raise ConnectError(str(exc), 422)
        cred_detail = {"credential": cred.kind, "fingerprint": cred.fingerprint}
    else:
        if (c.descriptor.get("auth") or {}).get("required"):
            raise ConnectError("The endpoint declares that it requires authentication. Provide a scoped credential.", 422)
        creds.revoke_all(db, c)
        cred_detail = {"credential": "none"}
    c.auth_status, c.authorized_at, c.authorized_by, c.granted_permissions = "authorized", utcnow(), p.username, perms
    audit(db, c, p.username, "access_authorized", {"permissions": perms, **cred_detail})


def revoke_credentials(db: Session, p: Principal, c: AgentConnection) -> int:
    n = creds.revoke_all(db, c)
    c.auth_status = "revoked"
    c.health_status = "unchecked"
    audit(db, c, p.username, "credentials_revoked", {"count": n})
    return n


async def _auth_headers(db: Session, c: AgentConnection) -> Dict[str, str]:
    cred = creds.active(db, c)
    if cred is not None and cred.expires_at is not None and cred.expires_at <= utcnow():
        c.auth_status = "expired"
    try:
        return await creds.headers_for(cred)
    except creds.CredentialError as exc:
        raise ConnectError(str(exc), 409)


# --------------------------------------------------------------------------- connect / health
def ensure_agent(db: Session, c: AgentConnection, owner: str) -> Agent:
    if c.agent_id:
        a = db.get(Agent, c.agent_id)
        if a is not None:
            a.enabled = True
            a.name = f"{c.name} (connected)"[:128]
            return a
    key = f"conn-{c.namespace}"
    prof = PermissionProfile(id=new_id("prf"), key=key, name=f"Connected agent {c.name}"[:128],
                             description=f"Grants for tools registered by {c.host} under namespace {c.namespace}. "
                                         "Nothing is granted until the owner allows it.",
                             grants={"tools": {}}, revision=1)
    db.add(prof)
    db.flush()
    a = Agent(id=new_id("agt"), key=key, name=f"{c.name} (connected)"[:128],
              description=(f"CONNECTED AGENT at {c.host} over {'A2A' if c.protocol == 'a2a' else 'the AEGIS agent API'}, "
                           f"owned by {owner}. AEGIS sends test messages only within the owner's approved test scope. "
                           "Tool calls appear only if the agent reports them to SENTINEL."),
              agent_type="remote_agent", behavior=None, provider=c.protocol, model_id=None, system_prompt=None,
              enabled_tools=[], permission_profile_id=prof.id, revision=1, enabled=True, is_builtin=False,
              created_at=utcnow(), updated_at=utcnow())
    db.add(a)
    db.flush()
    db.add(AgentVersion(id=new_id("agv"), agent_id=a.id, revision=1, created_by=owner,
                        snapshot={"key": key, "provider": c.protocol, "connection_id": c.id, "endpoint": c.interaction_endpoint}))
    c.agent_id = a.id
    return a


async def health(db: Session, p: Principal, c: AgentConnection) -> Dict[str, Any]:
    rate(p, "network")
    if c.auth_status != "authorized":
        raise ConnectError("Authorize access before checking health", 409)
    started = time.perf_counter()
    detail: str
    ok = False
    try:
        auth = await _auth_headers(db, c)
        if c.protocol in INTERACTIVE:
            if not c.interaction_endpoint:
                raise ConnectError("The descriptor has no interaction endpoint on this host", 409)
            if "health_check" not in (c.granted_permissions or []):
                d = await discover(c.submitted_url, c.protocol)
                ok, detail = d["protocol"] == c.protocol, "Descriptor reachable (no message sent: health_check not granted)"
            else:
                res = await send(c, auth, HEALTH_PROMPT, "health_" + secrets.token_hex(6), None)
                reply = (res.get("reply") or "").strip()
                ok = bool(reply)
                detail = f"Agent replied ({len(reply)} characters)" if ok else "The agent returned an empty reply"
        else:
            d = await discover(c.submitted_url, c.protocol, auth=auth or None)
            ok = d["protocol"] == c.protocol and (c.protocol != "mcp" or bool(d.get("tools_listed")))
            detail = "Descriptor reachable" + (f", {len(d.get('tools') or [])} tool(s) listed" if c.protocol == "mcp" else "")
    except EgressError as exc:
        detail = exc.message
    except ConnectError as exc:
        detail = exc.message
    ms = (time.perf_counter() - started) * 1000
    c.health_status = "healthy" if ok else "unhealthy"
    c.health_detail = f"{detail} in {ms:.0f} ms"
    c.last_health_at = utcnow()
    if ok:
        c.last_healthy_at = utcnow()
    audit(db, c, p.username, "health_check", {"healthy": ok, "detail": detail, "latency_ms": round(ms)},
          outcome="ok" if ok else "failed")
    return {"healthy": ok, "detail": c.health_detail}


async def connect(db: Session, p: Principal, c: AgentConnection) -> Dict[str, Any]:
    if c.verification_status != "verified" or c.auth_status != "authorized":
        raise ConnectError("The connection must be verified and authorized first", 409)
    if c.protocol == "none":
        raise ConnectError("No supported agent protocol was discovered at this URL", 409)
    owner = db.get(User, c.owner_user_id)
    if c.protocol in INTERACTIVE and c.interaction_endpoint:
        ensure_agent(db, c, owner.username if owner else p.username)
    if c.mode == "none":
        c.mode = "monitoring"
    audit(db, c, p.username, "connected", {"mode": c.mode, "agent_id": c.agent_id})
    db.commit()  # never hold the SQLite write lock across the network call below
    h = await health(db, p, c)
    return h


async def send(c: AgentConnection, auth: Dict[str, str], text: str, correlation_id: str,
               test: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    t = get_settings().connect_agent_timeout_seconds
    if c.protocol == "a2a":
        return await P.a2a_send(c.interaction_endpoint, text, auth, correlation_id, t)
    if c.protocol == "custom_http":
        return await P.custom_send(c.interaction_endpoint, text, auth, correlation_id, "aegis-" + correlation_id, test, t)
    raise ConnectError("This protocol does not support sending messages", 409)


# --------------------------------------------------------------------------- modes
def set_mode(db: Session, p: Principal, c: AgentConnection, mode: str) -> Optional[str]:
    """Returns a new SENTINEL service token (plaintext, shown once) when protection is enabled."""
    if c.auth_status != "authorized":
        raise ConnectError("Authorize access first", 409)
    if mode not in ("monitoring", "protected"):
        raise ConnectError("Mode must be monitoring or protected", 422)
    if c.owner_user_id != p.user_id and p.role != "admin":
        raise ConnectError("Only the owner can change the mode", 403)
    token = None
    if mode == "protected":
        if c.protocol not in INTERACTIVE and c.agent_id is None:
            # MCP/OpenAPI endpoints are tool providers; protection applies to the agent that calls them.
            owner = db.get(User, c.owner_user_id)
            ensure_agent(db, c, owner.username if owner else p.username)
        if c.agent_id is None:
            raise ConnectError("Connect the agent first", 409)
        agent = db.get(Agent, c.agent_id)
        token = create_service_token(db, f"conn-{c.namespace}", SERVICE_SCOPES, [agent.key], [c.namespace], p.username)
        c.service_token_id = db.execute(select(ServiceToken.id).where(ServiceToken.name == f"conn-{c.namespace}")).scalar_one()
        audit(db, c, p.username, "protection_enabled", {"agent_key": agent.key, "namespace": c.namespace,
                                                        "token_scopes": SERVICE_SCOPES})
    else:
        if c.service_token_id:
            tok = db.get(ServiceToken, c.service_token_id)
            if tok and tok.revoked_at is None:
                tok.revoked_at = utcnow()
        audit(db, c, p.username, "monitoring_only", {"note": "SENTINEL token revoked; tool calls are no longer enforced"})
    c.mode = mode
    return token


def disconnect(db: Session, p: Principal, c: AgentConnection) -> None:
    n = creds.revoke_all(db, c)
    if c.service_token_id:
        tok = db.get(ServiceToken, c.service_token_id)
        if tok and tok.revoked_at is None:
            tok.revoked_at = utcnow()
    for s in db.execute(select(ConnectionTestScope).where(ConnectionTestScope.connection_id == c.id,
                                                          ConnectionTestScope.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow()
    if c.agent_id:
        a = db.get(Agent, c.agent_id)
        if a:
            a.enabled = False
    c.mode, c.auth_status, c.disconnected_at, c.health_status = "none", "revoked", utcnow(), "unchecked"
    audit(db, c, p.username, "disconnected", {"credentials_revoked": n})


# --------------------------------------------------------------------------- permissions (tool grants)
def registered_tools(db: Session, c: AgentConnection) -> List[ToolDefinition]:
    return list(db.execute(select(ToolDefinition).where(ToolDefinition.provider == f"remote:{c.namespace}")
                           .order_by(ToolDefinition.name)).scalars())


def tool_grants(db: Session, c: AgentConnection) -> List[Dict[str, Any]]:
    a = db.get(Agent, c.agent_id) if c.agent_id else None
    grants = (a.permission_profile.grants or {}).get("tools", {}) if a else {}
    out = []
    for t in registered_tools(db, c):
        g = grants.get(t.name) if a and t.name in (a.enabled_tools or []) else None
        level = "deny" if g is None else ("approval" if g.get("require_approval") else "allow")
        out.append({"name": t.name, "description": t.description, "operation": t.operation, "sensitivity": t.sensitivity,
                    "level": level, "revision": t.revision})
    return out


def set_grants(db: Session, p: Principal, c: AgentConnection, levels: Dict[str, str]) -> None:
    if c.agent_id is None:
        raise ConnectError("Connect the agent first", 409)
    if c.owner_user_id != p.user_id and p.role != "admin":
        raise ConnectError("Only the owner can change permissions", 403)
    known = {t.name for t in registered_tools(db, c)}
    unknown = [k for k in levels if k not in known]
    if unknown:
        raise ConnectError("Not registered under this connection's namespace: " + ", ".join(unknown[:5]), 422)
    if any(v not in ("deny", "allow", "approval") for v in levels.values()):
        raise ConnectError("Level must be deny, allow or approval", 422)
    a = db.get(Agent, c.agent_id)
    prof = a.permission_profile
    tools = dict((prof.grants or {}).get("tools", {}))
    enabled = set(a.enabled_tools or [])
    for name, level in levels.items():
        if level == "deny":
            tools.pop(name, None)
            enabled.discard(name)
        else:
            tools[name] = {"require_approval": True} if level == "approval" else {}
            enabled.add(name)
    prof.grants = {"tools": tools}
    prof.revision += 1
    prof.updated_at = utcnow()
    a.enabled_tools = sorted(enabled)
    a.revision += 1
    a.updated_at = utcnow()
    audit(db, c, p.username, "permissions_changed", {"levels": levels, "profile_revision": prof.revision})


# --------------------------------------------------------------------------- test scopes
def approve_scope(db: Session, p: Principal, c: AgentConnection, *, suites: List[str], max_runs_per_day: int,
                  days: int, environment: str, confirm: bool) -> ConnectionTestScope:
    if c.owner_user_id != p.user_id:
        raise ConnectError("Only the connection owner can approve a test scope", 403)
    if c.auth_status != "authorized" or "send_test_messages" not in (c.granted_permissions or []):
        raise ConnectError("Authorize access with the 'send test messages' permission first", 409)
    if not confirm:
        raise ConnectError("Confirm that tests may run against this endpoint", 422)
    if environment not in ("test", "staging", "production_test_data"):
        raise ConnectError("Choose the environment the endpoint serves", 422)
    bad = [x for x in suites if x not in TESTABLE_SUITES]
    if not suites or bad:
        raise ConnectError("Only these suites can run against connected agents: " + ", ".join(TESTABLE_SUITES), 422)
    if not 1 <= max_runs_per_day <= 20 or not 1 <= days <= 30:
        raise ConnectError("Runs per day 1-20, validity 1-30 days", 422)
    for s in db.execute(select(ConnectionTestScope).where(ConnectionTestScope.connection_id == c.id,
                                                          ConnectionTestScope.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow()
    sc = ConnectionTestScope(id=new_id("tsc"), connection_id=c.id, suites=sorted(set(suites)), max_runs_per_day=max_runs_per_day,
                             environment=environment, approved_by=p.username, approved_at=utcnow(),
                             expires_at=utcnow() + timedelta(days=days))
    db.add(sc)
    db.flush()
    audit(db, c, p.username, "test_scope_approved", {"suites": sc.suites, "max_runs_per_day": max_runs_per_day,
                                                     "environment": environment, "days": days})
    return sc


def active_scope(db: Session, c: AgentConnection) -> Optional[ConnectionTestScope]:
    return db.execute(select(ConnectionTestScope).where(ConnectionTestScope.connection_id == c.id,
                                                        ConnectionTestScope.revoked_at.is_(None),
                                                        ConnectionTestScope.expires_at > utcnow())
                      .order_by(ConnectionTestScope.approved_at.desc())).scalars().first()


def authorize_test_run(db: Session, p: Principal, agent_id: str, suite_key: Optional[str], scenario_keys: Optional[List[str]]) -> None:
    """Called before any test run. Non-connected agents pass through unchanged."""
    a = db.get(Agent, agent_id)
    if a is None or a.provider not in CONNECTED_PROVIDERS:
        return
    c = db.execute(select(AgentConnection).where(AgentConnection.agent_id == a.id)).scalar_one_or_none()
    if c is None or c.disconnected_at is not None:
        raise ConnectError("This connected agent is disconnected", 409)
    if p.role != "admin" and c.owner_user_id != p.user_id:
        raise ConnectError("Only the owner of a connected agent (or an admin) can test it", 403)
    if c.verification_status != "verified" or c.auth_status != "authorized":
        raise ConnectError("The connection is not verified and authorized", 409)
    if "send_test_messages" not in (c.granted_permissions or []):
        raise ConnectError("The owner has not granted permission to send test messages", 403)
    scope = active_scope(db, c)
    if scope is None:
        raise ConnectError("No approved test scope. The owner must approve a test scope on the connection page.", 403)
    if suite_key:
        if suite_key not in scope.suites:
            raise ConnectError(f"Suite {suite_key} is outside the approved test scope ({', '.join(scope.suites)})", 403)
    else:
        rows = db.execute(select(Scenario.key, TestSuite.key).join(TestSuiteScenario, TestSuiteScenario.scenario_id == Scenario.id)
                          .join(TestSuite, TestSuite.id == TestSuiteScenario.suite_id)
                          .where(Scenario.key.in_(scenario_keys or []))).all()
        ok = {k for k, sk in rows if sk in scope.suites}
        outside = [k for k in (scenario_keys or []) if k not in ok]
        if outside:
            raise ConnectError("Scenarios outside the approved test scope: " + ", ".join(outside[:5]), 403)
    since = utcnow() - timedelta(days=1)
    n = db.execute(select(func.count()).select_from(TestRun).where(TestRun.agent_id == a.id, TestRun.created_at >= since)).scalar_one()
    if n >= scope.max_runs_per_day:
        raise ConnectError(f"The approved scope allows {scope.max_runs_per_day} run(s) per day; try again later", 429)
    rate(p, "test")
    audit(db, c, p.username, "test_authorized", {"suite": suite_key, "scenarios": len(scenario_keys or []), "scope": scope.id})


# --------------------------------------------------------------------------- views
def protection(db: Session, c: AgentConnection) -> Dict[str, Any]:
    tok = db.get(ServiceToken, c.service_token_id) if c.service_token_id else None
    tools = registered_tools(db, c)
    last = None
    n24 = 0
    if c.agent_id:
        last = db.execute(select(func.max(PolicyDecision.created_at)).where(PolicyDecision.agent_id == c.agent_id)).scalar_one()
        n24 = db.execute(select(func.count()).select_from(PolicyDecision).where(
            PolicyDecision.agent_id == c.agent_id, PolicyDecision.created_at >= utcnow() - timedelta(days=1))).scalar_one()
    token_ok = tok is not None and tok.revoked_at is None
    if c.mode != "protected":
        status, detail = "not_protected", ("Monitoring only: AEGIS observes but SENTINEL does not block anything for this agent"
                                           if c.mode == "monitoring" else "Not connected")
    elif not token_ok:
        status, detail = "degraded", "The SENTINEL service token was revoked"
    elif not tools:
        status, detail = "awaiting_evidence", "Token issued. Waiting for the agent to register its tools with SENTINEL (PUT /api/v1/sentinel/tools)."
    elif n24 == 0 and last is None:
        status, detail = "awaiting_evidence", "Tools registered. Waiting for the first SENTINEL decision (POST /api/v1/sentinel/evaluate)."
    elif n24 == 0:
        status, detail = "degraded", "No SENTINEL decisions in the last 24 hours; tool calls may be bypassing enforcement"
    else:
        status, detail = "protected", f"{n24} SENTINEL decision(s) in the last 24 hours"
    return {"status": status, "detail": detail, "token_active": token_ok,
            "token_last_used_at": iso(tok.last_used_at) if tok and tok.last_used_at else None,
            "registered_tools": len(tools), "decisions_24h": n24, "last_decision_at": iso(last) if last else None,
            "agent_key": f"conn-{c.namespace}", "namespace": c.namespace}


def display_state(c: AgentConnection, prot: Dict[str, Any], cred_expired: bool) -> str:
    if c.disconnected_at is not None:
        return "disconnected"
    if c.verification_status != "verified":
        return "pending"
    if c.auth_status != "authorized":
        return "degraded" if c.auth_status in ("expired",) else "verified"
    if cred_expired or c.health_status == "unhealthy" or prot["status"] == "degraded":
        return "degraded"
    if c.mode == "protected" and prot["status"] == "protected":
        return "protected"
    if c.mode in ("monitoring", "protected"):
        return "monitoring"
    return "connected"


def last_test(db: Session, c: AgentConnection) -> Optional[Dict[str, Any]]:
    if not c.agent_id:
        return None
    r = db.execute(select(TestRun).where(TestRun.agent_id == c.agent_id).order_by(TestRun.created_at.desc())).scalars().first()
    if r is None:
        return None
    return {"id": r.id, "status": r.status, "created_at": iso(r.created_at), "counts": (r.summary or {}).get("counts")}


def findings(db: Session, c: AgentConnection) -> Dict[str, Any]:
    if not c.agent_id:
        return {"open": 0, "by_severity": {}}
    rows = db.execute(select(Finding.severity, func.count()).join(TestRun, TestRun.id == Finding.run_id)
                      .where(TestRun.agent_id == c.agent_id, Finding.status == "open").group_by(Finding.severity)).all()
    return {"open": sum(n for _, n in rows), "by_severity": {s: n for s, n in rows}}


def monitoring(db: Session, c: AgentConnection) -> Dict[str, Any]:
    events = 0
    if c.agent_id:
        events = db.execute(select(func.count()).select_from(RuntimeEvent).where(
            RuntimeEvent.agent_id == c.agent_id, RuntimeEvent.created_at >= utcnow() - timedelta(days=1))).scalar_one()
    return {"active": c.mode in ("monitoring", "protected") and c.disconnected_at is None,
            "events_24h": events, "last_health_at": iso(c.last_health_at) if c.last_health_at else None,
            "note": "Monitoring records metadata, health checks, test observations and events the agent sends. It does not block anything."}


def out(db: Session, c: AgentConnection, full: bool = False) -> Dict[str, Any]:
    cred = creds.active(db, c)
    cv = creds.public_view(cred)
    prot = protection(db, c)
    owner = db.get(User, c.owner_user_id)
    d: Dict[str, Any] = {
        "id": c.id, "name": c.name, "owner": owner.username if owner else None, "host": c.host,
        "submitted_url": c.submitted_url, "protocol": c.protocol, "protocol_version": c.protocol_version,
        "endpoint": c.interaction_endpoint, "state": display_state(c, prot, bool(cv and cv["expired"])),
        "verification": {"status": c.verification_status, "domain": c.verified_domain, "method": c.verification_method,
                         "verified_at": iso(c.verified_at) if c.verified_at else None, "verified_by": c.verified_by},
        "authorization": {"status": c.auth_status, "authorized_at": iso(c.authorized_at) if c.authorized_at else None,
                          "authorized_by": c.authorized_by, "permissions": c.granted_permissions, "credential": cv},
        "health": {"status": c.health_status, "detail": c.health_detail,
                   "last_checked_at": iso(c.last_health_at) if c.last_health_at else None,
                   "last_healthy_at": iso(c.last_healthy_at) if c.last_healthy_at else None},
        "mode": c.mode, "monitoring": monitoring(db, c), "protection": prot,
        "agent_id": c.agent_id, "last_test": last_test(db, c), "findings": findings(db, c),
        "capabilities": c.capabilities, "tool_count": len(c.descriptor.get("tools") or []),
        "dev_exception": bool(c.descriptor.get("dev_exception")),
        "disconnected_at": iso(c.disconnected_at) if c.disconnected_at else None,
        "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
    }
    if full:
        expire_challenges(db, c)
        d["descriptor"] = c.descriptor
        d["challenges"] = [{"id": ch.id, "method": ch.method, "domain": ch.domain, "value": ch.value, "location": ch.location,
                            "status": ch.status, "detail": ch.detail, "expires_at": iso(ch.expires_at),
                            "created_by": ch.created_by, "checked_at": iso(ch.checked_at) if ch.checked_at else None}
                           for ch in db.execute(select(ConnectionChallenge).where(ConnectionChallenge.connection_id == c.id)
                                                .order_by(ConnectionChallenge.created_at.desc()).limit(10)).scalars()]
        sc = active_scope(db, c)
        d["test_scope"] = ({"id": sc.id, "suites": sc.suites, "max_runs_per_day": sc.max_runs_per_day,
                            "environment": sc.environment, "approved_by": sc.approved_by, "approved_at": iso(sc.approved_at),
                            "expires_at": iso(sc.expires_at)} if sc else None)
        d["tool_grants"] = tool_grants(db, c)
        d["permission_catalog"] = PERMISSIONS
        d["is_owner"] = None  # filled by the route
    return d


def agent_connection_status(db: Session, a: Agent) -> Dict[str, Any]:
    c = db.execute(select(AgentConnection).where(AgentConnection.agent_id == a.id)).scalar_one_or_none()
    if c is None:
        return {"state": "unavailable", "detail": "No connection record for this agent"}
    if c.disconnected_at is not None:
        return {"state": "disabled", "detail": "Disconnected by the owner"}
    if c.health_status == "healthy":
        return {"state": "ready", "detail": c.health_detail or "Healthy at last check",
                "checked_at": iso(c.last_health_at) if c.last_health_at else None}
    if c.health_status == "unhealthy":
        return {"state": "unavailable", "detail": c.health_detail or "Unhealthy at last check",
                "checked_at": iso(c.last_health_at) if c.last_health_at else None}
    return {"state": "unknown", "detail": "Not checked yet. Run a health check on the connection page."}
