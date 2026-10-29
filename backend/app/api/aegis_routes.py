from __future__ import annotations

from app.database.base import CommitBeforeResponseRoute

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.aegis.metrics import FORMULAS, compute_metrics
from app.aegis.regression_runner import compare_runs
from app.aegis.report_generator import generate_report
from app.aegis.scenario_registry import CATEGORIES, add_to_regression, find_scenario, remove_from_regression, validate_definition
from app.aegis.scenario_runner import runner
from app.api.common import Page, clean_setup, get_or_404, page_body, paginate
from app.core.timeutil import iso, new_id, utcnow
from app.database.base import get_db
from app.models import (
    Agent, EvaluationMetric, Finding, Investigation, Report, Scenario, ScenarioResult, TestRun, TestSuite,
    TestSuiteScenario, ToolCall, model_to_dict,
)
from app.security.authentication import Principal
from app.security.authorization import require

router = APIRouter(route_class=CommitBeforeResponseRoute, tags=["aegis"])


# ------------------------------------------------------------------ suites & scenarios
def scenario_out(s: Scenario, suites: Optional[List[str]] = None) -> dict:
    d = model_to_dict(s)
    d["setup"] = clean_setup(s.setup)
    if suites is not None:
        d["suites"] = suites
    return d


@router.get("/test-suites")
def list_suites(p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(TestSuiteScenario.suite_id, func.count()).group_by(TestSuiteScenario.suite_id)).all())
    return {"items": [{**model_to_dict(s), "scenario_count": counts.get(s.id, 0)}
                      for s in db.execute(select(TestSuite).order_by(TestSuite.key)).scalars()]}


@router.get("/scenarios")
def list_scenarios(category: Optional[str] = None, suite: Optional[str] = None, p: Principal = Depends(require("read")),
                   db: Session = Depends(get_db)):
    stmt = select(Scenario).order_by(Scenario.key)
    if category:
        stmt = stmt.where(Scenario.category == category)
    if suite:
        stmt = stmt.join(TestSuiteScenario, TestSuiteScenario.scenario_id == Scenario.id).join(
            TestSuite, TestSuite.id == TestSuiteScenario.suite_id).where(TestSuite.key == suite)
    membership: Dict[str, List[str]] = {}
    for sid, key in db.execute(select(TestSuiteScenario.scenario_id, TestSuite.key).join(TestSuite, TestSuite.id == TestSuiteScenario.suite_id)):
        membership.setdefault(sid, []).append(key)
    return {"items": [scenario_out(s, sorted(membership.get(s.id, []))) for s in db.execute(stmt).scalars()],
            "categories": CATEGORIES}


@router.get("/scenarios/{key}")
def get_scenario(key: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    s = find_scenario(db, key)
    if s is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    history = db.execute(select(ScenarioResult).where(ScenarioResult.scenario_key == key)
                         .order_by(ScenarioResult.started_at.desc()).limit(20)).scalars()
    return {**scenario_out(s), "recent_results": [{"id": r.id, "run_id": r.run_id, "outcome": r.outcome,
                                                   "started_at": iso(r.started_at)} for r in history]}


class ScenarioIn(BaseModel):
    key: str = Field(pattern=r"^CUS-[A-Z0-9-]{1,20}$")
    name: str = Field(min_length=3, max_length=160)
    category: str
    use_case: str = Field(default="custom", max_length=48)
    purpose: str = Field(min_length=3, max_length=2000)
    risk: str = Field(pattern=r"^(low|medium|high|critical)$")
    ground_truth: str = Field(pattern=r"^(malicious|benign|fault)$")
    setup: Dict[str, Any] = Field(default_factory=dict)
    input: Dict[str, Any]
    expected: Dict[str, Any]
    required_tools: List[str] = Field(default_factory=list, max_length=12)


@router.post("/scenarios", status_code=201)
def create_scenario(body: ScenarioIn, p: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)):
    d = body.model_dump()
    errors = validate_definition(d)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    if find_scenario(db, body.key):
        raise HTTPException(status_code=409, detail="Scenario key exists")
    s = Scenario(id=new_id("scn"), origin="custom", enabled=True, version=1, **d)
    db.add(s)
    db.flush()
    return scenario_out(s)


