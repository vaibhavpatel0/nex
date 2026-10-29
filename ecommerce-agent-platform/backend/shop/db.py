"""Database engines. The live store database plus disposable sandbox databases for security tests."""
from __future__ import annotations
from contextvars import ContextVar
from typing import List

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import DateTime, TypeDecorator, create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return as_utc(dt).isoformat().replace("+00:00", "Z") if dt else None


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        return as_utc(value) if value is not None else None

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        return as_utc(value) if value is not None else None


def _pragmas(conn, _record):  # type: ignore[no-untyped-def]
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(url: str, wal: bool = True) -> Engine:
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(eng, "connect", _pragmas)
        if wal:
            event.listen(eng, "connect", lambda c, r: c.execute("PRAGMA journal_mode=WAL"))
    return eng


_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def init_engine(url: str) -> Engine:
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database not initialised")
    return _engine


@contextmanager
def session_scope(factory: Optional[sessionmaker] = None) -> Iterator[Session]:
    f = factory or _factory
    if f is None:
        raise RuntimeError("database not initialised")
    s: Session = f()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    with session_scope() as s:
        _track(s)
        yield s


def schema_ready() -> bool:
    insp = inspect(engine())
    return insp.has_table("alembic_version") and insp.has_table("products")


# --- commit before the response leaves -------------------------------------------------------------
# FastAPI may run the teardown of `yield` dependencies after the response has been sent. A client
# could then use a token or read a record before its transaction is committed (a real race seen in
# tests). Routes built with CommitBeforeResponseRoute commit the request's sessions first.
_request_sessions: "ContextVar[Optional[List[Session]]]" = ContextVar("request_sessions", default=None)


def _track(session: Session) -> None:
    pending = _request_sessions.get()
    if pending is not None:
        pending.append(session)


class CommitBeforeResponseRoute(APIRoute):
    def get_route_handler(self):  # type: ignore[override]
        handler = super().get_route_handler()

        async def wrapped(request):
            pending: List[Session] = []
            token = _request_sessions.set(pending)
            try:
                response = await handler(request)
                for s in pending:
                    if s.is_active:
                        await run_in_threadpool(s.commit)
                return response
            finally:
                _request_sessions.reset(token)

        return wrapped
