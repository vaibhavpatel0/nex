"""WebSocket live event stream.

Protocol (JSON text frames):
  client -> {"type": "auth", "token": "...", "since_seq": 123}   must be the first frame, within 10 s
  server -> {"type": "ready", "latest_seq": N}
  server -> {"type": "event", "event": {...}}                    replayed (seq > since_seq), then live
  server -> {"type": "replay_truncated"}                          more than 500 missed; client should refetch via REST
  server -> {"type": "ping"}                                      every 20 s; client may answer {"type": "pong"}
  server -> {"type": "overflow"}                                  client was too slow; reconnect with last seq
The token is sent in a frame rather than the URL so it does not appear in access logs.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database.base import db_session
from app.security.authentication import resolve_token
from app.security.authorization import has_permission
from app.websocket.event_bus import event_bus

router = APIRouter()
REPLAY_LIMIT = 500


@router.websocket("/api/v1/ws")
async def events_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await ws.close(code=4401)
        return
    if not isinstance(msg, dict) or msg.get("type") != "auth":
        await ws.close(code=4401)
        return
    with db_session() as db:
        principal = resolve_token(db, str(msg.get("token", ""))[:256])
    if principal is None or not has_permission(principal, "read"):
        await ws.send_text(json.dumps({"type": "error", "detail": "unauthorized"}))
        await ws.close(code=4401)
        return
    try:
        since = max(0, int(msg.get("since_seq") or 0))
    except (TypeError, ValueError):
        since = 0

    sub = event_bus.subscribe()  # subscribe before replay so nothing falls in the gap
    try:
        latest = event_bus.latest_seq()
        await ws.send_text(json.dumps({"type": "ready", "latest_seq": latest}))
        sent_max = since
        if since and since < latest:
            missed = event_bus.replay(since, REPLAY_LIMIT + 1)
            if len(missed) > REPLAY_LIMIT:
                await ws.send_text(json.dumps({"type": "replay_truncated"}))
                missed = missed[-REPLAY_LIMIT:]
            for ev in missed:
                await ws.send_text(json.dumps({"type": "event", "event": ev, "replayed": True}))
                sent_max = max(sent_max, ev["seq"])
        elif not since:
            sent_max = latest

        async def reader() -> None:
            while True:
                await ws.receive_text()  # pongs / ignored; raises on disconnect

        reader_task = asyncio.create_task(reader())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(sub.queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    await ws.send_text(json.dumps({"type": "ping"}))
                    if reader_task.done():
                        break
                    continue
                if item.get("type") == "event":
                    if item["event"]["seq"] <= sent_max:  # de-duplicate against replay
                        continue
                    sent_max = item["event"]["seq"]
                await ws.send_text(json.dumps(item))
                if item.get("type") == "overflow":
                    await ws.close(code=4408)
                    break
                if reader_task.done():
                    break
        finally:
            reader_task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        event_bus.unsubscribe(sub)
