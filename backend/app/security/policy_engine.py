"""Deterministic SENTINEL policy engine.

The engine is a pure function of (agent, tool, arguments, resource attributes, session
history, policies). It performs no I/O and never consults a language model.

Combination rules (documented in docs/SECURITY_POLICIES.md):
1. Every enabled policy whose ``applies_to`` matches the tool is evaluated.
2. If any BLOCK policy triggers, the decision is BLOCK (deny overrides). Priority only
   selects which BLOCK is reported as the matched policy. An ALLOW can never override it.
3. Otherwise, if any REQUIRE_APPROVAL policy triggers, the decision is REQUIRE_APPROVAL.
4. Otherwise, if an ALLOW policy triggers, the decision is ALLOW.
5. Otherwise the decision is BLOCK (default deny).
6. If a rule raises, it is treated as a triggered BLOCK (fail closed).
CONSTRAIN policies never change the decision; they attach execution limits (timeouts).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SENSITIVITY_RISK = {"low": "low", "medium": "medium", "high": "high"}


# --------------------------------------------------------------------------- input types
@dataclass(frozen=True)
class PolicyView:
    key: str
    name: str
    rule_type: str
    effect: str
    priority: int
    enabled: bool
    applies_to: Sequence[str]
    params: Dict[str, Any]
    risk: str
    version: int


@dataclass(frozen=True)
class AgentView:
    id: str
    key: str
    enabled_tools: Sequence[str]
    grants: Dict[str, Any]
    revision: int


@dataclass(frozen=True)
class HistoryItem:
    tool_name: str
    operation: Optional[str]
    status: str
    classification: Optional[str]
    created_at: datetime
    flags: Tuple[str, ...] = ()


@dataclass
class EvaluationInput:
    agent: AgentView
    tool_name: str
    tool_operation: Optional[str]  # None => unknown tool
    tool_sensitivity: Optional[str]
    args: Optional[Dict[str, Any]]
    args_error: Optional[str]
    resource: Dict[str, Any]
    session_subject: Optional[str]
    session_history: List[HistoryItem]
    agent_recent_calls: Dict[str, int]  # tool name -> calls within each agent-scope window, plus "*"
    now: datetime


@dataclass
class RuleResult:
    triggered: bool
    detail: str
    constraint: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyEvaluation:
    decision: str
    matched_policy_key: Optional[str]
    reason: str
    risk: str
    evaluated: List[Dict[str, Any]]
    constraints: Dict[str, Any]
    approval_policy_keys: List[str]
    policy_hash: str


# --------------------------------------------------------------------------- helpers
def grant_for(inp: EvaluationInput) -> Optional[Dict[str, Any]]:
    tools = (inp.agent.grants or {}).get("tools", {})
    if inp.tool_name not in inp.agent.enabled_tools or inp.tool_name not in tools:
        return None
    g = tools.get(inp.tool_name)
    return g if isinstance(g, dict) else {}


def _match_any(path: str, globs: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def _external(domains: Sequence[str], internal: Sequence[str]) -> List[str]:
    return [d for d in domains if d not in set(internal)]


def tool_matches(tool_name: str, applies_to: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(tool_name, p) for p in applies_to)


def compute_policy_hash(policies: Sequence[PolicyView]) -> str:
    canon = [
        {
            "key": p.key, "version": p.version, "enabled": p.enabled, "effect": p.effect,
            "priority": p.priority, "applies_to": list(p.applies_to), "params": p.params, "rule_type": p.rule_type,
        }
        for p in sorted(policies, key=lambda x: x.key)
    ]
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- rules
def r_unknown_tool(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    if inp.tool_operation is None:
        return RuleResult(True, "The requested tool is not registered with SENTINEL")
    return RuleResult(False, "Tool is registered")


def r_tool_not_granted(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    if grant_for(inp) is None:
        return RuleResult(True, "The agent's permission profile does not grant this tool")
    return RuleResult(False, "Tool is granted to the agent")


def r_invalid_arguments(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    if inp.args_error:
        return RuleResult(True, f"Arguments failed validation: {inp.args_error}")
    return RuleResult(False, "Arguments match the tool schema")


def r_path_scope(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    res = inp.resource
    if not res.get("inside_sandbox", False) or not res.get("path"):
        return RuleResult(True, "Path is outside the permitted document store or could not be normalised")
    path = res["path"]
    # Directories are matched as if a file inside them were requested.
    probe = path if inp.tool_name != "files.search" else path.rstrip("/") + "/__any__"
    deny = p.params.get("deny_globs", [])
    if _match_any(probe, deny):
        return RuleResult(True, "Path matches a denied location")
    grant = grant_for(inp) or {}
    allowed = grant.get("paths", [])
    if not _match_any(probe, allowed):
        return RuleResult(True, "Path is not within the agent's granted paths")
    return RuleResult(False, "Path is within granted scope")


def r_customer_scope(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    grant = grant_for(inp) or {}
    if grant.get("customer_scope", "session") == "any":
        return RuleResult(False, "Agent grant allows any customer")
    owner = inp.resource.get("owner_customer_id")
    if inp.session_subject is None:
        return RuleResult(True, "No authenticated customer is bound to this session")
    if owner is None:
        # Unknown records are denied so that existence cannot be probed across customers.
        return RuleResult(True, "Record owner could not be verified")
    if owner != inp.session_subject:
        return RuleResult(True, "Record belongs to a different customer than the session customer")
    return RuleResult(False, "Record belongs to the session customer")


def r_db_table_scope(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    args = inp.args or {}
    table = args.get("table")
    if table in p.params.get("restricted_tables", []):
        return RuleResult(True, "Table is restricted for all agents")
    grant = grant_for(inp) or {}
    key = "write_tables" if inp.tool_operation == "write" else "tables"
    scoped: Dict[str, List[str]] = grant.get(key, {})
    if table not in scoped:
        return RuleResult(True, "Table is not granted for this operation")
    allowed_cols = set(scoped[table])
    requested = set(args.get("columns", [])) | set(args.get("filters", {})) | set(args.get("fields", {}))
    outside = sorted(requested - allowed_cols)
    if outside:
        return RuleResult(True, f"{len(outside)} column(s) are outside the granted column set")
    return RuleResult(False, "Table and columns are within grant")


def r_recipient_allowlist(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    domains = inp.resource.get("recipient_domains", [])
    grant = grant_for(inp) or {}
    allowed = set(grant.get("recipient_domains", [])) | set(p.params.get("always_allowed_domains", []))
    bad = [d for d in domains if d not in allowed]
    if bad:
        return RuleResult(True, f"{len(bad)} recipient domain(s) are not on the allowlist")
    return RuleResult(False, "All recipients are on the allowlist")


def r_attachment_restriction(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    atts = inp.resource.get("attachments", [])
    if not atts:
        return RuleResult(False, "No attachments")
    grant = grant_for(inp) or {}
    if not grant.get("allow_attachments", False):
        return RuleResult(True, "Agent is not permitted to send attachments")
    blocked = set(p.params.get("blocked_classifications", ["confidential"]))
    allowed_paths = grant.get("attachment_paths", [])
    for a in atts:
        if not a.get("inside_sandbox") or not a.get("path"):
            return RuleResult(True, "Attachment path is outside the document store")
        if a.get("classification") in blocked:
            return RuleResult(True, f"Attachment classified '{a.get('classification')}' cannot be sent")
        if not _match_any(a["path"], allowed_paths):
            return RuleResult(True, "Attachment is not within granted attachment paths")
    return RuleResult(False, "Attachments permitted")


def r_sensitive_egress(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    from app.security.redaction import find_sensitive

    args = inp.args or {}
    fields = p.params.get("fields", ["subject", "body"])
    text = "\n".join(str(args.get(f, "")) for f in fields)
    hits = find_sensitive(text)
    if hits:
        return RuleResult(True, "Outbound content contains sensitive data classes: " + ", ".join(sorted(set(hits))))
    return RuleResult(False, "No sensitive patterns in outbound content")


def r_suspicious_sequence(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    prior = set(p.params.get("prior_classifications", ["confidential"]))
    internal = p.params.get("internal_domains", [])
    external = _external(inp.resource.get("recipient_domains", []), internal)
    if not external:
        return RuleResult(False, "No external destination")
    seen = [h for h in inp.session_history if h.status == "executed" and h.classification in prior]
    if seen:
        kinds = sorted({h.classification or "" for h in seen})
        return RuleResult(True, "Session read " + "/".join(kinds) + " data and is now sending to an external destination")
    return RuleResult(False, "No sensitive read earlier in this session")


def r_tainted_session(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    """Triggers for write/egress tools after this session received content flagged as containing
    embedded instructions (see security/content_scan.py)."""
    ops = p.params.get("operations", ["write", "egress"])
    if inp.tool_operation not in ops:
        return RuleResult(False, "Operation is not covered by the taint rule")
    wanted = set(p.params.get("flags", ["suspected_injection"]))
    tainted = [h for h in inp.session_history if h.status == "executed" and wanted.intersection(h.flags)]
    if tainted:
        return RuleResult(True, f"Earlier in this session {tainted[-1].tool_name} returned content with embedded "
                                "instructions; this action needs a human decision")
    return RuleResult(False, "No untrusted instructions seen in this session")


def r_approval_required(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    when = p.params.get("when", "always")
    if when == "always":
        return RuleResult(True, "This operation always requires human approval")
    if when == "external_recipients":
        external = _external(inp.resource.get("recipient_domains", []), p.params.get("internal_domains", []))
        if external:
            return RuleResult(True, "Sending to external recipients requires human approval")
        return RuleResult(False, "All recipients are internal")
    return RuleResult(False, "Condition not met")


def r_rate_limit(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    max_calls = int(p.params.get("max_calls", 10))
    window = float(p.params.get("window_seconds", 60))
    scope = p.params.get("scope", "session")
    per_tool = bool(p.params.get("per_tool", False))
    if scope == "session":
        count = sum(
            1
            for h in inp.session_history
            if (inp.now - h.created_at).total_seconds() <= window and (not per_tool or h.tool_name == inp.tool_name)
        )
    else:
        count = inp.agent_recent_calls.get(inp.tool_name if per_tool else "*", 0)
    if count >= max_calls:
        return RuleResult(True, f"Rate limit reached: {count} calls in {int(window)}s ({scope} scope, limit {max_calls})")
    return RuleResult(False, f"{count} of {max_calls} calls used in window")


def r_failure_limit(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    limit = int(p.params.get("max_consecutive_failures", 3))
    consecutive = 0
    for h in reversed(inp.session_history):
        if h.tool_name != inp.tool_name:
            continue
        if h.status == "failed":
            consecutive += 1
        elif h.status == "executed":
            break
    if consecutive >= limit:
        return RuleResult(True, f"{consecutive} consecutive failures for this tool (limit {limit})")
    return RuleResult(False, f"{consecutive} consecutive failures")


def r_url_allowlist(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    r = inp.resource
    if not r.get("valid"):
        return RuleResult(True, "URL could not be parsed")
    if r.get("scheme") not in p.params.get("allowed_schemes", ["https"]):
        return RuleResult(True, "URL scheme is not permitted")
    if r.get("has_credentials"):
        return RuleResult(True, "URLs containing credentials are not permitted")
    if r.get("is_ip_literal") or r.get("private_address"):
        return RuleResult(True, "Private, loopback and IP-literal destinations are blocked")
    if r.get("port") not in (None, 443):
        return RuleResult(True, "Non-standard ports are not permitted")
    if r.get("host") not in p.params.get("allowed_hosts", []):
        return RuleResult(True, "Host is not on the outbound allowlist")
    return RuleResult(False, "Host is on the outbound allowlist")


def r_granted_by_profile(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    if inp.tool_operation is not None and grant_for(inp) is not None:
        return RuleResult(True, "Tool is explicitly granted by the agent's permission profile")
    return RuleResult(False, "No grant")


def r_execution_timeout(inp: EvaluationInput, p: PolicyView) -> RuleResult:
    t = float(p.params.get("timeout_seconds", 3.0))
    return RuleResult(True, f"Execution timeout {t:g}s", constraint={"timeout_seconds": t})


RULES: Dict[str, Callable[[EvaluationInput, PolicyView], RuleResult]] = {
    "unknown_tool": r_unknown_tool,
    "tool_not_granted": r_tool_not_granted,
    "invalid_arguments": r_invalid_arguments,
    "path_scope": r_path_scope,
    "customer_scope": r_customer_scope,
    "db_table_scope": r_db_table_scope,
    "recipient_allowlist": r_recipient_allowlist,
    "attachment_restriction": r_attachment_restriction,
    "sensitive_egress": r_sensitive_egress,
    "suspicious_sequence": r_suspicious_sequence,
    "approval_required": r_approval_required,
    "tainted_session": r_tainted_session,
    "rate_limit": r_rate_limit,
    "failure_limit": r_failure_limit,
    "url_allowlist": r_url_allowlist,
    "granted_by_profile": r_granted_by_profile,
    "execution_timeout": r_execution_timeout,
}

# Rules that need validated arguments / resource attributes. When arguments are invalid,
# these are reported as "skipped" because SEN-003 already blocks the call.
_NEEDS_ARGS = {
    "path_scope", "customer_scope", "db_table_scope", "recipient_allowlist", "attachment_restriction",
    "sensitive_egress", "suspicious_sequence", "approval_required", "url_allowlist", "tainted_session",
}


def evaluate(inp: EvaluationInput, policies: Sequence[PolicyView]) -> PolicyEvaluation:
    policy_hash = compute_policy_hash(policies)
    evaluated: List[Dict[str, Any]] = []
    triggered: Dict[str, List[PolicyView]] = {"BLOCK": [], "REQUIRE_APPROVAL": [], "ALLOW": []}
    details: Dict[str, str] = {}
    constraints: Dict[str, Any] = {}

    for p in sorted(policies, key=lambda x: (-x.priority, x.key)):
        entry = {"key": p.key, "version": p.version, "effect": p.effect, "rule_type": p.rule_type}
        if not p.enabled:
            evaluated.append({**entry, "result": "disabled"})
            continue
        if not tool_matches(inp.tool_name, p.applies_to):
            continue  # not applicable: omitted to keep records concise
        if p.rule_type in _NEEDS_ARGS and (inp.args_error or inp.tool_operation is None):
            evaluated.append({**entry, "result": "skipped", "detail": "Requires valid arguments"})
            continue
        rule = RULES.get(p.rule_type)
        try:
            if rule is None:
                raise KeyError(f"unknown rule type {p.rule_type}")
            res = rule(inp, p)
        except Exception as exc:  # fail closed
            evaluated.append({**entry, "result": "error", "detail": f"Evaluation error ({type(exc).__name__}); denied by default"})
            triggered["BLOCK"].append(p)
            details[p.key] = "Policy evaluation failed; the action is denied by default"
            continue
        if p.effect == "CONSTRAIN":
            if res.triggered:
                constraints.update(res.constraint)
            evaluated.append({**entry, "result": "applied" if res.triggered else "not_triggered", "detail": res.detail})
            continue
        evaluated.append({**entry, "result": "triggered" if res.triggered else "not_triggered", "detail": res.detail})
        if res.triggered:
            triggered[p.effect].append(p)
            details[p.key] = res.detail

    tool_risk = SENSITIVITY_RISK.get(inp.tool_sensitivity or "high", "high")

    def risk_of(ps: List[PolicyView]) -> str:
        r = max([tool_risk] + [x.risk for x in ps], key=lambda v: RISK_ORDER.get(v, 2))
        return r

    if triggered["BLOCK"]:
        top = triggered["BLOCK"][0]  # already sorted by priority
        return PolicyEvaluation("BLOCK", top.key, details[top.key], risk_of(triggered["BLOCK"]), evaluated,
                                constraints, [], policy_hash)
    if triggered["REQUIRE_APPROVAL"]:
        top = triggered["REQUIRE_APPROVAL"][0]
        return PolicyEvaluation("REQUIRE_APPROVAL", top.key, details[top.key], risk_of(triggered["REQUIRE_APPROVAL"]),
                                evaluated, constraints, [x.key for x in triggered["REQUIRE_APPROVAL"]], policy_hash)
    if triggered["ALLOW"]:
        top = triggered["ALLOW"][0]
        return PolicyEvaluation("ALLOW", top.key, details[top.key], tool_risk, evaluated, constraints, [], policy_hash)
    return PolicyEvaluation(
        "BLOCK", None, "No policy explicitly allowed this action (default deny)", tool_risk if tool_risk != "low" else "medium",
        evaluated, constraints, [], policy_hash,
    )
