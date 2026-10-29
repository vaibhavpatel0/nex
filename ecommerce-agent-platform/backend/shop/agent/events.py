"""Agent run events for the Agent Workbench: persisted first, then pushed to WebSocket subscribers.

Clients reconnect with the last ``seq`` they processed and the server replays everything after it,
so a refresh or disconnect never loses part of a run.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select

from shop.db import iso, session_scope, utcnow
from shop.models import AgentEvent, AgentRun
from shop.redact import value as redact_value

STATES = ("RUNNING", "WAITING_FOR_MODEL", "TOOL_REQUESTED", "POLICY_CHECKING", "BLOCKED", "WAITING_FOR_APPROVAL",
          "TOOL_EXECUTING", "COMPLETED", "FAILED")


def to_dict(e: AgentEvent) -> Dict[str, Any]:
    return {"seq": e.seq, "event_id": e.event_id, "correlation_id": e.correlation_id, "type": e.event_type,
            "state": e.state, "tool": e.tool, "decision": e.decision, "execution_status": e.execution_status,
            "error_class": e.error_class, "payload": e.payload, "created_at": iso(e.created_at)}


class Subscriber:
    def __init__(self, customer_id: Optional[str]) -> None:
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=1000)
        self.customer_id = customer_id  # None = staff (sees everything)
        self.overflowed = False


class EventBus:
    def __init__(self) -> None:
        self._subs: Set[Subscriber] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()
        self._run_customer: Dict[str, Optional[str]] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def record(self, correlation_id: str, event_type: str, *, state: Optional[str] = None, tool: Optional[str] = None,
               decision: Optional[str] = None, execution_status: Optional[str] = None, error_class: Optional[str] = None,
               payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if state is not None and state not in STATES:
            raise ValueError(f"unknown state {state}")
        with self._lock, session_scope() as db:
            ev = AgentEvent(event_id="aev_" + uuid.uuid4().hex[:20], correlation_id=correlation_id, event_type=event_type,
                            state=state, tool=tool, decision=decision, execution_status=execution_status,
                            error_class=error_class, payload=redact_value(payload or {}), created_at=utcnow())
            db.add(ev)
            run = db.get(AgentRun, correlation_id)
            if run is not None and state is not None and run.status not in ("COMPLETED", "FAILED"):
                run.status = state
            customer = run.customer_id if run else None
            db.flush()
            data = to_dict(ev)
        self._run_customer[correlation_id] = customer
        self._dispatch(data, customer)
        return data

    def _dispatch(self, data: Dict[str, Any], customer: Optional[str]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(data, customer)
        else:
            loop.call_soon_threadsafe(self._fanout, data, customer)

    def _fanout(self, data: Dict[str, Any], customer: Optional[str]) -> None:
        for sub in list(self._subs):
            if sub.overflowed or (sub.customer_id is not None and sub.customer_id != customer):
                continue
            try:
                sub.queue.put_nowait(data)
            except asyncio.QueueFull:
                sub.overflowed = True

    def subscribe(self, customer_id: Optional[str]) -> Subscriber:
        s = Subscriber(customer_id)
        self._subs.add(s)
        return s

    def unsubscribe(self, s: Subscriber) -> None:
        self._subs.discard(s)

    @staticmethod
    def replay(since: int, customer_id: Optional[str], limit: int = 1000) -> List[Dict[str, Any]]:
        with session_scope() as db:
            stmt = select(AgentEvent).where(AgentEvent.seq > since).order_by(AgentEvent.seq).limit(limit)
            if customer_id is not None:
                ids = select(AgentRun.id).where(AgentRun.customer_id == customer_id, AgentRun.kind == "chat")
                stmt = stmt.where(AgentEvent.correlation_id.in_(ids))
            return [to_dict(e) for e in db.execute(stmt).scalars()]

    @staticmethod
    def latest() -> int:
        with session_scope() as db:
            return int(db.execute(select(AgentEvent.seq).order_by(AgentEvent.seq.desc()).limit(1)).scalar_one_or_none() or 0)


bus = EventBus()
