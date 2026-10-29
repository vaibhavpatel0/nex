"""Agent adapter interface.

Agent adapters receive a ``ToolClient``. The client can only *submit requests* to the
SENTINEL gateway; adapters never hold tool implementations, sandbox handles or the
gateway capability. ``tests/security/test_gateway_bypass.py`` checks this boundary
statically (no imports of ``app.adapters.tools`` from ``app.agents``).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol


class AgentUnavailable(Exception):
    """The agent's model or integration cannot be reached. Results must be ERROR."""


class AgentFailure(Exception):
    """The agent crashed or produced output that could not be processed."""


@dataclass
class ToolResult:
    tool_name: str
    status: str  # executed | blocked | pending_approval | failed
    decision: str
    reason: str
    matched_policy: Optional[str]
    approval_id: Optional[str]
    output_text: Optional[str]
    data: Any
    error_class: Optional[str]
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDescriptor:
    name: str
    description: str
    parameters: Dict[str, Any]


class ToolClient(Protocol):
    async def call(self, tool_name: str, args: Any, origin: str = "user_task") -> ToolResult: ...

    def available_tools(self) -> List[ToolDescriptor]: ...


EmitFn = Callable[[str, str, Dict[str, Any]], None]


@dataclass
class AgentTask:
    task: str
    session_subject: Optional[str] = None
    model_fault: Optional[str] = None  # malformed_tool_call | empty_output (test fault injection)
    # Context used by remote agents (they report their tool calls to SENTINEL under this correlation).
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    scenario_key: Optional[str] = None
    faults: Dict[str, str] = field(default_factory=dict)
    direct_tool_call: Optional[Dict[str, Any]] = None  # deterministic gateway probe, no model involved


@dataclass
class AgentOutput:
    final_text: Optional[str]
    usage: Optional[Dict[str, Any]] = None  # only when the provider reports it
    steps: int = 0
    calls: List[ToolResult] = field(default_factory=list)
    # An error the agent itself reported after running (for example a model timeout). Distinct from
    # AgentUnavailable, which means the agent could not be reached at all.
    error_kind: Optional[str] = None
    error: Optional[str] = None
    # Evidence produced by remote agents running against sandbox fixtures.
    side_effects: Optional[Dict[str, Any]] = None
    invoked_tools: Optional[List[str]] = None
    invariants: Optional[Dict[str, bool]] = None
    model: Optional[Dict[str, Any]] = None


class AgentAdapter(abc.ABC):
    kind: str = "abstract"

    @abc.abstractmethod
    async def run(self, task: AgentTask, tools: ToolClient, emit: EmitFn) -> AgentOutput:
        raise NotImplementedError

    async def check_available(self) -> "tuple[bool, str]":
        return True, "ready"


Awaitable  # re-export for type checkers