class ScenarioPatch(BaseModel):
    enabled: Optional[bool] = None
    regression: Optional[bool] = None


@router.patch("/scenarios/{key}")
def patch_scenario(key: str, body: ScenarioPatch, p: Principal = Depends(require("tests:run")), db: Session = Depends(get_db)):
    s = find_scenario(db, key)
    if s is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if body.enabled is not None:
        s.enabled = body.enabled
    if body.regression is True:
        add_to_regression(db, s)
    elif body.regression is False:
        remove_from_regression(db, s)
    return scenario_out(s)


# ------------------------------------------------------------------ test runs
class RunIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=40)
    suite_key: Optional[str] = Field(default=None, max_length=64)
    scenario_keys: Optional[List[str]] = Field(default=None, max_length=200)
    label: str = Field(default="", max_length=160)
    mode: str = Field(default="protected", pattern=r"^(protected|baseline)$")


def run_out(r: TestRun) -> dict:
    d = model_to_dict(r, exclude=("agent_snapshot", "policy_snapshot"))
    d["agent"] = {"id": r.agent_id, "key": r.agent_snapshot.get("key"), "name": r.agent_snapshot.get("name"),
                  "agent_type": r.agent_snapshot.get("agent_type"), "behavior": r.agent_snapshot.get("behavior"),
                  "model_id": r.agent_snapshot.get("model_id")}
    d["suite_key"] = r.suite.key if r.suite else None
    d["policy_hash_short"] = r.policy_hash[:12]
    d["active"] = runner.is_active(r.id)
    return d


def _start(db_body: RunIn, who: str, replay_of: Optional[str] = None) -> str:
    if not db_body.suite_key and not db_body.scenario_keys:
        raise HTTPException(status_code=422, detail="Choose a suite or at least one scenario")
    try:
        run_id = runner.create_run(agent_id=db_body.agent_id, suite_key=db_body.suite_key, scenario_keys=db_body.scenario_keys,
                                   label=db_body.label or "Security test run", triggered_by=who, replay_of=replay_of,
                                   mode=db_body.mode)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    runner.start(run_id)
    return run_id


@router.post("/test-runs", status_code=202)
async def create_run(body: RunIn, p: Principal = Depends(require("tests:run")), db: Session = Depends(get_db)):
    run_id = _start(body, p.username)
    return {"run_id": run_id, "status": "queued", "progress_url": f"/api/v1/test-runs/{run_id}"}


class ReplayIn(BaseModel):
    agent_id: Optional[str] = Field(default=None, max_length=40)
    label: str = Field(default="", max_length=160)


@router.post("/test-runs/{run_id}/replay", status_code=202)
async def replay_run(run_id: str, body: ReplayIn, p: Principal = Depends(require("tests:run")), db: Session = Depends(get_db)):
    src = get_or_404(db, TestRun, run_id, "Run")
    agent_id = body.agent_id or src.agent_id
    label = body.label or f"Replay of {src.label or src.id}"
    new = _start(RunIn(agent_id=agent_id, scenario_keys=list(src.scenario_keys), label=label, mode=src.mode), p.username,
                 replay_of=src.id)
    return {"run_id": new, "status": "queued", "replay_of": src.id}


