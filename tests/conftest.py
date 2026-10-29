"""Shared fixtures. Each test session uses a fresh data directory and database,
initialised through the real Alembic migrations and seed loader."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_TMP = tempfile.mkdtemp(prefix="aegis-test-")
os.environ["AEGIS_DATA_DIR"] = _TMP
os.environ.pop("AEGIS_DATABASE_URL", None)
os.environ["AEGIS_SIMULATED_TIMEOUT_DELAY"] = "1.0"
os.environ["AEGIS_APPROVAL_TTL_SECONDS"] = "600"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:9"  # nothing listens here: Ollama is "unavailable"
os.environ["AEGIS_CORS_ORIGINS"] = "http://localhost:5173"

from app.config.settings import get_settings, reset_settings_cache  # noqa: E402

reset_settings_cache()

PASSWORDS = {"admin": "admin-password-123", "operator": "operator-password-123", "viewer": "viewer-password-123"}


def _init() -> None:
    from sqlalchemy import update

    from app.cli import migrate
    from app.core.timeutil import new_id
    from app.database.base import db_session, init_engine
    from app.models import Policy, User
    from app.security.authentication import hash_password
    from app.seed.loader import seed_all

    s = get_settings()
    migrate(s.resolved_database_url)
    init_engine(s.resolved_database_url)
    with db_session() as db:
        seed_all(db)
        for role, pw in PASSWORDS.items():
            db.add(User(id=new_id("usr"), username=role, password_hash=hash_password(pw), role=role))
        # Shorter execution timeout keeps timeout tests fast (simulated upstream delay is 1.0 s).
        db.execute(update(Policy).where(Policy.key == "SEN-060").values(params={"timeout_seconds": 0.3}))


_init()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def initial_overview(client):
    """Overview captured before any test generates activity (empty-database check)."""
    from app.database.base import db_session
    from app.models import PolicyDecision, TestRun

    with db_session() as db:
        assert db.query(TestRun).count() == 0 and db.query(PolicyDecision).count() == 0
    return client.get("/api/v1/overview", headers=auth(client, "viewer")).json()


_TOKENS: dict = {}


def auth(client, role: str) -> dict:
    if role not in _TOKENS:
        r = client.post("/api/v1/auth/login", json={"username": role, "password": PASSWORDS[role]})
        assert r.status_code == 200, r.text
        _TOKENS[role] = r.json()["token"]
    return {"Authorization": f"Bearer {_TOKENS[role]}"}


def agent_id(client, key: str) -> str:
    items = client.get("/api/v1/agents", headers=auth(client, "viewer")).json()["items"]
    return next(a["id"] for a in items if a["key"] == key)


def wait_run(client, run_id: str, timeout: float = 120) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/test-runs/{run_id}", headers=auth(client, "viewer")).json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.2)
    raise AssertionError("run did not finish")
