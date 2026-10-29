"""Regression comparison between two completed runs.

Comparisons are made per scenario key on actual stored results. Conditions that make a
comparison less reliable (different scenario versions, policy set, software version,
small sample) are reported as warnings rather than hidden.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvaluationMetric, ScenarioResult, TestRun

SMALL_SAMPLE = 10
METRICS = ("pass_rate", "attack_attempt_rate", "attack_success_rate", "block_rate", "false_positive_rate",
           "false_negative_rate", "task_completion_rate", "error_rate", "scenario_latency_p50_ms", "tool_calls_total")


def _metrics(db: Session, run_id: str) -> Dict[str, Dict[str, Any]]:
    return {m.metric: {"value": m.value, "sample_size": m.sample_size, "note": m.note}
            for m in db.execute(select(EvaluationMetric).where(EvaluationMetric.run_id == run_id)).scalars()}


def compare_runs(db: Session, baseline_id: str, candidate_id: str) -> Dict[str, Any]:
    a, b = db.get(TestRun, baseline_id), db.get(TestRun, candidate_id)
    if a is None or b is None:
        raise LookupError("run not found")
    if a.status != "completed" or b.status != "completed":
        raise ValueError("both runs must be completed")
    ra = {r.scenario_key: r for r in db.execute(select(ScenarioResult).where(ScenarioResult.run_id == a.id)).scalars()}
    rb = {r.scenario_key: r for r in db.execute(select(ScenarioResult).where(ScenarioResult.run_id == b.id)).scalars()}
    common = sorted(set(ra) & set(rb))
    warnings: List[str] = []
    if set(ra) != set(rb):
        warnings.append(f"Runs cover different scenarios; only {len(common)} shared scenario(s) are compared.")
    version_diff = [k for k in common if ra[k].scenario_version != rb[k].scenario_version]
    if version_diff:
        warnings.append("Scenario definitions changed between runs: " + ", ".join(version_diff))
    if a.mode != b.mode:
        warnings.append(f"Comparing a {a.mode} run with a {b.mode} run: in a baseline run SENTINEL records decisions but does "
                        "not enforce them, so differences show the effect of enforcement, not of the agent.")
    if a.policy_hash != b.policy_hash:
        warnings.append("The SENTINEL policy set differs between runs, so differences may come from policy changes rather than the agent.")
    if a.software_version != b.software_version:
        warnings.append(f"Platform version differs ({a.software_version} vs {b.software_version}).")
    if len(common) < SMALL_SAMPLE:
        warnings.append(f"Only {len(common)} shared scenario(s). This is too small to conclude that one configuration is safer in general.")

    rows = []
    for k in common:
        x, y = ra[k], rb[k]
        rows.append({
            "scenario_key": k,
            "baseline": {"outcome": x.outcome, "attack_attempted": x.attack_attempted, "attack_succeeded": x.attack_succeeded,
                         "prohibited_attempts": x.prohibited_attempts, "latency_ms": x.latency_ms, "result_id": x.id},
            "candidate": {"outcome": y.outcome, "attack_attempted": y.attack_attempted, "attack_succeeded": y.attack_succeeded,
                          "prohibited_attempts": y.prohibited_attempts, "latency_ms": y.latency_ms, "result_id": y.id},
            "changed": (x.outcome != y.outcome) or (x.attack_attempted != y.attack_attempted),
            "direction": ("improved" if (x.outcome != "PASS" and y.outcome == "PASS") or (x.attack_attempted and not y.attack_attempted)
                          else "regressed" if (x.outcome == "PASS" and y.outcome != "PASS") or (not x.attack_attempted and y.attack_attempted)
                          else "unchanged"),
        })
    ma, mb = _metrics(db, a.id), _metrics(db, b.id)
    metric_rows = []
    for name in METRICS:
        va, vb = ma.get(name, {}), mb.get(name, {})
        delta = None
        if va.get("value") is not None and vb.get("value") is not None:
            delta = vb["value"] - va["value"]
        metric_rows.append({"metric": name, "baseline": va, "candidate": vb, "delta": delta})
    return {
        "baseline": {"id": a.id, "label": a.label, "mode": a.mode, "agent": a.agent_snapshot.get("key"), "agent_revision": a.agent_revision,
                     "policy_hash": a.policy_hash[:12], "software_version": a.software_version},
        "candidate": {"id": b.id, "label": b.label, "mode": b.mode, "agent": b.agent_snapshot.get("key"), "agent_revision": b.agent_revision,
                      "policy_hash": b.policy_hash[:12], "software_version": b.software_version},
        "shared_scenarios": len(common),
        "scenarios": rows,
        "metrics": metric_rows,
        "summary": {
            "improved": sum(1 for r in rows if r["direction"] == "improved"),
            "regressed": sum(1 for r in rows if r["direction"] == "regressed"),
            "unchanged": sum(1 for r in rows if r["direction"] == "unchanged"),
        },
        "warnings": warnings,
    }
