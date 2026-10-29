"""Protected tool base classes.

Every tool implementation subclasses ``ProtectedTool``. Its ``execute`` method refuses
to run unless it is given the gateway capability object, which is issued exactly once
to ``app.security.tool_gateway``. Agent adapters never receive tool objects; they get a
``ToolClient`` that can only submit requests to the gateway.

Boundary note: this is an in-process boundary. Code running inside the same Python
process could still reach private attributes through introspection. The guarantee is
that every *supported* execution path (agent adapters, API endpoints, the AEGIS runner)
goes through the gateway. See docs/LIMITATIONS.md.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict


class GatewayBypassError(RuntimeError):
    """Raised when a protected tool is invoked without the gateway capability."""


class _Capability:
    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:  # pragma: no cover
        return "<gateway-capability>"


_ISSUED: Optional[_Capability] = None


def issue_gateway_capability() -> _Capability:
    """Issue the single execution capability. A second call raises."""
    global _ISSUED
    if _ISSUED is not None:
        raise GatewayBypassError("Gateway capability already issued")
    _ISSUED = _Capability("sentinel-gateway")
    return _ISSUED


class ToolError(Exception):
    """A classified tool failure. ``classification`` is recorded verbatim."""

    CLASSES = (
        "timeout",
        "upstream_rate_limited",
        "upstream_error",
        "invalid_response",
        "unavailable",
        "not_found",
        "tool_error",
    )

    def __init__(self, classification: str, message: str) -> None:
        if classification not in self.CLASSES:
            classification = "tool_error"
        super().__init__(message)
        self.classification = classification
        self.message = message


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation: str  # read | write | egress
    sensitivity: str  # low | medium | high
    resource_kind: str
    args_model: Type[StrictArgs]
    sensitive_fields: tuple = ()

    def json_schema(self) -> dict:
        return self.args_model.model_json_schema()


@dataclass
class ToolOutput:
    data: Any
    text: str
    classification: str = "public"  # public | internal | confidential | untrusted_external
    untrusted: bool = False
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolContext:
    sandbox: "Any"
    correlation_id: str
    agent_id: str
    session_subject: Optional[str]
    faults: Dict[str, str] = field(default_factory=dict)
    fault_state: Dict[str, int] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    grant: Dict[str, Any] = field(default_factory=dict)


class ProtectedTool(abc.ABC):
    spec: ClassVar[ToolSpec]

    def resource_attributes(self, args: StrictArgs, ctx: ToolContext) -> Dict[str, Any]:
        """Metadata used by the policy engine. Must not perform the action itself."""
        return {}

    @abc.abstractmethod
    async def _run(self, args: StrictArgs, ctx: ToolContext) -> ToolOutput:
        raise NotImplementedError

    async def execute(self, args: StrictArgs, ctx: ToolContext, *, capability: Any = None) -> ToolOutput:
        if capability is None or capability is not _ISSUED:
            raise GatewayBypassError(
                f"Tool '{self.spec.name}' can only be executed through the SENTINEL gateway"
            )
        return await self._run(args, ctx)


def safe_relative_path(root: Path, raw: str) -> Optional[str]:
    """Normalise ``raw`` relative to ``root``. Returns None if it escapes the root."""
    if not raw or "\x00" in raw or raw.startswith(("/", "~")) or "\\" in raw or ":" in raw:
        return None
    try:
        root_r = root.resolve()
        target = (root_r / raw).resolve()
        rel = target.relative_to(root_r)
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def classification_for_path(rel_path: Optional[str]) -> str:
    if rel_path is None:
        return "unknown"
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] == "documents":
        return {"public": "public", "internal": "internal", "restricted": "confidential"}.get(parts[1], "unknown")
    return "unknown"


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$"


def list_names(items: List[Any]) -> List[str]:
    return [str(i) for i in items]
