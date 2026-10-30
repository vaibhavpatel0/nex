"""Loads built-in scenarios into the database and manages suites.

Built-in scenarios are deterministic templates stored in ``scenarios/builtin.json``.
Seeding is idempotent: a scenario whose definition changed gets its ``version`` bumped,
so results always record which scenario version produced them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.timeutil import new_id
from app.models import Scenario, TestSuite, TestSuiteScenario
from app.seed.catalog import SUITES

BUILTIN_PATH = Path(__file__).resolve().parent / "scenarios" / "builtin.json"
SCENARIO_FILES = [BUILTIN_PATH, Path(__file__).resolve().parent / "scenarios" / "ecommerce.json",
                  Path(__file__).resolve().parent / "scenarios" / "connected.json"]

CATEGORIES = [
    "direct_prompt_injection", "indirect_prompt_injection", "unauthorized_tool_usage", "unauthorized_file_access",
    "sensitive_information_disclosure", "malicious_retrieved_content", "invalid_tool_arguments",
    "repeated_tool_calls", "api_error", "network_timeout", "rate_limit_error", "malformed_model_output",
    "permission_escalation", "regression", "model_timeout", "inventory_race", "normal_operation",
]
ALLOWED_EXPECTATION_KEYS = {
    "prohibited_calls", "permitted_calls", "approval_calls", "must_attempt", "must_execute", "expected_failure",
    "max_reaching_tool", "output_must_not_match", "output_must_match_any", "output_no_markers",
    "final_output_missing_recorded", "approval_flow", "expected_decisions",
    "expected_agent_error", "side_effects", "tools_not_invoked", "invariants_hold",
}
ALLOWED_SETUP_KEYS = {"session_subject", "faults", "model_fault", "approval"}


def _fingerprint(d: Dict[str, Any]) -> str:
    keys = ("name", "category", "use_case", "purpose", "risk", "ground_truth", "setup", "input", "expected", "required_tools")
    return hashlib.sha256(json.dumps({k: d.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def validate_definition(d: Dict[str, Any]) -> List[str]:
    """Structural validation used for built-in and custom scenarios before they can run."""
    errors = []
    for k in ("key", "name", "category", "purpose", "risk", "ground_truth", "input", "expected"):
        if k not in d:
            errors.append(f"missing {k}")
    if d.get("category") not in CATEGORIES:
        errors.append("unknown category")
    if d.get("ground_truth") not in ("malicious", "benign", "fault"):
        errors.append("ground_truth must be malicious, benign or fault")
    if not isinstance(d.get("input", {}).get("task"), str) or not d["input"]["task"].strip():
        errors.append("input.task must be a non-empty string")
    elif len(d["input"]["task"]) > 2000:
        errors.append("input.task is limited to 2000 characters")
    bad = set(d.get("expected", {})) - ALLOWED_EXPECTATION_KEYS
    if bad:
        errors.append("unknown expectation keys: " + ", ".join(sorted(bad)))
    bad = set(d.get("setup", {})) - ALLOWED_SETUP_KEYS
    if bad:
        errors.append("unknown setup keys: " + ", ".join(sorted(bad)))
    if not d.get("expected"):
        errors.append("expected must define at least one check")
    return errors


def seed_scenarios(db: Session) -> int:
    for s in SUITES:
        row = db.execute(select(TestSuite).where(TestSuite.key == s["key"])).scalar_one_or_none()
        if row is None:
            db.add(TestSuite(id=new_id("sui"), key=s["key"], name=s["name"], description=s["description"]))
        else:  # keep built-in suite names and descriptions current
            row.name, row.description = s["name"], s["description"]
    db.flush()
    suites = {s.key: s for s in db.execute(select(TestSuite)).scalars()}
    data = [d for f in SCENARIO_FILES for d in json.loads(f.read_text())["scenarios"]]
    count = 0
    for pos, d in enumerate(data):
        errs = validate_definition(d)
        if errs:
            raise ValueError(f"Built-in scenario {d.get('key')} is invalid: {errs}")
        fp = _fingerprint(d)
        row = db.execute(select(Scenario).where(Scenario.key == d["key"])).scalar_one_or_none()
        fields = dict(
            name=d["name"], category=d["category"], use_case=d.get("use_case", "general"), purpose=d["purpose"],
            risk=d["risk"], ground_truth=d["ground_truth"], setup=d.get("setup", {}), input=d["input"],
            expected=d["expected"], required_tools=d.get("required_tools", []), origin="builtin",
        )
        if row is None:
            row = Scenario(id=new_id("scn"), key=d["key"], version=1, enabled=True, **fields)
            row.setup = {**row.setup, "_fp": fp}
            db.add(row)
            count += 1
        elif row.setup.get("_fp") != fp:
            for k, v in fields.items():
                setattr(row, k, v)
            row.setup = {**fields["setup"], "_fp": fp}
            row.version += 1
        db.flush()
        for skey in d.get("suites", []):
            suite = suites[skey]
            exists = db.get(TestSuiteScenario, (suite.id, row.id))
            if exists is None:
                db.add(TestSuiteScenario(suite_id=suite.id, scenario_id=row.id, position=pos))
    db.flush()
    return count


def scenario_setup(s: Scenario) -> Dict[str, Any]:
    return {k: v for k, v in (s.setup or {}).items() if not k.startswith("_")}


def scenarios_for_suite(db: Session, suite_key: str) -> List[Scenario]:
    suite = db.execute(select(TestSuite).where(TestSuite.key == suite_key)).scalar_one_or_none()
    if suite is None:
        return []
    rows = db.execute(
        select(Scenario).join(TestSuiteScenario, TestSuiteScenario.scenario_id == Scenario.id)
        .where(TestSuiteScenario.suite_id == suite.id).order_by(TestSuiteScenario.position, Scenario.key)
    ).scalars().all()
    return list(rows)


def add_to_regression(db: Session, scenario: Scenario) -> bool:
    suite = db.execute(select(TestSuite).where(TestSuite.key == "regression")).scalar_one()
    if db.get(TestSuiteScenario, (suite.id, scenario.id)) is not None:
        return False
    db.add(TestSuiteScenario(suite_id=suite.id, scenario_id=scenario.id, position=999))
    return True


def remove_from_regression(db: Session, scenario: Scenario) -> None:
    suite = db.execute(select(TestSuite).where(TestSuite.key == "regression")).scalar_one()
    db.execute(delete(TestSuiteScenario).where(
        TestSuiteScenario.suite_id == suite.id, TestSuiteScenario.scenario_id == scenario.id))


def find_scenario(db: Session, key: str) -> Optional[Scenario]:
    return db.execute(select(Scenario).where(Scenario.key == key)).scalar_one_or_none()
