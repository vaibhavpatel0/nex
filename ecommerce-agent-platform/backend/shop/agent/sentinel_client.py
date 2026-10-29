"""Client for the SENTINEL decision API in AEGIS (see aegis-ai backend/app/api/sentinel_routes.py).

Fail-closed: if SENTINEL is not configured, unreachable, slow, or answers with an error, the
caller must treat the tool call as BLOCKED. This module never fabricates a decision.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from shop.config import get_settings

log = logging.getLogger("shop.sentinel")


class SentinelUnavailable(Exception):
    pass


class SentinelClient:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._transport = transport

    @property
    def configured(self) -> bool:
        s = get_settings()
        return bool(s.aegis_base_url and s.aegis_api_token)

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        s = get_settings()
        return httpx.AsyncClient(base_url=s.aegis_base_url.rstrip("/") + "/api/v1/sentinel",
                                 timeout=timeout or s.sentinel_timeout_seconds, transport=self._transport,
                                 headers={"Authorization": f"Bearer {s.aegis_api_token}"})

    async def _call(self, method: str, path: str, body: Any = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        if not self.configured:
            raise SentinelUnavailable("SENTINEL is not configured (AEGIS_API_TOKEN missing; run `make link-ecommerce` in aegis-ai)")
        try:
            async with self._client(timeout) as c:
                r = await c.request(method, path, json=body)
        except httpx.TimeoutException as exc:
            raise SentinelUnavailable("SENTINEL did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise SentinelUnavailable(f"SENTINEL not reachable at {get_settings().aegis_base_url}") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise SentinelUnavailable(f"SENTINEL returned HTTP {r.status_code}" + (f": {detail}" if detail else ""))
        return r.json()

    # -------------------------------------------------------------- enforcement path
    async def register_tools(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call("PUT", "/tools", payload)

    async def open_session(self, correlation_id: str, task: str, customer_id: Optional[str], conversation_id: Optional[str]) -> Dict[str, Any]:
        return await self._call("POST", "/sessions", {"correlation_id": correlation_id, "agent_key": get_settings().aegis_agent_key,
                                                     "task": task[:4000], "session_subject": customer_id,
                                                     "conversation_id": conversation_id})

    async def event(self, correlation_id: str, event_type: str, summary: str, payload: Dict[str, Any], severity: str = "info") -> None:
        try:
            await self._call("POST", f"/sessions/{correlation_id}/events",
                             {"event_type": event_type, "summary": summary[:400], "severity": severity, "payload": payload}, timeout=4)
        except SentinelUnavailable as exc:  # model telemetry is best effort; decisions are not
            log.warning("could not forward %s to SENTINEL: %s", event_type, exc)

    async def evaluate(self, correlation_id: str, tool: str, args: Any, resource: Dict[str, Any], customer_id: Optional[str],
                       approval_id: Optional[str] = None, origin: str = "user_task") -> Dict[str, Any]:
        return await self._call("POST", "/evaluate", {
            "correlation_id": correlation_id, "agent_key": get_settings().aegis_agent_key, "tool": tool, "args": args,
            "resource": resource, "session_subject": customer_id, "approval_id": approval_id, "origin": origin})

    async def report(self, tool_call_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return await self._call("POST", f"/tool-calls/{tool_call_id}/result", body)

    async def complete(self, correlation_id: str, body: Dict[str, Any]) -> None:
        try:
            await self._call("POST", f"/sessions/{correlation_id}/complete", body, timeout=6)
        except SentinelUnavailable as exc:
            log.warning("could not complete SENTINEL session: %s", exc)

    # -------------------------------------------------------------- workbench views
    async def evaluation(self, correlation_id: str) -> Dict[str, Any]:
        return await self._call("GET", f"/sessions/{correlation_id}/evaluation")

    async def runs(self) -> Dict[str, Any]:
        return await self._call("GET", "/test-runs")

    async def run(self, run_id: str) -> Dict[str, Any]:
        return await self._call("GET", f"/test-runs/{run_id}")

    async def start_run(self, suite_key: Optional[str], scenario_keys: Optional[List[str]], mode: str, label: str) -> Dict[str, Any]:
        return await self._call("POST", "/test-runs", {"agent_key": get_settings().aegis_agent_key, "suite_key": suite_key,
                                                      "scenario_keys": scenario_keys, "mode": mode, "label": label}, timeout=20)

    async def scenarios(self) -> Dict[str, Any]:
        return await self._call("GET", "/scenarios")

    async def approvals(self, status: Optional[str]) -> Dict[str, Any]:
        return await self._call("GET", "/approvals" + (f"?status={status}" if status else ""))

    async def decide(self, approval_id: str, approve: bool, note: str, on_behalf_of: str) -> Dict[str, Any]:
        return await self._call("POST", f"/approvals/{approval_id}/decide",
                                {"approve": approve, "note": note, "on_behalf_of": on_behalf_of})

    async def policy(self, key: str) -> Dict[str, Any]:
        return await self._call("GET", f"/policies/{key}")


sentinel = SentinelClient()
