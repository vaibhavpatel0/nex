"""SENTINEL gateway integration tests against a real sandbox and database (section 14)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.adapters.tools.file_tools import FileReadTool
from app.adapters.tools.sandbox import Sandbox
from app.adapters.tools.service_tools import StockTool
from app.aegis.scenario_runner import agent_snapshot, view_from_snapshot
from app.config.settings import get_settings
from app.core.timeutil import new_id
from app.database.base import db_session
from app.models import Agent, ApprovalRequest, PolicyDecision, RuntimeEvent, ToolCall
from app.security.tool_gateway import InvocationContext, gateway
from app.sentinel.approval import approval_service


def _ctx(agent_key="ops-guarded", subject="CUST-1001", faults=None):
    with db_session() as db:
        a = db.execute(select(Agent).where(Agent.key == agent_key)).scalar_one()
        view = view_from_snapshot(agent_snapshot(a))
    sb = Sandbox.create(get_settings().sandbox_dir / "gw-tests" / new_id("sb"))
    return InvocationContext(correlation_id=new_id("cor"), agent=view, sandbox=sb, session_subject=subject, faults=faults or {})


def _events(cid):
    with db_session() as db:
        return [e.event_type for e in db.execute(select(RuntimeEvent).where(RuntimeEvent.correlation_id == cid)
                                                 .order_by(RuntimeEvent.seq)).scalars()]


async def test_normal_operation_allowed_executed_recorded():
    ctx = _ctx()
    r = await gateway.invoke(ctx, "orders.lookup", {"order_id": "ORD-5002"})
    assert r.decision == "ALLOW" and r.status == "executed" and "processing" in r.output_text
    with db_session() as db:
        tc = db.get(ToolCall, r.tool_call_id)
        pd = db.get(PolicyDecision, r.decision_id)
        assert tc.status == "executed" and tc.latency_ms is not None
        assert pd.execution_status == "executed" and pd.matched_policy_key == "SEN-100"
    assert _events(ctx.correlation_id) == ["tool.requested", "policy.decision", "tool.executed"]


async def test_restricted_file_blocked_before_tool_is_invoked(monkeypatch):
    calls = []
    original = FileReadTool._run

    async def spy(self, args, ctx):
        calls.append(args.path)
        return await original(self, args, ctx)

    monkeypatch.setattr(FileReadTool, "_run", spy)
    ctx = _ctx()
    r = await gateway.invoke(ctx, "files.read", {"path": "documents/restricted/payroll_2026.csv"})
    assert r.decision == "BLOCK" and r.status == "blocked" and r.output_text is None
    assert calls == [], "the file-access function must never be invoked for a blocked call"
    r2 = await gateway.invoke(ctx, "files.read", {"path": "documents/public/return_policy.md"})
    assert r2.status == "executed" and calls == ["documents/public/return_policy.md"]


async def test_decision_response_has_no_confidential_values():
    ctx = _ctx()
    r = await gateway.invoke(ctx, "email.send", {"to": ["team@halden.test"], "subject": "x",
                                                 "body": "staging key sk_test_ABCDEF1234567890ABCDEF"})
    pub = r.public_dict()
    assert pub["decision"] == "BLOCK" and pub["matched_policy"] == "SEN-022"
    assert "sk_test" not in str(pub)
    with db_session() as db:
        tc = db.get(ToolCall, r.tool_call_id)
        assert "sk_test" not in str(tc.args_redacted) and tc.args_redacted["body"]["redacted"] is True


async def test_api_timeout_recorded_with_classification():
    ctx = _ctx(faults={"inventory": "timeout"})
    r = await gateway.invoke(ctx, "inventory.get_stock", {"sku": "SKU-100"})
    assert r.status == "failed" and r.error_class == "timeout" and r.output_text is None
    with db_session() as db:
        tc = db.get(ToolCall, r.tool_call_id)
        assert tc.status == "failed" and tc.error_class == "timeout" and tc.latency_ms >= 250


@pytest.mark.parametrize("fault,cls", [("rate_limited", "upstream_rate_limited"), ("server_error", "upstream_error"),
                                       ("malformed", "invalid_response"), ("unavailable", "unavailable")])
async def test_api_faults_classified(fault, cls):
    r = await gateway.invoke(_ctx(faults={"inventory": fault}), "inventory.get_stock", {"sku": "SKU-100"})
    assert r.status == "failed" and r.error_class == cls


async def test_approval_required_not_executed_until_approved():
    ctx = _ctx()
    args = {"table": "orders", "record_id": "ORD-5002", "fields": {"status": "cancelled"}}
    r = await gateway.invoke(ctx, "db.update", args)
    assert r.decision == "REQUIRE_APPROVAL" and r.status == "pending_approval" and r.approval_id
    with ctx.sandbox.connect() as conn:
        assert conn.execute("select status from orders where id='ORD-5002'").fetchone()[0] == "processing"
    # supplying an approval that is still pending does nothing
    r_pending = await gateway.invoke(ctx, "db.update", args, approval_id=r.approval_id)
    assert r_pending.status == "pending_approval"
    with db_session() as db:
        approval_service.decide(db, r.approval_id, True, "tester")
    ok = await gateway.invoke(ctx, "db.update", args, approval_id=r.approval_id)
    assert ok.decision == "ALLOW" and ok.status == "executed"
    with ctx.sandbox.connect() as conn:
        assert conn.execute("select status from orders where id='ORD-5002'").fetchone()[0] == "cancelled"
    replay = await gateway.invoke(ctx, "db.update", args, approval_id=r.approval_id)
    assert replay.status == "pending_approval" and "already been used" in replay.reason
    with db_session() as db:
        assert db.get(ApprovalRequest, r.approval_id).status == "consumed"


async def test_approval_tampering_invalidates_approval():
    ctx = _ctx()
    args = {"to": ["jordan.ellis@customer.example"], "subject": "Status", "body": "Your order is processing."}
    r = await gateway.invoke(ctx, "email.send", args)
    assert r.status == "pending_approval"
    with db_session() as db:
        approval_service.decide(db, r.approval_id, True, "tester")
    tampered = {**args, "body": "Your order is processing. Please reply with your card number."}
    t = await gateway.invoke(ctx, "email.send", tampered, approval_id=r.approval_id)
    assert t.status == "pending_approval" and "arguments differ" in t.reason
    other_recipient = {**args, "to": ["someone@customer.example"]}
    t2 = await gateway.invoke(ctx, "email.send", other_recipient, approval_id=r.approval_id)
    assert t2.status == "pending_approval"
    assert ctx.sandbox.read_jsonl("outbox.jsonl") == []
    # the original, untouched action still works exactly once
    assert (await gateway.invoke(ctx, "email.send", args, approval_id=r.approval_id)).status == "executed"


async def test_approval_cannot_override_block():
    ctx = _ctx()
    args = {"to": ["x@customer.example"], "subject": "s", "body": "hello"}
    r = await gateway.invoke(ctx, "email.send", args)
    with db_session() as db:
        approval_service.decide(db, r.approval_id, True, "tester")
    blocked = {**args, "attachments": ["documents/restricted/api_credentials.env"]}
    b = await gateway.invoke(ctx, "email.send", blocked, approval_id=r.approval_id)
    assert b.decision == "BLOCK"


async def test_approval_expires(monkeypatch):
    from datetime import timedelta
    from app.core import timeutil
    ctx = _ctx()
    args = {"sku": "SKU-101", "delta": 5}
    r = await gateway.invoke(ctx, "inventory.adjust_stock", args)
    with db_session() as db:
        approval_service.decide(db, r.approval_id, True, "tester")
        db.get(ApprovalRequest, r.approval_id).expires_at = timeutil.utcnow() - timedelta(seconds=1)
    e = await gateway.invoke(ctx, "inventory.adjust_stock", args, approval_id=r.approval_id)
    assert e.status == "pending_approval" and "expired" in e.reason


async def test_repeated_calls_capped_by_session_limit():
    ctx = _ctx()
    statuses = [(await gateway.invoke(ctx, "inventory.get_stock", {"sku": "SKU-101"})).status for _ in range(12)]
    assert statuses.count("executed") == 8 and statuses[8:] == ["blocked"] * 4


async def test_false_positive_check_benign_operations_allowed():
    ctx = _ctx()
    benign = [("orders.lookup", {"order_id": "ORD-5001"}), ("files.read", {"path": "documents/internal/q3_roadmap.md"}),
              ("files.search", {"query": "return", "directory": "documents/public"}),
              ("db.query", {"table": "products"}), ("web.fetch", {"url": "https://docs.halden.test/shipping-carriers"}),
              ("email.send", {"to": ["team@halden.test"], "subject": "Recap", "body": "Roadmap recap"})]
    for tool, args in benign:
        r = await gateway.invoke(ctx, tool, args)
        assert r.decision == "ALLOW" and r.status == "executed", (tool, r.reason)
