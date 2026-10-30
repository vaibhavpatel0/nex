"""Connection-center integration tests against real HTTPS fixtures, a real DNS server and a real AEGIS process.

Nothing is simulated: discovery parses documents served over TLS, verification reads a real TXT record or
a real file, credentials authenticate real requests, tests send real messages, and protection is judged from
decisions SENTINEL actually recorded when the fixture agent asked it.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.connect.conftest import ADMIN, BEARER_TOKEN, TXT_RECORDS

pytestmark = pytest.mark.connect


def H(e, user):
    return e.login(user, f"{user}-password-123")


def create(e, user, url, protocol="auto", expect=201):
    r = e.http.post("/connections", json={"url": url, "protocol": protocol}, headers=H(e, user))
    assert r.status_code == expect, r.text
    return r.json()


def verify_by_file(e, user, c):
    r = e.http.post(f"/connections/{c['id']}/challenges", json={"method": "https_file"}, headers=H(e, user))
    assert r.status_code == 201, r.text
    ch = r.json()["challenges"][0]
    # Before the file exists: not verified.
    r = e.http.post(f"/connections/{c['id']}/challenges/{ch['id']}/check", headers=H(e, user))
    assert r.json()["verified"] is False
    with e.fixture(c["host"]) as f:
        f.post("/__fixture/verification-file", json={"id": ch["id"], "value": ch["value"]}).raise_for_status()
    r = e.http.post(f"/connections/{c['id']}/challenges/{ch['id']}/check", headers=H(e, user))
    assert r.json()["verified"] is True, r.text
    return r.json()["connection"]


def wait_run(e, user, run_id, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        d = e.http.get(f"/test-runs/{run_id}", headers=H(e, user)).json()
        if d["status"] in ("completed", "failed"):
            return d
        time.sleep(0.5)
    raise AssertionError("run did not finish")


# ------------------------------------------------------------------ discovery
def test_discovery_identifies_each_protocol(cenv):
    e = cenv
    c = create(e, "dev1", e.url("agent.test"))
    assert c["protocol"] == "custom_http" and c["endpoint"] == e.url("agent.test", "/aegis/message")
    assert c["descriptor"]["identity"]["name"] == "Fixture Support Agent"
    assert c["descriptor"]["auth"]["required"] is True
    assert c["state"] == "pending" and c["verification"]["status"] == "unverified"
    assert c["capabilities"]["interaction"]["supported"] is True
    assert c["capabilities"]["runtime_enforcement"]["supported"] == "conditional"
    assert c["protection"]["status"] == "not_protected"

    a = create(e, "dev1", e.url("a2a.agent.test", "/.well-known/agent-card.json"))
    assert a["protocol"] == "a2a" and a["endpoint"] == e.url("a2a.agent.test", "/a2a")
    assert a["descriptor"]["skills"][0]["id"] == "book" and a["capabilities"]["tool_discovery"]["supported"] is False

    m = create(e, "dev1", e.url("mcp.agent.test", "/mcp"))
    assert m["protocol"] == "mcp" and [t["name"] for t in m["descriptor"]["tools"]] == ["read_file", "delete_file"]
    assert m["capabilities"]["interaction"]["supported"] is False and m["capabilities"]["security_testing"]["supported"] is False

    o = create(e, "dev1", e.url("api.agent.test", "/openapi.json"))
    assert o["protocol"] == "openapi" and {t["name"] for t in o["descriptor"]["tools"]} == {"getOrder", "deleteOrder"}
    assert o["capabilities"]["interaction"]["supported"] is False


def test_a_website_is_not_an_agent(cenv):
    c = create(cenv, "dev1", cenv.url("web.agent.test"))
    assert c["protocol"] == "none"
    assert any("not an agent backend" in w for w in c["descriptor"]["warnings"])
    assert all(v["supported"] is False for v in c["capabilities"].values())


def test_card_pointing_to_another_host_is_not_trusted(cenv):
    c = create(cenv, "dev1", cenv.url("cross.agent.test", "/.well-known/agent-card.json"))
    assert c["protocol"] == "a2a" and c["endpoint"] is None
    assert any("different host" in w for w in c["descriptor"]["warnings"])


# ------------------------------------------------------------------ SSRF and URL validation
@pytest.mark.parametrize("url,fragment", [
    ("http://agent.test/", "Only HTTPS"),
    ("https://127.0.0.1:{p}/", "not an IP address"),
    ("https://internal.test:{p}/", "private, loopback"),
    ("https://meta.test/latest/meta-data/", "private, loopback"),
    ("https://ten.test/", "private, loopback"),
    ("https://mixed.test/", "private, loopback"),
    ("https://user:pass@agent.test:{p}/", "user names or passwords"),
    ("https://agent.test:22/", "port"),
    ("https://nothing-here.test/", "does not resolve"),
])
def test_unsafe_urls_are_refused(cenv, url, fragment):
    r = cenv.http.post("/connections", json={"url": url.format(p=cenv.fport)}, headers=H(cenv, "dev1"))
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_redirect_into_private_network_is_refused(cenv):
    c = create(cenv, "dev1", cenv.url("redir.agent.test"))
    assert c["protocol"] == "none"
    ev = [x["finding"] for x in c["descriptor"]["evidence"]]
    assert any("private, loopback" in f for f in ev), ev


def test_oversized_response_is_refused(cenv):
    c = create(cenv, "dev1", cenv.url("big.agent.test", "/.well-known/agent-card.json"))
    assert c["protocol"] == "none"
    assert any("byte limit" in x["finding"] for x in c["descriptor"]["evidence"])


# ------------------------------------------------------------------ full flow: custom HTTP agent
def test_custom_agent_full_flow_and_isolation(cenv):
    e = cenv
    c = create(e, "dev1", e.url("agent.test"))
    cid = c["id"]

    # Tenant isolation: another developer cannot see or touch it; a viewer cannot create connections.
    assert e.http.get(f"/connections/{cid}", headers=H(e, "dev2")).status_code == 404
    assert cid not in [x["id"] for x in e.http.get("/connections", headers=H(e, "dev2")).json()["items"]]
    assert e.http.post("/connections", json={"url": e.url("agent.test")}, headers=H(e, "watcher")).status_code == 403
    admin = e.login(*ADMIN)
    assert cid in [x["id"] for x in e.http.get("/connections", headers=admin).json()["items"]]

    # Authorization before verification is refused; public reachability is not verification.
    r = e.http.post(f"/connections/{cid}/authorize", json={"accept": True, "permissions": ["read_descriptor"]}, headers=H(e, "dev1"))
    assert r.status_code == 409

    c = verify_by_file(e, "dev1", c)
    assert c["state"] == "verified" and c["verification"]["method"] == "https_file" and c["verification"]["verified_by"] == "dev1"

    perms = ["read_descriptor", "health_check", "send_test_messages"]
    r = e.http.post(f"/connections/{cid}/authorize", json={"accept": True, "permissions": perms}, headers=H(e, "dev1"))
    assert r.status_code == 422 and "requires authentication" in r.json()["detail"]
    model_key = "sk-proj-" + "A" * 40
    r = e.http.post(f"/connections/{cid}/authorize", json={"accept": True, "permissions": perms,
                                                          "credential": {"kind": "bearer", "secret": model_key}}, headers=H(e, "dev1"))
    assert r.status_code == 422 and "model API key" in r.json()["detail"]
    r = e.http.post(f"/connections/{cid}/authorize", json={"accept": True, "permissions": perms,
                                                          "credential": {"kind": "bearer", "secret": BEARER_TOKEN}}, headers=H(e, "dev1"))
    assert r.status_code == 200, r.text
    assert BEARER_TOKEN not in r.text
    assert r.json()["authorization"]["credential"]["kind"] == "bearer"

    r = e.http.post(f"/connections/{cid}/connect", headers=H(e, "dev1"))
    assert r.status_code == 200, r.text
    c = r.json()["connection"]
    assert r.json()["health"]["healthy"] is True and c["state"] == "monitoring" and c["agent_id"]
    assert c["protection"]["status"] == "not_protected"

    # No test scope yet: refused, through both entry points.
    assert e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev1")).status_code == 403
    assert e.http.post("/test-runs", json={"agent_id": c["agent_id"], "suite_key": "connected-agent"},
                       headers=H(e, "dev1")).status_code == 403
    # Out-of-scope suites are refused even with a scope.
    r = e.http.post(f"/connections/{cid}/test-scope", json={"suites": ["connected-agent"], "max_runs_per_day": 2, "days": 1,
                                                           "environment": "test", "confirm": True}, headers=H(e, "dev1"))
    assert r.status_code == 200, r.text
    assert e.http.post("/test-runs", json={"agent_id": c["agent_id"], "suite_key": "quick-demo"},
                       headers=H(e, "dev1")).status_code == 403
    assert e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev2")).status_code == 404

    r = e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev1"))
    assert r.status_code == 202, r.text
    run = wait_run(e, "dev1", r.json()["run_id"])
    outcomes = {x["scenario_key"]: x["outcome"] for x in run["results"]}
    # The naive fixture agent repeats injected markers: every injection scenario must FAIL; the baseline passes.
    assert outcomes["CON-001"] == "PASS"
    assert outcomes["CON-002"] == outcomes["CON-003"] == outcomes["CON-005"] == outcomes["CON-006"] == "FAIL"
    assert outcomes["CON-004"] == "PASS"

    # Run-per-day limit from the approved scope (2): one more allowed, then refused.
    r = e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev1"))
    assert r.status_code == 202
    wait_run(e, "dev1", r.json()["run_id"])
    assert e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev1")).status_code == 429

    d = e.http.get(f"/connections/{cid}", headers=H(e, "dev1")).json()
    assert d["last_test"]["status"] == "completed" and d["findings"]["open"] >= 4
    events = [x["action"] for x in e.http.get(f"/connections/{cid}/events", headers=H(e, "dev1")).json()["items"]]
    for a in ("discovered", "challenge_issued", "ownership_verified", "access_authorized", "connected", "health_check",
              "test_scope_approved", "test_authorized"):
        assert a in events, a
    assert BEARER_TOKEN not in json.dumps(e.http.get(f"/connections/{cid}/events", headers=H(e, "dev1")).json())


# ------------------------------------------------------------------ DNS verification + A2A
def test_a2a_agent_dns_verification_and_tests(cenv):
    e = cenv
    c = create(e, "dev2", e.url("a2a.agent.test", "/.well-known/agent-card.json"))
    cid = c["id"]
    r = e.http.post(f"/connections/{cid}/challenges", json={"method": "dns_txt", "domain": "agent.test"}, headers=H(e, "dev2"))
    assert r.status_code == 201
    ch = r.json()["challenges"][0]
    assert ch["location"] == "_aegis-challenge.agent.test"
    assert e.http.post(f"/connections/{cid}/challenges", json={"method": "dns_txt", "domain": "other.test"},
                       headers=H(e, "dev2")).status_code == 422
    assert e.http.post(f"/connections/{cid}/challenges/{ch['id']}/check", headers=H(e, "dev2")).json()["verified"] is False
    TXT_RECORDS["_aegis-challenge.agent.test"] = ["unrelated=1", ch["value"]]
    r = e.http.post(f"/connections/{cid}/challenges/{ch['id']}/check", headers=H(e, "dev2"))
    assert r.json()["verified"] is True
    c = r.json()["connection"]
    assert c["verification"]["domain"] == "agent.test" and c["verification"]["method"] == "dns_txt"

    perms = ["read_descriptor", "health_check", "send_test_messages"]
    assert e.http.post(f"/connections/{cid}/authorize", json={"accept": False, "permissions": perms},
                       headers=H(e, "dev2")).status_code == 422
    assert e.http.post(f"/connections/{cid}/authorize", json={"accept": True, "permissions": perms},
                       headers=H(e, "dev2")).status_code == 200
    r = e.http.post(f"/connections/{cid}/connect", headers=H(e, "dev2"))
    assert r.json()["health"]["healthy"] is True, r.text
    e.http.post(f"/connections/{cid}/test-scope", json={"suites": ["connected-agent"], "environment": "staging", "confirm": True},
                headers=H(e, "dev2")).raise_for_status()
    run = wait_run(e, "dev2", e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev2")).json()["run_id"])
    outcomes = {x["scenario_key"]: x["outcome"] for x in run["results"]}
    assert set(outcomes.values()) == {"PASS"}, outcomes  # the careful A2A agent refuses every injection


# ------------------------------------------------------------------ protected mode with real SENTINEL enforcement
def test_protected_mode_requires_evidence_and_blocks_before_execution(cenv):
    e = cenv
    c = [x for x in e.http.get("/connections", headers=H(e, "dev1")).json()["items"]
         if x["host"] == "agent.test" and x["state"] == "monitoring"][0]
    cid = c["id"]
    r = e.http.post(f"/connections/{cid}/mode", json={"mode": "protected"}, headers=H(e, "dev1"))
    assert r.status_code == 200, r.text
    s = r.json()["sentinel"]
    token, agent_key, ns = s["token"], s["agent_key"], s["tool_namespace"]
    assert token.startswith("aegis_svc_")
    d = e.http.get(f"/connections/{cid}", headers=H(e, "dev1")).json()
    assert token not in json.dumps(d)
    # Configured is not protected.
    assert d["protection"]["status"] == "awaiting_evidence" and d["state"] == "monitoring"

    with e.fixture("agent.test") as f:
        f.post("/__fixture/sentinel", json={"base_url": e.aegis, "token": token, "agent_key": agent_key,
                                             "namespace": ns}).raise_for_status()
        hdr = {"Authorization": f"Bearer {BEARER_TOKEN}"}
        # Tools are not granted by default: SENTINEL blocks and the record survives.
        r = f.post("/aegis/message", json={"message": "please delete record R-1", "correlation_id": ""}, headers=hdr)
        assert "did not delete" in r.json()["reply"] and "BLOCK" in r.json()["reply"]
        assert "R-1" in f.get("/__fixture/state").json()["records"]

        d = e.http.get(f"/connections/{cid}", headers=H(e, "dev1")).json()
        assert d["protection"]["status"] == "protected" and d["state"] == "protected"
        assert {t["name"]: t["level"] for t in d["tool_grants"]} == {f"{ns}.delete_record": "deny", f"{ns}.read_record": "deny"}

        # Allow with approval: still not executed.
        assert e.http.put(f"/connections/{cid}/permissions", json={"levels": {f"{ns}.delete_record": "approval"}},
                          headers=H(e, "dev1")).status_code == 200
        r = f.post("/aegis/message", json={"message": "please delete record R-1"}, headers=hdr)
        assert "REQUIRE_APPROVAL" in r.json()["reply"] and "R-1" in f.get("/__fixture/state").json()["records"]

        # Unknown tool names cannot be granted; another developer cannot change grants.
        assert e.http.put(f"/connections/{cid}/permissions", json={"levels": {"other.tool": "allow"}},
                          headers=H(e, "dev1")).status_code == 422
        assert e.http.put(f"/connections/{cid}/permissions", json={"levels": {f"{ns}.delete_record": "allow"}},
                          headers=H(e, "dev2")).status_code == 404

        assert e.http.put(f"/connections/{cid}/permissions", json={"levels": {f"{ns}.delete_record": "allow"}},
                          headers=H(e, "dev1")).status_code == 200
        r = f.post("/aegis/message", json={"message": "please delete record R-1"}, headers=hdr)
        assert r.json()["reply"] == "Deleted R-1." and "R-1" not in f.get("/__fixture/state").json()["records"]

        # Disconnect revokes the SENTINEL token and the credential.
        d = e.http.post(f"/connections/{cid}/disconnect", headers=H(e, "dev1")).json()
        assert d["state"] == "disconnected" and d["authorization"]["credential"] is None
        r = f.post("/aegis/message", json={"message": "please delete record R-2"}, headers=hdr)
        assert r.status_code == 500  # the fixture's SENTINEL call is rejected (401)
    assert e.http.post(f"/connections/{cid}/tests", json={}, headers=H(e, "dev1")).status_code == 409
    assert e.http.post(f"/connections/{cid}/health", headers=H(e, "dev1")).status_code == 409


# ------------------------------------------------------------------ MCP, OpenAPI and OAuth
def test_mcp_server_is_inspected_but_never_called(cenv):
    e = cenv
    c = create(e, "dev2", e.url("mcp.agent.test", "/mcp"))
    c = verify_by_file(e, "dev2", c)
    e.http.post(f"/connections/{c['id']}/authorize", json={"accept": True, "permissions": ["read_descriptor"]},
                headers=H(e, "dev2")).raise_for_status()
    r = e.http.post(f"/connections/{c['id']}/connect", headers=H(e, "dev2"))
    assert r.json()["health"]["healthy"] is True and r.json()["connection"]["agent_id"] is None
    assert e.http.post(f"/connections/{c['id']}/tests", json={}, headers=H(e, "dev2")).status_code == 409
    assert e.http.post(f"/connections/{c['id']}/test-scope", json={"suites": ["connected-agent"], "environment": "test",
                                                                  "confirm": True}, headers=H(e, "dev2")).status_code == 409


def test_oauth_client_credentials(cenv):
    from tests.connect.conftest import OAUTH_CLIENT

    e = cenv
    c = create(e, "dev1", e.url("oauth.agent.test"))
    assert c["protocol"] == "custom_http" and c["descriptor"]["auth"]["token_url"] == e.url("oauth.agent.test", "/oauth/token")
    c = verify_by_file(e, "dev1", c)
    perms = ["read_descriptor", "health_check"]
    bad = {"kind": "oauth2_client_credentials", "secret": OAUTH_CLIENT[1], "client_id": OAUTH_CLIENT[0],
           "token_url": e.url("agent.test", "/oauth/token")}
    r = e.http.post(f"/connections/{c['id']}/authorize", json={"accept": True, "permissions": perms, "credential": bad}, headers=H(e, "dev1"))
    assert r.status_code == 422 and "must be on oauth.agent.test" in r.json()["detail"]
    good = dict(bad, token_url=e.url("oauth.agent.test", "/oauth/token"), scopes=["agent.invoke"])
    r = e.http.post(f"/connections/{c['id']}/authorize", json={"accept": True, "permissions": perms, "credential": good}, headers=H(e, "dev1"))
    assert r.status_code == 200 and OAUTH_CLIENT[1] not in r.text
    r = e.http.post(f"/connections/{c['id']}/connect", headers=H(e, "dev1"))
    assert r.json()["health"]["healthy"] is True, r.text
    # Revoking credentials makes the next check fail honestly.
    e.http.post(f"/connections/{c['id']}/revoke-credentials", headers=H(e, "dev1")).raise_for_status()
    assert e.http.post(f"/connections/{c['id']}/health", headers=H(e, "dev1")).status_code == 409


def test_credentials_are_encrypted_at_rest(cenv):
    import sqlite3

    db = sqlite3.connect(str(cenv.data_dir / "aegis.db"))
    rows = db.execute("select ciphertext, fingerprint from connection_credentials").fetchall()
    assert rows
    for ct, fp in rows:
        assert BEARER_TOKEN not in ct and ct.startswith("gAAAA") and len(fp) == 12
    key = (cenv.data_dir / "credential.key")
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"
