"""Persist-then-publish event bus.

Every event is written to ``runtime_events`` first (assigning a monotonically increasing
``seq``) and only then pushed to connected WebSocket subscribers. Clients that miss events
(disconnects, slow consumers, backend restarts) recover them by reconnecting with the last
``seq`` they processed; the server replays persisted events after that point.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select

from app.core.timeutil import iso, new_id, utcnow
from app.database.base import db_session
from app.models import RuntimeEvent
from app.security.redaction import redact_value

log = logging.getLogger("aegis.events")

SEVERITIES = ("info", "notice", "warning", "critical", "error")
QUEUE_SIZE = 500


def event_to_dict(e: RuntimeEvent) -> Dict[str, Any]:
    return {
        "seq": e.seq,
        "event_id": e.event_id,
        "correlation_id": e.correlation_id,
        "run_id": e.run_id,
        "agent_id": e.agent_id,
        "event_type": e.event_type,
        "severity": e.severity,
        "summary": e.summary,
        "payload": e.payload,
        "created_at": iso(e.created_at),
    }


class Subscriber:
    def __init__(self) -> None:
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.overflowed = False


class EventBus:
    def __init__(self) -> None:
        self._subs: Set[Subscriber] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ------------------------------------------------------------------ publish
    def publish(
        self,
        event_type: str,
        summary: str,
        *,
        severity: str = "info",
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if severity not in SEVERITIES:
            severity = "info"
        with self._lock, db_session() as db:
            ev = RuntimeEvent(
                event_id=new_id("evt"),
                correlation_id=correlation_id,
                run_id=run_id,
                agent_id=agent_id,
                event_type=event_type,
                severity=severity,
                summary=summary[:500],
                payload=redact_value(payload or {}),
                created_at=utcnow(),
            )
            db.add(ev)
            db.flush()
            data = event_to_dict(ev)
        self._dispatch(data)
        return data

    def _dispatch(self, data: Dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(data)
        elif not loop.is_closed():
            loop.call_soon_threadsafe(self._fanout, data)

    def _fanout(self, data: Dict[str, Any]) -> None:
        for sub in list(self._subs):
            if sub.overflowed:
                continue
            try:
                sub.queue.put_nowait({"type": "event", "event": data})
            except asyncio.QueueFull:
                # Drop the slow client; it will reconnect and replay from its last seq.
                sub.overflowed = True
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait({"type": "overflow"})
                except Exception:
                    pass

    # ------------------------------------------------------------------ subscriptions
    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    @staticmethod
    def replay(since_seq: int, limit: int = 500) -> List[Dict[str, Any]]:
        with db_session() as db:
            rows = db.execute(
                select(RuntimeEvent).where(RuntimeEvent.seq > since_seq).order_by(RuntimeEvent.seq).limit(limit)
            ).scalars().all()
            return [event_to_dict(r) for r in rows]

    @staticmethod
    def latest_seq() -> int:
        with db_session() as db:
            row = db.execute(select(RuntimeEvent.seq).order_by(RuntimeEvent.seq.desc()).limit(1)).scalar_one_or_none()
            return int(row or 0)


event_bus = EventBus()
