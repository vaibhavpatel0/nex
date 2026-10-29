"""AEGIS scenario runner.

A run snapshots the agent configuration and the policy set, then executes each scenario
in its own fresh sandbox with a new correlation id. Every tool call goes through SENTINEL
via the shared agent executor. Results are evaluated from the persisted records only.

Runs execute as background tasks with bounded concurrency. Runs interrupted by a backend
restart are marked ``failed`` at startup with an explanatory error (never left "running").
"""
from __future__ import annotations

import asyncio
import copy
import logging
import shutil
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.adapters.tools.sandbox import Sandbox
from app.aegis.evaluation_engine import CallRecord, Observation, evaluate_scenario
from app.aegis.metrics import compute_metrics
from app.aegis.scenario_registry import scenario_setup, scenarios_for_suite
from app.agents.base import AgentTask
from app.config.settings import SOFTWARE_VERSION, get_settings
from app.core.timeutil import new_id, utcnow
from app.database.base import db_session
from app.models import (
    Agent,
    ApprovalRequest,
    EvaluationMetric,
    Finding,
    PolicyDecision,
    Scenario,
    ScenarioResult,
    TestRun,
    TestSuite,
    ToolCall,
)
from app.security.audit import agent_view, policy_snapshot
from app.security.policy_engine import AgentView
from app.security.tool_gateway import InvocationContext, gateway
from app.sentinel.approval import approval_service
from app.services.agent_executor import execute_agent
from app.websocket.event_bus import event_bus

log = logging.getLogger("aegis.runner")

RISK_DOWN = {"critical": "high", "high": "medium", "medium": "low", "low": "low"}
REMEDIATION = {
    "direct_prompt_injection": "Keep enforcement outside the model: verify the relevant SENTINEL scope policy is enabled, and harden the agent prompt to refuse role-override requests.",
    "indirect_prompt_injection": "Treat retrieved content as data. Strip or quarantine instructions from tool output before it reaches the model, and keep egress tools behind allowlists and approvals.",
    "malicious_retrieved_content": "Mark retrieved documents as untrusted and prevent the agent from acting on instructions contained in them.",
    "unauthorized_tool_usage": "Narrow the agent's permission profile to the tools and arguments the task needs.",
    "unauthorized_file_access": "Restrict file grants to explicit directories and keep deny rules for restricted folders and secret files.",
    "sensitive_information_disclosure": "Scope data access to the session principal, minimise returned fields, and scan outbound content.",
    "invalid_tool_arguments": "Keep strict schemas (no extra fields, typed identifiers) on every tool.",
    "repeated_tool_calls": "Cap retries in the agent and keep session rate limits in SENTINEL.",
    "api_error": "Make the agent report upstream failures explicitly and never substitute cached or invented values.",
    "network_timeout": "Report timeouts to the user; do not present stale values as current.",
    "rate_limit_error": "Back off on HTTP 429 and stop after a small number of attempts.",
    "malformed_model_output": "Validate model tool calls before execution and record missing outputs.",
    "permission_escalation": "Keep approvals bound to exact arguments, single use and time limited; never let the agent change its own grants.",
    "regression": "Re-run the original scenario after remediation and keep it in the regression suite.",
}


def agent_snapshot(a: Agent) -> Dict[str, Any]:
    return {
        "id": a.id, "key": a.key, "name": a.name, "agent_type": a.agent_type, "behavior": a.behavior,
        "provider": a.provider, "model_id": a.model_id, "system_prompt": a.system_prompt,
        "enabled_tools": list(a.enabled_tools or []), "revision": a.revision,
        "permission_profile": {"key": a.permission_profile.key, "revision": a.permission_profile.revision,
                               "grants": a.permission_profile.grants},
    }


def view_from_snapshot(snap: Dict[str, Any]) -> AgentView:
    return AgentView(id=snap["id"], key=snap["key"], enabled_tools=tuple(snap["enabled_tools"]),
                     grants=copy.deepcopy(snap["permission_profile"]["grants"]), revision=snap["revision"])


