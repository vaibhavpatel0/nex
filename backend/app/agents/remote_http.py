"""Remote agent adapter: drives an agent that runs inside another application over HTTP.

Used for the e-commerce shopping agent. AEGIS sends the scenario input to the application's
internal test endpoint; the application runs its real agent against a sandbox copy of its
fixtures, sends every tool call to SENTINEL under the same correlation ID, and returns the final
answer plus evidence (side effects in the sandbox, which tool implementations were invoked).

AEGIS evaluates from SENTINEL's own records (tool calls, decisions). The evidence returned here is
used only for the side-effect and invocation checks, and is labelled as reported by the application.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.agents.base import AgentAdapter, AgentFailure, AgentOutput, AgentTask, AgentUnavailable, EmitFn, ToolClient


class RemoteHTTPAgent(AgentAdapter):
    kind = "remote_http"

    def __init__(self, base_url: str, token: str, timeout: float, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._transport = transport

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or self.timeout, transport=self._transport,
                                 headers={"Authorization": f"Bearer {self.token}"})

    async def check_available(self) -> "tuple[bool, str]":
        if not self.token:
            return False, "AEGIS_ECOMMERCE_TOKEN is not set (run `make link-ecommerce`)"
        try:
            async with self._client(5) as c:
                r = await c.get("/api/internal/health")
        except httpx.HTTPError:
            return False, f"E-commerce backend not reachable at {self.base_url}"
        if r.status_code != 200:
            return False, f"E-commerce backend answered HTTP {r.status_code}"
        body = r.json()
        return True, f"ready (model {body.get('model', {}).get('model', 'unknown')})"

    async def run(self, task: AgentTask, tools: ToolClient, emit: EmitFn) -> AgentOutput:
        if not self.token:
            raise AgentUnavailable("AEGIS_ECOMMERCE_TOKEN is not set (run `make link-ecommerce`)")
        payload: Dict[str, Any] = {
            "correlation_id": task.correlation_id, "run_id": task.run_id, "scenario_key": task.scenario_key,
            "message": task.task, "customer_id": task.session_subject, "faults": task.faults,
            "model_fault": task.model_fault, "direct_tool_call": task.direct_tool_call,
        }
        emit("agent.status", "Sent scenario input to the e-commerce backend", {"remote": self.base_url,
                                                                                "direct_tool_call": bool(task.direct_tool_call)})
        try:
            async with self._client() as c:
                r = await c.post("/api/internal/agent-runs", json=payload)
        except httpx.ConnectError as exc:
            raise AgentUnavailable(f"E-commerce backend not reachable at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise AgentUnavailable("E-commerce backend did not answer before the timeout") from exc
        if r.status_code in (401, 403):
            raise AgentUnavailable("E-commerce backend rejected the AEGIS token (run `make link-ecommerce`)")
        if r.status_code >= 400:
            raise AgentFailure(f"E-commerce backend returned HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise AgentFailure("E-commerce backend returned invalid JSON") from exc
        return AgentOutput(
            final_text=body.get("final_text"),
            usage=body.get("usage"),
            steps=int(body.get("steps") or 0),
            error_kind=body.get("error_kind"),
            error=body.get("error"),
            side_effects=body.get("side_effects"),
            invoked_tools=body.get("invoked_tools"),
            invariants=body.get("invariants"),
            model=body.get("model"),
        )
