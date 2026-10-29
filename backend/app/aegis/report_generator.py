"""Evidence-based HTML reports.

Report data is assembled from stored records into a Pydantic model and validated
(counts must reconcile, every result must carry evidence) before any HTML is rendered.
The rendered HTML is self-contained and print-friendly (use the browser's Print to PDF).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aegis.metrics import FORMULAS, fmt_metric
from app.aegis.regression_runner import compare_runs
from app.config.settings import SOFTWARE_VERSION
from app.core.timeutil import iso, new_id, utcnow
from app.models import EvaluationMetric, Finding, Investigation, Report, Scenario, ScenarioResult, TestRun, TestSuite

TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))

LIMITATIONS = [
    "Results apply only to the scenarios executed, the agent configuration and the policy version recorded here. They do not show that the agent is secure in general.",
    "All targets were local sandbox fixtures. No external system was tested.",
    "Simulated agents follow a deterministic grammar and model typical failure modes; they are not language models.",
    "SENTINEL enforces policies for tool calls made through supported adapters. Actions outside those adapters are out of scope.",
    "Pattern-based content scanning can miss paraphrased or encoded sensitive data.",
]


class ResultRow(BaseModel):
    scenario_key: str
    name: str
    category: str
    risk: str
    ground_truth: str
    outcome: str
    reason: str
    checks: List[Dict[str, Any]]
    correlation_id: str
    attack_attempted: bool
    attack_succeeded: bool
    latency_ms: Optional[float]


class ReportData(BaseModel):
    title: str
    generated_at: str
    software_version: str
    investigation: Optional[Dict[str, Any]]
    run: Dict[str, Any]
    agent: Dict[str, Any]
    policy: Dict[str, Any]
    counts: Dict[str, int]
    metrics: List[Dict[str, Any]]
    results: List[ResultRow]
    findings: List[Dict[str, Any]]
    comparison: Optional[Dict[str, Any]]
    limitations: List[str]

    @model_validator(mode="after")
    def _reconcile(self) -> "ReportData":
        if self.run.get("status") != "completed":
            raise ValueError("reports can only be generated for completed runs")
        if sum(self.counts.values()) != len(self.results):
            raise ValueError("outcome counts do not reconcile with stored results")
        if len(self.results) != self.run.get("total_scenarios"):
            raise ValueError("number of stored results does not match the run's scenario count")
        for r in self.results:
            if r.outcome in ("PASS", "FAIL") and not r.checks:
                raise ValueError(f"result {r.scenario_key} has no evidence checks")
        return self


def build_report_data(db: Session, run_id: str, investigation: Optional[Investigation] = None) -> ReportData:
    run = db.get(TestRun, run_id)
    if run is None:
        raise LookupError("run not found")
    rows = db.execute(select(ScenarioResult, Scenario).join(Scenario, Scenario.id == ScenarioResult.scenario_id)
                      .where(ScenarioResult.run_id == run_id).order_by(ScenarioResult.started_at)).all()
    results = [ResultRow(
        scenario_key=r.scenario_key, name=s.name, category=s.category, risk=s.risk, ground_truth=s.ground_truth,
        outcome=r.outcome, reason=r.reason, checks=r.checks, correlation_id=r.correlation_id,
        attack_attempted=r.attack_attempted, attack_succeeded=r.attack_succeeded, latency_ms=r.latency_ms,
    ) for r, s in rows]
    counts = {o: sum(1 for x in results if x.outcome == o) for o in ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED")}
    metrics = []
    for m in db.execute(select(EvaluationMetric).where(EvaluationMetric.run_id == run_id)).scalars():
        is_pct = m.metric.endswith("_rate")
        metrics.append({"metric": m.metric, "display": fmt_metric({"value": m.value, "note": m.note}, pct=is_pct) if is_pct
                        else (f"{m.value:.1f}" if m.value is not None else (m.note or "Insufficient data")),
                        "sample_size": m.sample_size, "formula": FORMULAS.get(m.metric, ""),
                        "numerator": m.numerator, "denominator": m.denominator})
    findings = [{"title": f.title, "severity": f.severity, "category": f.category, "status": f.status,
                 "description": f.description, "remediation": f.remediation, "evidence": f.evidence}
                for f in db.execute(select(Finding).where(Finding.run_id == run_id)).scalars()]
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (f["status"] != "open", sev_order.get(f["severity"], 9)))
    comparison = None
    if investigation and investigation.baseline_run_id:
        try:
            comparison = compare_runs(db, investigation.baseline_run_id, run_id)
        except (LookupError, ValueError) as exc:
            comparison = {"error": str(exc)}
    suite = db.get(TestSuite, run.suite_id) if run.suite_id else None
    snap = run.agent_snapshot
    return ReportData(
        title=(investigation.title if investigation else f"Security evaluation: {snap.get('name')}"),
        generated_at=iso(utcnow()) or "", software_version=SOFTWARE_VERSION,
        investigation={"key": investigation.key, "status": investigation.status, "notes": investigation.notes} if investigation else None,
        run={"id": run.id, "label": run.label, "status": run.status, "suite": suite.name if suite else "Custom selection",
             "started_at": iso(run.started_at), "finished_at": iso(run.finished_at), "total_scenarios": run.total_scenarios,
             "software_version": run.software_version},
        agent={"key": snap.get("key"), "name": snap.get("name"), "type": snap.get("agent_type"), "provider": snap.get("provider"),
               "model": snap.get("model_id"), "behavior": snap.get("behavior"), "revision": snap.get("revision"),
               "profile": snap.get("permission_profile", {}).get("key"), "tools": snap.get("enabled_tools")},
        policy={"hash": run.policy_hash[:16], "policies": run.policy_snapshot.get("policies", {})},
        counts=counts, metrics=metrics, results=results, findings=findings, comparison=comparison, limitations=LIMITATIONS,
    )


def generate_report(db: Session, run_id: str, investigation: Optional[Investigation], generated_by: str) -> Report:
    data = build_report_data(db, run_id, investigation)
    payload = data.model_dump()
    data_hash = hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "generated_at"},
                                          sort_keys=True, default=str).encode()).hexdigest()
    html = _env.get_template("report.html").render(d=payload, data_hash=data_hash)
    rep = Report(id=new_id("rep"), investigation_id=investigation.id if investigation else None, run_id=run_id,
                 title=data.title[:200], format="html", data_hash=data_hash, content=html, generated_by=generated_by,
                 generated_at=utcnow())
    db.add(rep)
    db.flush()
    return rep
