"""ORM entities for AEGIS.

Conventions
- String primary keys carry a readable prefix (agt_, pol_, run_ ...).
- Timestamps are stored as UTC and returned timezone-aware via ``UTCDateTime``.
- Free-form payloads (JSON) only ever contain *redacted* argument/result data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.timeutil import as_utc, utcnow
from app.database.base import Base


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return as_utc(value)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return as_utc(value)


ROLES = ("admin", "operator", "viewer")
DECISIONS = ("ALLOW", "BLOCK", "REQUIRE_APPROVAL")
OUTCOMES = ("PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED")


# --------------------------------------------------------------------------- identity
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (CheckConstraint("role in ('admin','operator','viewer')", name="ck_users_role"),)


class ApiSession(Base):
    __tablename__ = "api_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    user: Mapped[User] = relationship()


class ServiceToken(Base):
    """Credential for another application (for example the e-commerce backend) calling SENTINEL.

    Only the SHA-256 digest is stored. Scopes are platform permissions; agent_keys and
    tool_namespaces bind the token to the agents it may act for and the tools it may register.
    """

    __tablename__ = "service_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    agent_keys: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tool_namespaces: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- agents & tools
class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)  # read | write | egress
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False)  # low | medium | high
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    args_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    sensitive_fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # "builtin" for in-process tools; "remote:<namespace>" for tools executed by an external
    # application that asks SENTINEL for a decision before every call.
    provider: Mapped[str] = mapped_column(String(48), default="builtin", server_default="builtin", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint("operation in ('read','write','egress')", name="ck_tool_operation"),
    )


class PermissionProfile(Base):
    __tablename__ = "permission_profiles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    grants: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_type: Mapped[str] = mapped_column(String(24), nullable=False)  # simulated | local_model | hosted_model | remote_agent
    behavior: Mapped[Optional[str]] = mapped_column(String(24))  # simulated agents: naive | guarded
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # deterministic | ollama | openai_compatible
    model_id: Mapped[Optional[str]] = mapped_column(String(128))
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)
    enabled_tools: Mapped[list] = mapped_column(JSON, nullable=False)
    permission_profile_id: Mapped[str] = mapped_column(ForeignKey("permission_profiles.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    permission_profile: Mapped[PermissionProfile] = relationship()
    __table_args__ = (
        CheckConstraint("agent_type in ('simulated','local_model','hosted_model','remote_agent')", name="ck_agent_type"),
    )


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("agent_id", "revision", name="uq_agent_revision"),)


# --------------------------------------------------------------------------- policies
class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(48), nullable=False)
    effect: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applies_to: Mapped[list] = mapped_column(JSON, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("effect in ('ALLOW','BLOCK','REQUIRE_APPROVAL','CONSTRAIN')", name="ck_policy_effect"),
    )


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    changed_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_version"),)


# --------------------------------------------------------------------------- AEGIS
class TestSuite(Base):
    __tablename__ = "test_suites"
    __test__ = False  # not a pytest class
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    use_case: Mapped[str] = mapped_column(String(48), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    ground_truth: Mapped[str] = mapped_column(String(16), nullable=False)  # malicious | benign | fault
    setup: Mapped[dict] = mapped_column(JSON, nullable=False)
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected: Mapped[dict] = mapped_column(JSON, nullable=False)
    required_tools: Mapped[list] = mapped_column(JSON, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="builtin", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class TestSuiteScenario(Base):
    __tablename__ = "test_suite_scenarios"
    __test__ = False
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id", ondelete="CASCADE"), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TestRun(Base):
    __tablename__ = "test_runs"
    __test__ = False
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    suite_id: Mapped[Optional[str]] = mapped_column(ForeignKey("test_suites.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    agent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    software_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scenario_keys: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # protected: SENTINEL enforces decisions. baseline: decisions are recorded but not enforced,
    # allowed only for test runs, which always execute against sandbox fixtures.
    mode: Mapped[str] = mapped_column(String(16), default="protected", server_default="protected", nullable=False)
    replay_of_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("test_runs.id"))
    triggered_by: Mapped[Optional[str]] = mapped_column(String(64))
    total_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    agent: Mapped[Agent] = relationship()
    suite: Mapped[Optional[TestSuite]] = relationship()
    __table_args__ = (
        CheckConstraint("status in ('queued','running','completed','failed','cancelled')", name="ck_run_status"),
        CheckConstraint("mode in ('protected','baseline')", name="ck_run_mode"),
    )


class ScenarioResult(Base):
    __tablename__ = "scenario_results"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), nullable=False)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    checks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    observed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    attack_attempted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attack_succeeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    task_completed: Mapped[Optional[bool]] = mapped_column(Boolean)
    prohibited_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prohibited_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prohibited_executed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    permitted_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    permitted_blocked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    scenario: Mapped[Scenario] = relationship()
    __table_args__ = (
        CheckConstraint("outcome in ('PASS','FAIL','ERROR','INCONCLUSIVE','SKIPPED')", name="ck_result_outcome"),
    )


class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Float)
    numerator: Mapped[Optional[float]] = mapped_column(Float)
    denominator: Mapped[Optional[float]] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "metric", name="uq_run_metric"),)


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_result_id: Mapped[Optional[str]] = mapped_column(ForeignKey("scenario_results.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # open | mitigated
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    remediation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    key: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    baseline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    investigation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[Optional[str]] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- runtime (SENTINEL)
class Trace(Base):
    __tablename__ = "traces"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)  # == correlation id
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # test | runtime | direct
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    scenario_key: Mapped[Optional[str]] = mapped_column(String(64))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    agent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running | completed | error
    task_redacted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    final_output_redacted: Mapped[Optional[str]] = mapped_column(Text)
    final_output_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    usage: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(64))


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(48), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (Index("ix_events_type_created", "event_type", "created_at"),)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[Optional[str]] = mapped_column(String(16))
    args_redacted: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    decision: Mapped[Optional[str]] = mapped_column(String(24))
    decision_id: Mapped[Optional[str]] = mapped_column(String(40))
    approval_id: Mapped[Optional[str]] = mapped_column(String(40))
    error_class: Mapped[Optional[str]] = mapped_column(String(32))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    result_classification: Mapped[Optional[str]] = mapped_column(String(24))
    flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), default="user_task", nullable=False)
    # False only in baseline test runs: the decision was recorded but not enforced.
    enforced: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint(
            "status in ('requested','blocked','pending_approval','executed','failed')", name="ck_toolcall_status"
        ),
    )


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    agent_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    evaluated: Mapped[list] = mapped_column(JSON, nullable=False)
    matched_policy_key: Mapped[Optional[str]] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (
        CheckConstraint("decision in ('ALLOW','BLOCK','REQUIRE_APPROVAL')", name="ck_decision_value"),
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(40))
    agent_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    args_redacted: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    matched_policy_key: Mapped[Optional[str]] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    consumed_by_call_id: Mapped[Optional[str]] = mapped_column(String(40))
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','approved','denied','expired','consumed')", name="ck_approval_status"
        ),
    )


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # never secrets
    status: Mapped[str] = mapped_column(String(24), default="unchecked", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- agent connections
class AgentConnection(Base):
    """An external agent endpoint submitted by a developer (see app/connect).

    Each stage is tracked separately so the UI never conflates them:
    discovery (what the endpoint says about itself), verification (proof of domain control),
    authorization (the owner's explicit grant and scoped credentials), health, monitoring and
    protection (SENTINEL enforcement observed, not merely configured).
    """

    __tablename__ = "agent_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(24), nullable=False)  # a2a | mcp | openapi | custom_http | none
    protocol_version: Mapped[Optional[str]] = mapped_column(String(32))
    interaction_endpoint: Mapped[Optional[str]] = mapped_column(String(2048))
    descriptor: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # redacted discovery result
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # adapter capability matrix
    discovered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    verification_status: Mapped[str] = mapped_column(String(16), default="unverified", nullable=False)
    verified_domain: Mapped[Optional[str]] = mapped_column(String(255))
    verification_method: Mapped[Optional[str]] = mapped_column(String(24))
    verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    verified_by: Mapped[Optional[str]] = mapped_column(String(64))
    auth_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    authorized_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    authorized_by: Mapped[Optional[str]] = mapped_column(String(64))
    granted_permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    health_status: Mapped[str] = mapped_column(String(16), default="unchecked", nullable=False)
    health_detail: Mapped[Optional[str]] = mapped_column(Text)
    last_health_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_healthy_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    mode: Mapped[str] = mapped_column(String(16), default="none", nullable=False)  # none | monitoring | protected
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    namespace: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    service_token_id: Mapped[Optional[str]] = mapped_column(ForeignKey("service_tokens.id", ondelete="SET NULL"))
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("protocol in ('a2a','mcp','openapi','custom_http','none')", name="ck_conn_protocol"),
        CheckConstraint("verification_status in ('unverified','pending','verified','expired','failed')", name="ck_conn_verif"),
        CheckConstraint("auth_status in ('none','authorized','expired','revoked')", name="ck_conn_auth"),
        CheckConstraint("mode in ('none','monitoring','protected')", name="ck_conn_mode"),
    )


class ConnectionChallenge(Base):
    __tablename__ = "connection_challenges"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # dns_txt | https_file
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(2048), nullable=False)  # TXT record name or file URL
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint("method in ('dns_txt','https_file')", name="ck_chal_method"),
        CheckConstraint("status in ('pending','verified','failed','expired')", name="ck_chal_status"),
    )


class ConnectionCredential(Base):
    """Scoped credential for an agent endpoint. The secret is encrypted (Fernet) and never returned."""

    __tablename__ = "connection_credentials"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # bearer | header | oauth2_client_credentials
    header_name: Mapped[Optional[str]] = mapped_column(String(64))
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # non-secret: token_url, client_id, scopes
    fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint("kind in ('bearer','header','oauth2_client_credentials')", name="ck_cred_kind"),
    )


class ConnectionTestScope(Base):
    """The owner's approval for AEGIS to run specific suites against the connected endpoint."""

    __tablename__ = "connection_test_scopes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    suites: Mapped[list] = mapped_column(JSON, nullable=False)
    max_runs_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class ConnectionEvent(Base):
    """Audit log for connection actions. Details never contain secrets."""

    __tablename__ = "connection_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


def model_to_dict(obj: Any, exclude: "tuple[str, ...]" = ()) -> dict:
    from app.core.timeutil import iso

    out: dict = {}
    for col in obj.__table__.columns:
        if col.key in exclude:
            continue
        val = getattr(obj, col.key)
        if isinstance(val, datetime):
            val = iso(val)
        out[col.key] = val
    return out