@router.get("/test-runs")
def list_runs(agent_id: Optional[str] = None, status: Optional[str] = None, page: Page = Depends(),
              p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    stmt = select(TestRun).order_by(TestRun.created_at.desc())
    if agent_id:
        stmt = stmt.where(TestRun.agent_id == agent_id)
    if status:
        stmt = stmt.where(TestRun.status == status)
    items, total = paginate(db, stmt, page)
    return page_body([run_out(r) for r in items], total, page)


@router.get("/test-runs/compare")
def compare(baseline: str, candidate: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    try:
        return compare_runs(db, baseline, candidate)
    except LookupError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def result_out(r: ScenarioResult, s: Scenario) -> dict:
    d = model_to_dict(r)
    d["scenario"] = {"key": s.key, "name": s.name, "category": s.category, "use_case": s.use_case, "risk": s.risk,
                     "ground_truth": s.ground_truth, "purpose": s.purpose}
    return d


@router.get("/test-runs/{run_id}")
def get_run(run_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    r = get_or_404(db, TestRun, run_id, "Run")
    rows = db.execute(select(ScenarioResult, Scenario).join(Scenario, Scenario.id == ScenarioResult.scenario_id)
                      .where(ScenarioResult.run_id == run_id).order_by(ScenarioResult.started_at)).all()
    metrics = [model_to_dict(m) for m in db.execute(select(EvaluationMetric).where(EvaluationMetric.run_id == run_id)).scalars()]
    for m in metrics:
        m["formula"] = FORMULAS.get(m["metric"], "")
    findings = [model_to_dict(f) for f in db.execute(select(Finding).where(Finding.run_id == run_id)).scalars()]
    return {**run_out(r), "agent_snapshot": r.agent_snapshot, "policy_snapshot": r.policy_snapshot,
            "results": [result_out(x, s) for x, s in rows], "metrics": metrics, "findings": findings}


@router.get("/findings")
def list_findings(run_id: Optional[str] = None, status: Optional[str] = None, page: Page = Depends(),
                  p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    stmt = select(Finding).order_by(Finding.created_at.desc())
    if run_id:
        stmt = stmt.where(Finding.run_id == run_id)
    if status:
        stmt = stmt.where(Finding.status == status)
    items, total = paginate(db, stmt, page)
    return page_body([model_to_dict(f) for f in items], total, page)


# ------------------------------------------------------------------ investigations
class InvestigationIn(BaseModel):
    run_id: str = Field(max_length=40)
    baseline_run_id: Optional[str] = Field(default=None, max_length=40)
    title: str = Field(default="", max_length=200)


class InvestigationPatch(BaseModel):
    status: Optional[str] = Field(default=None, pattern=r"^(open|closed)$")
    notes: Optional[str] = Field(default=None, max_length=20000)
    baseline_run_id: Optional[str] = Field(default=None, max_length=40)


def inv_out(db: Session, i: Investigation) -> dict:
    d = model_to_dict(i)
    run = db.get(TestRun, i.run_id)
    d["run"] = run_out(run) if run else None
    d["reports"] = [{"id": r.id, "generated_at": iso(r.generated_at), "title": r.title}
                    for r in db.execute(select(Report).where(Report.investigation_id == i.id).order_by(Report.generated_at.desc())).scalars()]
    return d


@router.get("/investigations")
def list_investigations(page: Page = Depends(), p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    items, total = paginate(db, select(Investigation).order_by(Investigation.created_at.desc()), page)
    return page_body([inv_out(db, i) for i in items], total, page)


@router.post("/investigations", status_code=201)
def create_investigation(body: InvestigationIn, p: Principal = Depends(require("investigations:write")), db: Session = Depends(get_db)):
    run = get_or_404(db, TestRun, body.run_id, "Run")
    if body.baseline_run_id:
        get_or_404(db, TestRun, body.baseline_run_id, "Baseline run")
        if body.baseline_run_id == body.run_id:
            raise HTTPException(status_code=422, detail="Baseline must be a different run")
    n = db.execute(select(func.count(Investigation.id))).scalar_one() + 1
    inv = Investigation(id=new_id("inv"), key=f"INV-{n:04d}", title=body.title or f"Investigation of {run.agent_snapshot.get('name')}",
                        run_id=run.id, baseline_run_id=body.baseline_run_id, created_by=p.username)
    db.add(inv)
    db.flush()
    return inv_out(db, inv)


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    inv = get_or_404(db, Investigation, inv_id, "Investigation")
    out = inv_out(db, inv)
    out["detail"] = get_run(inv.run_id, p, db)
    out["comparison"] = None
    if inv.baseline_run_id:
        try:
            out["comparison"] = compare_runs(db, inv.baseline_run_id, inv.run_id)
        except (LookupError, ValueError) as exc:
            out["comparison"] = {"error": str(exc)}
    return out


@router.patch("/investigations/{inv_id}")
def patch_investigation(inv_id: str, body: InvestigationPatch, p: Principal = Depends(require("investigations:write")),
                        db: Session = Depends(get_db)):
    inv = get_or_404(db, Investigation, inv_id, "Investigation")
    ch = body.model_dump(exclude_unset=True)
    if ch.get("baseline_run_id"):
        get_or_404(db, TestRun, ch["baseline_run_id"], "Baseline run")
    for k, v in ch.items():
        setattr(inv, k, v)
    inv.updated_at = utcnow()
    return inv_out(db, inv)


# ------------------------------------------------------------------ reports
class ReportIn(BaseModel):
    investigation_id: Optional[str] = Field(default=None, max_length=40)
    run_id: Optional[str] = Field(default=None, max_length=40)


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, p: Principal = Depends(require("reports:generate")), db: Session = Depends(get_db)):
    inv = get_or_404(db, Investigation, body.investigation_id, "Investigation") if body.investigation_id else None
    run_id = inv.run_id if inv else body.run_id
    if not run_id:
        raise HTTPException(status_code=422, detail="Provide an investigation or a run")
    get_or_404(db, TestRun, run_id, "Run")
    try:
        rep = generate_report(db, run_id, inv, p.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Report data failed validation: {exc}")
    return {"id": rep.id, "title": rep.title, "data_hash": rep.data_hash, "generated_at": iso(rep.generated_at)}


@router.get("/reports")
def list_reports(page: Page = Depends(), p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    items, total = paginate(db, select(Report).order_by(Report.generated_at.desc()), page)
    return page_body([model_to_dict(r, exclude=("content",)) for r in items], total, page)


@router.get("/reports/{report_id}")
def get_report(report_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    r = get_or_404(db, Report, report_id, "Report")
    return model_to_dict(r)


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    r = get_or_404(db, Report, report_id, "Report")
    return Response(content=r.content, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="aegis-report-{r.id}.html"',
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


# ------------------------------------------------------------------ analytics
@router.get("/analytics")
def analytics(agent_id: Optional[str] = None, p: Principal = Depends(require("read")), db: Session = Depends(get_db)):
    runs_stmt = select(TestRun).where(TestRun.status == "completed").order_by(TestRun.created_at)
    if agent_id:
        runs_stmt = runs_stmt.where(TestRun.agent_id == agent_id)
    runs = db.execute(runs_stmt).scalars().all()
    run_ids = [r.id for r in runs]
    rows = db.execute(select(ScenarioResult, Scenario).join(Scenario, Scenario.id == ScenarioResult.scenario_id)
                      .where(ScenarioResult.run_id.in_(run_ids))).all() if run_ids else []
    results = [{**{c: getattr(r, c) for c in ("outcome", "attack_attempted", "attack_succeeded", "task_completed",
                                               "prohibited_attempts", "prohibited_blocked", "prohibited_executed",
                                               "permitted_attempts", "permitted_blocked", "latency_ms")},
                "ground_truth": s.ground_truth, "category": s.category} for r, s in rows]
    calls = db.execute(select(ToolCall.latency_ms).where(ToolCall.run_id.in_(run_ids))).scalars().all() if run_ids else []
    m = compute_metrics(results, [c for c in calls if c is not None], len(calls))
    counts = m.pop("_counts")
    by_cat: Dict[str, Dict[str, int]] = {}
    for r in results:
        c = by_cat.setdefault(r["category"], {"PASS": 0, "FAIL": 0, "ERROR": 0, "INCONCLUSIVE": 0, "SKIPPED": 0})
        c[r["outcome"]] += 1
    per_run = [{"run_id": r.id, "label": r.label, "agent_key": r.agent_snapshot.get("key"), "created_at": iso(r.created_at),
                "counts": (r.summary or {}).get("counts", {}),
                "block_rate": ((r.summary or {}).get("metrics", {}).get("block_rate") or {}).get("value"),
                "attack_attempt_rate": ((r.summary or {}).get("metrics", {}).get("attack_attempt_rate") or {}).get("value"),
                "pass_rate": ((r.summary or {}).get("metrics", {}).get("pass_rate") or {}).get("value")} for r in runs]
    scenario_total = db.execute(select(func.count(Scenario.id)).where(Scenario.enabled.is_(True))).scalar_one()
    covered = len({r.scenario_key for r, _ in rows})
    return {"runs": len(runs), "results": counts, "metrics": m, "formulas": FORMULAS, "by_category": by_cat,
            "per_run": per_run, "coverage": {"scenarios_enabled": scenario_total, "scenarios_exercised": covered},
            "data_label": "Computed from stored test results against simulated fixtures. Not production telemetry."}