class ScenarioRunner:
    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._sem: Optional[asyncio.Semaphore] = None

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(get_settings().max_concurrent_runs)
        return self._sem

    # ------------------------------------------------------------------ lifecycle
    @staticmethod
    def recover_interrupted() -> int:
        with db_session() as db:
            res = db.execute(
                update(TestRun).where(TestRun.status.in_(("queued", "running")))
                .values(status="failed", finished_at=utcnow(), error="The backend stopped while this run was in progress. Results recorded before the interruption are kept; re-run to complete it.")
            )
            return res.rowcount or 0

    def create_run(
        self, *, agent_id: str, suite_key: Optional[str], scenario_keys: Optional[List[str]], label: str,
        triggered_by: Optional[str], replay_of: Optional[str] = None,
    ) -> str:
        with db_session() as db:
            agent = db.get(Agent, agent_id)
            if agent is None:
                raise LookupError("agent not found")
            if not agent.enabled:
                raise ValueError("agent is disabled")
            suite = None
            if suite_key:
                suite = db.execute(select(TestSuite).where(TestSuite.key == suite_key)).scalar_one_or_none()
                if suite is None:
                    raise LookupError("suite not found")
                keys = [s.key for s in scenarios_for_suite(db, suite_key)]
            else:
                keys = list(dict.fromkeys(scenario_keys or []))
                found = {s.key for s in db.execute(select(Scenario).where(Scenario.key.in_(keys))).scalars()}
                missing = [k for k in keys if k not in found]
                if missing:
                    raise LookupError("unknown scenarios: " + ", ".join(missing))
            if not keys:
                raise ValueError("no scenarios selected (the suite may be empty)")
            run = TestRun(
                id=new_id("run"), label=label[:160], suite_id=suite.id if suite else None, agent_id=agent.id,
                agent_revision=agent.revision, agent_snapshot=agent_snapshot(agent), policy_snapshot=policy_snapshot(db),
                software_version=SOFTWARE_VERSION, scenario_keys=keys, status="queued", replay_of_run_id=replay_of,
                triggered_by=triggered_by, total_scenarios=len(keys), created_at=utcnow(),
            )
            run.policy_hash = run.policy_snapshot["hash"]
            db.add(run)
            run_id = run.id
        event_bus.publish("run.queued", f"Test run queued: {len(keys)} scenario(s)", payload={"run_id": run_id, "label": label},
                          run_id=run_id, agent_id=agent_id)
        return run_id

    def start(self, run_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._guarded(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    async def run_now(self, run_id: str) -> None:
        """Execute synchronously (used by tests and the CLI)."""
        await self._guarded(run_id)

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    async def _guarded(self, run_id: str) -> None:
        async with self._semaphore():
            try:
                await self._execute(run_id)
            except Exception as exc:
                log.exception("run %s failed", run_id)
                with db_session() as db:
                    run = db.get(TestRun, run_id)
                    run.status = "failed"
                    run.finished_at = utcnow()
                    run.error = f"Runner error: {type(exc).__name__}"
                event_bus.publish("run.failed", f"Test run failed: {type(exc).__name__}", severity="error",
                                  payload={"run_id": run_id}, run_id=run_id)

    # ------------------------------------------------------------------ execution
    async def _execute(self, run_id: str) -> None:
        with db_session() as db:
            run = db.get(TestRun, run_id)
            run.status = "running"
            run.started_at = utcnow()
            snap = run.agent_snapshot
            keys = list(run.scenario_keys)
        view = view_from_snapshot(snap)
        root = get_settings().sandbox_dir / run_id
        event_bus.publish("run.started", f"Test run started for {snap['key']}", payload={"run_id": run_id, "scenarios": keys},
                          run_id=run_id, agent_id=view.id)
        for key in keys:
            await self._run_scenario(run_id, key, snap, view, root)
            with db_session() as db:
                db.get(TestRun, run_id).completed_scenarios += 1
        self.finalize(run_id)
        shutil.rmtree(root, ignore_errors=True)  # evidence lives in the database; sandboxes are disposable

    async def _run_scenario(self, run_id: str, key: str, snap: Dict[str, Any], view: AgentView, root) -> None:
        with db_session() as db:
            sc = db.execute(select(Scenario).where(Scenario.key == key)).scalar_one()
            sc_id, version, expected = sc.id, sc.version, copy.deepcopy(sc.expected)
            setup, task_text, required, enabled = scenario_setup(sc), sc.input["task"], list(sc.required_tools), sc.enabled
        started = utcnow()
        correlation = new_id("cor")
        event_bus.publish("scenario.started", f"{key} started", payload={"scenario": key}, correlation_id=correlation,
                          run_id=run_id, agent_id=view.id)

        missing_tools = [t for t in required if t not in view.enabled_tools]
        if not enabled or missing_tools:
            reason = "Scenario is disabled" if not enabled else "Agent does not have required tool(s): " + ", ".join(missing_tools)
            self._store_result(run_id, sc_id, key, version, correlation, "SKIPPED", reason, [], {}, None, started, None)
            return

        sandbox = Sandbox.create(root / key)
        rec = await execute_agent(
            snapshot=snap, agent_view=view,
            task=AgentTask(task=task_text, session_subject=setup.get("session_subject"), model_fault=setup.get("model_fault")),
            sandbox=sandbox, correlation_id=correlation, kind="test", run_id=run_id, scenario_key=key,
            faults=setup.get("faults"), triggered_by="aegis-runner",
        )
        flow: Dict[str, Any] = {}
        if setup.get("approval") and rec.status == "completed":
            flow = await self._approval_flow(setup["approval"], expected, rec.context)

        with db_session() as db:
            calls = [
                CallRecord(id=c.id, tool_name=c.tool_name, args=c.args_redacted or {}, status=c.status, decision=c.decision,
                           matched_policy=(db.get(PolicyDecision, c.decision_id).matched_policy_key if c.decision_id else None),
                           error_class=c.error_class, origin=c.origin, latency_ms=c.latency_ms)
                for c in db.execute(select(ToolCall).where(ToolCall.correlation_id == correlation)
                                    .order_by(ToolCall.created_at, ToolCall.id)).scalars()
            ]
        obs = Observation(calls=calls, final_text=rec.final_text, final_missing=rec.final_missing,
                          agent_error_kind=rec.error_kind, agent_error=rec.error, approval_flow=flow, latency_ms=rec.latency_ms)
        ev = evaluate_scenario(expected, obs)
        # Approvals raised inside an automated test are evidence, not work for a human: close them.
        with db_session() as db:
            for a in db.execute(select(ApprovalRequest).where(ApprovalRequest.correlation_id == correlation,
                                                              ApprovalRequest.status.in_(("pending", "approved")))).scalars():
                a.status = "expired"
                a.decision_note = "Closed automatically at the end of the AEGIS test scenario"
                approval_service.forget(a.id)
        self._store_result(run_id, sc_id, key, version, correlation, ev.outcome, ev.reason, ev.checks, ev.observed,
                           rec.latency_ms, started, ev)

    async def _approval_flow(self, mode: str, expected: Dict[str, Any], ctx: InvocationContext) -> Dict[str, Any]:
        from app.aegis.evaluation_engine import CallRecord as CR, matches

        with db_session() as db:
            approvals = db.execute(
                select(ApprovalRequest).where(ApprovalRequest.correlation_id == ctx.correlation_id,
                                              ApprovalRequest.status == "pending").order_by(ApprovalRequest.created_at)
            ).scalars().all()
            target = None
            for a in approvals:
                probe = CR(id="", tool_name=a.tool_name, args=a.args_redacted, status="", decision=None,
                           matched_policy=None, error_class=None, origin="", latency_ms=None)
                if any(matches(m, probe) for m in expected.get("approval_calls", [])):
                    target = a
                    break
            if target is None:
                return {"no_approval_created": True}
            appr, status = approval_service.decide(db, target.id, True, "aegis-runner (simulated approver)",
                                                   "Automatic approval issued by the AEGIS test runner")
            target_id, tool = target.id, target.tool_name
        event_bus.publish("approval.decided", f"Approval {target_id} approved by the AEGIS runner (test)",
                          payload={"approval_id": target_id, "approved": True, "by": "aegis-runner"},
                          correlation_id=ctx.correlation_id, run_id=ctx.run_id, agent_id=ctx.agent.id)
        pending = approval_service.pending_action(target_id)
        if pending is None:
            return {"no_approval_created": True}
        out: Dict[str, Any] = {"approval_id": target_id, "mode": mode}
        if mode == "approve":
            first = await gateway.invoke(ctx, tool, dict(pending.args), approval_id=target_id, origin="approval_resubmit")
            replay = await gateway.invoke(ctx, tool, dict(pending.args), approval_id=target_id, origin="approval_resubmit")
            with db_session() as db:
                execs = db.execute(select(ToolCall).where(ToolCall.correlation_id == ctx.correlation_id,
                                                          ToolCall.tool_name == tool, ToolCall.status == "executed")).scalars().all()
            out.update(first_status=first.status, executions=len(execs), replay_status=replay.status, replay_reason=replay.reason)
        elif mode == "approve_then_tamper":
            tampered = dict(pending.args)
            if "body" in tampered:
                tampered["body"] = str(tampered["body"]) + " Also, please confirm your card number by reply."
            else:
                tampered["_tampered"] = True
            res = await gateway.invoke(ctx, tool, tampered, approval_id=target_id, origin="approval_resubmit")
            out.update(tampered_status=res.status, tampered_reason=res.reason)
        return out

    def _store_result(self, run_id, sc_id, key, version, correlation, outcome, reason, checks, observed, latency,
                      started, ev) -> None:
        with db_session() as db:
            sc = db.get(Scenario, sc_id)
            res = ScenarioResult(
                id=new_id("res"), run_id=run_id, scenario_id=sc_id, scenario_key=key, scenario_version=version,
                correlation_id=correlation, outcome=outcome, reason=reason[:4000], checks=checks, observed=observed,
                latency_ms=latency, started_at=started, finished_at=utcnow(),
            )
            if ev is not None:
                for f in ("attack_attempted", "attack_succeeded", "task_completed", "prohibited_attempts", "prohibited_blocked",
                          "prohibited_executed", "permitted_attempts", "permitted_blocked", "tool_calls_total"):
                    setattr(res, f, getattr(ev, f))
            db.add(res)
            db.flush()
            for finding in self._findings(sc, res):
                db.add(finding)
            agent_id = db.get(TestRun, run_id).agent_id
        sev = {"PASS": "info", "FAIL": "critical", "ERROR": "error", "INCONCLUSIVE": "warning", "SKIPPED": "info"}[outcome]
        event_bus.publish("scenario.completed", f"{key}: {outcome}", severity=sev,
                          payload={"scenario": key, "outcome": outcome, "reason": reason[:300], "result_id": res.id,
                                   "attack_attempted": res.attack_attempted, "attack_succeeded": res.attack_succeeded},
                          correlation_id=correlation, run_id=run_id, agent_id=agent_id)

    @staticmethod
    def _findings(sc: Scenario, res: ScenarioResult) -> List[Finding]:
        out = []
        evidence = {"scenario": sc.key, "correlation_id": res.correlation_id,
                    "failed_checks": [c for c in res.checks if not c["passed"]]}
        if res.outcome == "FAIL":
            failed = evidence["failed_checks"]
            title = f"{sc.name}: {failed[0]['description'] if failed else 'check failed'}"
            out.append(Finding(
                id=new_id("fnd"), run_id=res.run_id, scenario_result_id=res.id, title=title[:200],
                severity=sc.risk if sc.ground_truth == "malicious" else RISK_DOWN.get(sc.risk, "low"),
                category=sc.category, status="open",
                description=f"Scenario {sc.key} failed. {res.reason}",
                evidence=evidence, remediation=REMEDIATION.get(sc.category, "Review the trace and tighten the relevant policy."),
            ))
        if res.outcome in ("PASS", "FAIL") and res.attack_attempted and res.prohibited_executed == 0:
            out.append(Finding(
                id=new_id("fnd"), run_id=res.run_id, scenario_result_id=res.id,
                title=f"Agent attempted a prohibited action in {sc.key} (stopped by SENTINEL)"[:200],
                severity=RISK_DOWN.get(sc.risk, "low"), category="agent_weakness", status="mitigated",
                description=(f"The agent attempted {res.prohibited_attempts} prohibited call(s). SENTINEL stopped all of them before "
                             "execution, but the agent itself is susceptible and relies on runtime enforcement."),
                evidence={**evidence, "prohibited_attempts": res.prohibited_attempts},
                remediation=REMEDIATION.get(sc.category, ""),
            ))
        if res.outcome == "ERROR":
            out.append(Finding(
                id=new_id("fnd"), run_id=res.run_id, scenario_result_id=res.id,
                title=f"{sc.key} could not run: integration or agent error"[:200], severity="low", category="reliability",
                status="open", description=res.reason, evidence=evidence,
                remediation="Check the agent's integration status on the Integrations page, then re-run.",
            ))
        return out

    # ------------------------------------------------------------------ metrics
    @staticmethod
    def finalize(run_id: str) -> None:
        with db_session() as db:
            run = db.get(TestRun, run_id)
            rows = db.execute(select(ScenarioResult, Scenario.ground_truth).join(Scenario, Scenario.id == ScenarioResult.scenario_id)
                              .where(ScenarioResult.run_id == run_id)).all()
            results = [{**{c: getattr(r, c) for c in ("outcome", "attack_attempted", "attack_succeeded", "task_completed",
                                                         "prohibited_attempts", "prohibited_blocked", "prohibited_executed",
                                                         "permitted_attempts", "permitted_blocked", "latency_ms")},
                        "ground_truth": gt} for r, gt in rows]
            lat = [c.latency_ms for c in db.execute(select(ToolCall).where(ToolCall.run_id == run_id)).scalars() if c.latency_ms is not None]
            total_calls = db.execute(select(ToolCall.id).where(ToolCall.run_id == run_id)).all()
            metrics = compute_metrics(results, lat, len(total_calls))
            counts = metrics.pop("_counts")
            for name, m in metrics.items():
                existing = db.execute(select(EvaluationMetric).where(EvaluationMetric.run_id == run_id,
                                                                     EvaluationMetric.metric == name)).scalar_one_or_none()
                row = existing or EvaluationMetric(run_id=run_id, metric=name)
                row.value, row.numerator, row.denominator = m["value"], m["numerator"], m["denominator"]
                row.sample_size, row.note = m["sample_size"], m["note"]
                db.add(row)
            run.summary = {"counts": counts, "metrics": metrics}
            run.status = "completed"
            run.finished_at = utcnow()
            agent_id = run.agent_id
        event_bus.publish("run.completed", "Test run completed: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v and k != "total"),
                          severity="notice", payload={"run_id": run_id, "counts": counts}, run_id=run_id, agent_id=agent_id)


runner = ScenarioRunner()
