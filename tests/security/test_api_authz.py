"""Authorization tests for the platform API (section 14, "Security and authorization")."""
from __future__ import annotations

import json

import pytest

from tests.conftest import agent_id, auth

PROTECTED_GET = ["/api/v1/agents", "/api/v1/policies", "/api/v1/test-runs", "/api/v1/reports", "/api/v1/approvals",
                 "/api/v1/traces", "/api/v1/runtime/events", "/api/v1/analytics", "/api/v1/overview", "/api/v1/integrations",
                 "/api/v1/investigations", "/api/v1/scenarios", "/api/v1/users", "/api/v1/settings/data"]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_unauthenticated_requests_rejected(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_health_is_public_and_leaks_nothing(client):
    r = client.get("/api/v1/health").json()
    assert set(r) == {"status", "database", "version", "time"}


def test_viewer_cannot_write(client):
    h = auth(client, "viewer")
    pol = client.get("/api/v1/policies", headers=h).json()["items"][0]
    checks = [
        client.patch(f"/api/v1/policies/{pol['id']}", json={"priority": 5, "change_note": "x x"}, headers=h),
        client.post("/api/v1/test-runs", json={"agent_id": agent_id(client, "ops-naive"), "suite_key": "quick-demo"}, headers=h),
        client.post("/api/v1/approvals/apr_00000000000000000000/decide", json={"approve": True}, headers=h),
        client.post("/api/v1/runtime/invoke", json={"agent_id": "x", "tool": "files.read"}, headers=h),
        client.post("/api/v1/users", json={"username": "eve", "password": "x" * 12, "role": "admin"}, headers=h),
        client.post("/api/v1/settings/data/purge", json={"older_than_days": 0, "confirm": "DELETE"}, headers=h),
        client.post("/api/v1/reports", json={"run_id": "run_x"}, headers=h),
        client.post("/api/v1/agents", json={}, headers=h),
        client.get("/api/v1/users", headers=h),
    ]
    assert [r.status_code for r in checks] == [403] * len(checks)


def test_operator_cannot_administer_policies_users_or_data(client):
    h = auth(client, "operator")
    pol = client.get("/api/v1/policies", headers=h).json()["items"][0]
    assert client.patch(f"/api/v1/policies/{pol['id']}", json={"priority": 5, "change_note": "x x"}, headers=h).status_code == 403
    assert client.get("/api/v1/users", headers=h).status_code == 403
    assert client.post("/api/v1/runtime/sandbox/reset", headers=h).status_code == 403


def test_locked_policy_cannot_be_disabled_even_by_admin(client):
    h = auth(client, "admin")
    pol = next(p for p in client.get("/api/v1/policies", headers=h).json()["items"] if p["key"] == "SEN-001")
    r = client.patch(f"/api/v1/policies/{pol['id']}", json={"enabled": False, "change_note": "try"}, headers=h)
    assert r.status_code == 403


def test_policy_edit_is_versioned(client):
    h = auth(client, "admin")
    pol = next(p for p in client.get("/api/v1/policies", headers=h).json()["items"] if p["key"] == "SEN-041")
    r = client.patch(f"/api/v1/policies/{pol['id']}", json={"params": {**pol["params"], "max_calls": 150},
                                                            "change_note": "raise agent limit"}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == pol["version"] + 1
    detail = client.get(f"/api/v1/policies/{pol['id']}", headers=h).json()
    assert detail["versions"][0]["change_note"] == "raise agent limit" and detail["versions"][0]["changed_by"] == "admin"
    bad = client.patch(f"/api/v1/policies/{pol['id']}", json={"params": {"max_calls": -1}, "change_note": "bad"}, headers=h)
    assert bad.status_code == 422


def test_unknown_object_ids_return_uniform_404(client):
    h = auth(client, "admin")
    for path in ("/api/v1/test-runs/run_doesnotexist", "/api/v1/reports/rep_x", "/api/v1/agents/agt_x",
                 "/api/v1/investigations/inv_x", "/api/v1/traces/cor_x", "/api/v1/reports/" + "a" * 300):
        assert client.get(path, headers=h).status_code == 404


def test_validation_errors_do_not_echo_input(client):
    h = auth(client, "operator")
    secret = "sk_test_SHOULDNOTECHO1234567890"
    r = client.post("/api/v1/runtime/sessions", json={"agent_id": "x", "task": "", "session_subject": secret}, headers=h)
    assert r.status_code == 422 and secret not in r.text


def test_oversized_body_rejected(client):
    h = auth(client, "operator")
    r = client.post("/api/v1/runtime/sessions", content=b'{"task":"' + b"a" * 2_000_000 + b'"}',
                    headers={**h, "content-type": "application/json"})
    assert r.status_code == 413


def test_login_throttling(client):
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"}).status_code == 429
    from app.security.authentication import login_throttle
    login_throttle._failures.clear()


def test_logout_revokes_token(client):
    from tests.conftest import PASSWORDS
    t = client.post("/api/v1/auth/login", json={"username": "operator", "password": PASSWORDS["operator"]}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    client.post("/api/v1/auth/logout", headers=h)
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_websocket_requires_authentication(client):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/api/v1/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "bogus"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
