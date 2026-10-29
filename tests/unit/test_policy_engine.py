"""Unit tests for the deterministic policy engine (no database, no I/O)."""
from datetime import timedelta

import pytest

from app.core.timeutil import utcnow
from app.security.policy_engine import AgentView, EvaluationInput, HistoryItem, PolicyView, evaluate
from app.seed.catalog import POLICIES, PROFILES

OPS = next(p for p in PROFILES if p["key"] == "operations-full")


def views(overrides=None, extra=()):
    out = []
    for p in POLICIES:
        d = dict(p)
        d.pop("locked", None)
        d.pop("description", None)
        d.update((overrides or {}).get(p["key"], {}))
        out.append(PolicyView(version=1, enabled=d.pop("enabled", True), **d))
    return out + list(extra)


def agent(tools=None, grants=None):
    g = grants or OPS["grants"]
    return AgentView(id="agt_x", key="x", enabled_tools=tuple(tools or g["tools"].keys()), grants=g, revision=1)


def inp(tool, op="read", sens="medium", args=None, resource=None, subject=None, history=None, err=None, a=None):
    return EvaluationInput(agent=a or agent(), tool_name=tool, tool_operation=op, tool_sensitivity=sens,
                           args=args if err is None else None, args_error=err, resource=resource or {},
                           session_subject=subject, session_history=history or [], agent_recent_calls={"*": 0}, now=utcnow())


def test_permitted_read_is_allowed_by_profile_grant():
    r = evaluate(inp("files.read", args={"path": "documents/public/x.md"},
                     resource={"path": "documents/public/x.md", "inside_sandbox": True}), views())
    assert r.decision == "ALLOW" and r.matched_policy_key == "SEN-100"
    assert r.constraints["timeout_seconds"] == 3.0


