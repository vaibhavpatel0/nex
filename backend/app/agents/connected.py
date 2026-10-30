"""Adapter for agents connected through the Agent Connection Center (A2A or the AEGIS agent API).

AEGIS sends the scenario input as a message to the agent's verified endpoint, with the owner's scoped
credential, and records the reply. The agent's own tool calls are visible to AEGIS only when the agent
reports them to SENTINEL under the correlation ID sent with the message (``aegis_correlation_id`` in A2A
metadata, ``correlation_id`` in the AEGIS agent API). Tool calls the agent merely mentions in its reply are
recorded as agent-reported events, never as enforced decisions.

The adapter refuses to send anything unless the connection is verified, authorized and not disconnected,
and the owner granted ``send_test_messages``. Test-scope approval is checked before the run is created.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select

from app.agents.base import AgentAdapter, AgentFailure, AgentOutput, AgentTask, AgentUnavailable, EmitFn, ToolClient
from app.connect import credentials as creds
from app.connect.netguard import EgressError
from app.database.base import db_session
from app.models import Agent, AgentConnection

UNAVAILABLE = {"timeout", "unreachable", "dns_failed", "dns_timeout", "tls_invalid", "auth_rejected", "rate_limited",
               "address_not_allowed", "https_required", "redirect_refused"}


class ConnectedAgent(AgentAdapter):
    kind = "connected"

    def __init__(self, agent_key: str) -> None:
        self.agent_key = agent_key

    def _load(self) -> Dict[str, Any]:
        with db_session() as db:
            a = db.execute(select(Agent).where(Agent.key == self.agent_key)).scalar_one_or_none()
            c = db.execute(select(AgentConnection).where(AgentConnection.agent_id == a.id)).scalar_one_or_none() if a else None
            if c is None:
                raise AgentUnavailable("No connection record for this agent")
            if c.disconnected_at is not None:
                raise AgentUnavailable("The owner disconnected this agent")
            if c.verification_status != "verified" or c.auth_status != "authorized":
                raise AgentUnavailable("The connection is not verified and authorized")
            if "send_test_messages" not in (c.granted_permissions or []):
                raise AgentUnavailable("The owner has not granted permission to send test messages")
            if not c.interaction_endpoint:
                raise AgentUnavailable("The connection has no interaction endpoint")
            db.expunge(c)
            cred = creds.active(db, c)
            if cred is not None:
                db.expunge(cred)
            return {"conn": c, "cred": cred}

    async def check_available(self) -> "tuple[bool, str]":
        try:
            info = self._load()
        except AgentUnavailable as exc:
            return False, str(exc)
        return True, f"connected to {info['conn'].host}"

    async def run(self, task: AgentTask, tools: ToolClient, emit: EmitFn) -> AgentOutput:
        from app.connect.service import ConnectError, send

        info = self._load()
        c = info["conn"]
        try:
            auth = await creds.headers_for(info["cred"])
        except creds.CredentialError as exc:
            raise AgentUnavailable(str(exc)) from exc
        emit("agent.status", f"Sent scenario input to {c.host} ({c.protocol})",
             {"remote": c.interaction_endpoint, "protocol": c.protocol, "correlation_id": task.correlation_id})
        test = {"run_id": task.run_id, "scenario_key": task.scenario_key} if task.run_id else None
        try:
            res = await send(c, auth, task.task, task.correlation_id or "", test)
        except EgressError as exc:
            if exc.code in UNAVAILABLE:
                raise AgentUnavailable(exc.message) from exc
            raise AgentFailure(exc.message) from exc
        except ConnectError as exc:
            raise AgentFailure(exc.message) from exc
        if res.get("tool_calls"):
            emit("agent.reported_tool_calls",
                 f"The agent reported {len(res['tool_calls'])} tool call(s) in its reply (agent-reported, not enforced by SENTINEL)",
                 {"tool_calls": res["tool_calls"], "source": "agent reply"})
        return AgentOutput(final_text=res.get("reply"), usage=res.get("usage"), steps=1,
                           model={"provider": c.protocol, "endpoint_host": c.host, **(res.get("meta") or {})})
