"""Database engine and session management.

SQLite is used for the local prototype. All access goes through SQLAlchemy 2.0
ORM sessions and Alembic migrations, so a PostgreSQL URL can be configured later
without changing the application code (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import List

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from typing import Iterator, Optional

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def init_engine(url: str) -> Engine:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialised")
    return _engine


def session_factory() -> sessionmaker:
    if _SessionLocal is None:
        raise RuntimeError("Database engine is not initialised")
    return _SessionLocal


@contextmanager
def db_session() -> Iterator[Session]:
    session: Session = session_factory()()
    _track(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session: Session = session_factory()()
    _track(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def schema_is_current() -> bool:
    """Return True when the migration-managed tables exist."""
    insp = inspect(get_engine())
    # agent_connections arrived in migration 0003; an older database must be upgraded with `make init`.
    return insp.has_table("alembic_version") and insp.has_table("agents") and insp.has_table("agent_connections")


# --- commit before the response leaves -------------------------------------------------------------
# FastAPI may run the teardown of `yield` dependencies after the response has been sent. A client
# could then use a token or read a record before its transaction is committed (a real race seen in
# tests). Routes built with CommitBeforeResponseRoute commit the request's sessions first.
_request_sessions: "ContextVar[Optional[List[Session]]]" = ContextVar("request_sessions", default=None)


def _track(session: Session) -> None:
    pending = _request_sessions.get()
    if pending is not None:
        pending.append(session)


def spawn_detached(coro):
    """Start a background task that does not belong to the current request.

    asyncio tasks copy the creating context. A task started inside a request would otherwise inherit the
    request's session-tracking list, and CommitBeforeResponseRoute would commit the task's live sessions
    from a worker thread while the task is still using them (seen as a stuck SQLite write lock).
    """
    import asyncio

    async def _run():
        _request_sessions.set(None)
        return await coro

    return asyncio.get_running_loop().create_task(_run())


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