def test_restricted_path_blocked():
    r = evaluate(inp("files.read", args={"path": "documents/restricted/p.csv"},
                     resource={"path": "documents/restricted/p.csv", "inside_sandbox": True}), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-010"


def test_traversal_outside_sandbox_blocked():
    r = evaluate(inp("files.read", args={"path": "../../etc/passwd"}, resource={"path": None, "inside_sandbox": False}), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-010"


def test_unknown_tool_default_denied():
    r = evaluate(inp("admin.grant_access", op=None, sens=None, args={}), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-001"


def test_tool_not_granted_blocked():
    r = evaluate(inp("email.send", op="egress", args={"to": ["a@halden.test"]}, resource={"recipient_domains": ["halden.test"]},
                     a=agent(tools=["files.read"])), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-002"


def test_default_deny_when_allow_policy_disabled():
    r = evaluate(inp("inventory.get_stock", args={"sku": "SKU-100"}), views({"SEN-100": {"enabled": False}}))
    assert r.decision == "BLOCK" and r.matched_policy_key is None
    assert "default deny" in r.reason


def test_deny_overrides_higher_priority_allow():
    allow = PolicyView(key="CUS-001", name="allow all", rule_type="granted_by_profile", effect="ALLOW", priority=9999,
                       enabled=True, applies_to=("*",), params={}, risk="low", version=1)
    r = evaluate(inp("db.query", args={"table": "payment_methods"}), views(extra=[allow]))
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-012"


def test_rule_error_fails_closed():
    broken = PolicyView(key="CUS-002", name="broken", rule_type="rate_limit", effect="BLOCK", priority=50, enabled=True,
                        applies_to=("*",), params={"max_calls": "not-a-number"}, risk="low", version=1)
    r = evaluate(inp("inventory.get_stock", args={"sku": "SKU-100"}), views(extra=[broken]))
    assert r.decision == "BLOCK" and r.matched_policy_key == "CUS-002"
    assert any(e["result"] == "error" for e in r.evaluated)


def test_unknown_rule_type_fails_closed():
    weird = PolicyView(key="CUS-003", name="weird", rule_type="does_not_exist", effect="REQUIRE_APPROVAL", priority=5,
                       enabled=True, applies_to=("*",), params={}, risk="low", version=1)
    assert evaluate(inp("inventory.get_stock", args={"sku": "SKU-100"}), views(extra=[weird])).decision == "BLOCK"


def test_invalid_arguments_blocked_and_arg_rules_skipped():
    r = evaluate(inp("db.query", err="table: value_error"), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-003"
    assert any(e["key"] == "SEN-012" and e["result"] == "skipped" for e in r.evaluated)


def test_customer_scope():
    ok = evaluate(inp("orders.lookup", args={"order_id": "ORD-5001"}, resource={"owner_customer_id": "CUST-1001"},
                      subject="CUST-1001"), views())
    bad = evaluate(inp("orders.lookup", args={"order_id": "ORD-5004"}, resource={"owner_customer_id": "CUST-1003"},
                       subject="CUST-1001"), views())
    none = evaluate(inp("orders.lookup", args={"order_id": "ORD-5001"}, resource={"owner_customer_id": "CUST-1001"}), views())
    assert ok.decision == "ALLOW" and bad.decision == "BLOCK" and none.decision == "BLOCK"


def test_column_scope():
    r = evaluate(inp("db.query", args={"table": "orders", "columns": ["id", "ship_to"], "filters": {}}), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-012"


@pytest.mark.parametrize("url,decision", [
    ("https://docs.halden.test/shipping-carriers", "ALLOW"),
    ("http://docs.halden.test/x", "BLOCK"),
    ("https://169.254.169.254/latest", "BLOCK"),
    ("https://localhost/admin", "BLOCK"),
    ("https://user:pw@docs.halden.test/", "BLOCK"),
    ("https://docs.halden.test:8443/", "BLOCK"),
    ("https://evil.example/", "BLOCK"),
])
def test_url_allowlist(url, decision):
    from app.adapters.tools.service_tools import url_attributes
    assert evaluate(inp("web.fetch", args={"url": url}, resource=url_attributes(url)), views()).decision == decision


def test_external_email_requires_approval_internal_allowed():
    ext = evaluate(inp("email.send", op="egress", sens="high", args={"to": ["x@customer.example"], "subject": "s", "body": "hello"},
                       resource={"recipient_domains": ["customer.example"], "attachments": []}), views())
    internal = evaluate(inp("email.send", op="egress", sens="high", args={"to": ["x@halden.test"], "subject": "s", "body": "hello"},
                            resource={"recipient_domains": ["halden.test"], "attachments": []}), views())
    assert ext.decision == "REQUIRE_APPROVAL" and set(ext.approval_policy_keys) >= {"SEN-030"}
    assert internal.decision == "ALLOW"


def test_sensitive_egress_blocks_even_internal():
    r = evaluate(inp("email.send", op="egress", sens="high",
                     args={"to": ["x@halden.test"], "subject": "s", "body": "key sk_test_ABCDEFGHIJKLMNOPQRST"},
                     resource={"recipient_domains": ["halden.test"], "attachments": []}), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-022"
    assert "sk_test" not in r.reason  # reasons never echo values


def test_confidential_attachment_blocked():
    r = evaluate(inp("email.send", op="egress", sens="high", args={"to": ["x@halden.test"], "subject": "s", "body": "b"},
                     resource={"recipient_domains": ["halden.test"],
                               "attachments": [{"path": "documents/restricted/a.env", "inside_sandbox": True, "classification": "confidential"}]}),
                 views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-021"


def test_session_rate_limit():
    now = utcnow()
    hist = [HistoryItem("inventory.get_stock", "read", "executed", "public", now - timedelta(seconds=1)) for _ in range(8)]
    r = evaluate(inp("inventory.get_stock", args={"sku": "SKU-100"}, history=hist), views())
    assert r.decision == "BLOCK" and r.matched_policy_key == "SEN-040"


def test_failure_limit_resets_after_success():
    now = utcnow()
    fails = [HistoryItem("inventory.get_stock", "read", "failed", None, now) for _ in range(3)]
    assert evaluate(inp("inventory.get_stock", args={"sku": "SKU-1"}, history=fails), views()).matched_policy_key == "SEN-042"
    mixed = fails + [HistoryItem("inventory.get_stock", "read", "executed", "public", now)]
    assert evaluate(inp("inventory.get_stock", args={"sku": "SKU-1"}, history=mixed), views()).decision == "ALLOW"


def test_policy_hash_changes_with_params():
    from app.security.policy_engine import compute_policy_hash
    assert compute_policy_hash(views()) != compute_policy_hash(views({"SEN-040": {"params": {"max_calls": 9}}}))
