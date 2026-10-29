"""Model-provider interface used by LLM-backed agents.

Providers translate between a neutral chat format and a specific API. They never see
tool implementations: tool calls they return are submitted to SENTINEL by the agent loop.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ProviderUnavailable(Exception):
    """The provider could not be reached or is not configured."""


class ProviderError(Exception):
    """The provider answered with an error or an unusable payload."""


@dataclass
class ChatToolCall:
    id: str
    name: str
    arguments: Any  # dict when parseable, raw string otherwise (sent to SENTINEL as-is -> rejected)


@dataclass
class ChatResponse:
    content: Optional[str]
    tool_calls: List[ChatToolCall] = field(default_factory=list)
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None


_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def to_wire_name(tool_name: str) -> str:
    """Function names may not contain dots for several providers."""
    return _SAFE.sub("_", tool_name.replace(".", "__"))


def from_wire_name(wire: str) -> str:
    return wire.replace("__", ".")


class ChatProvider(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    async def chat(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ChatResponse:
        raise NotImplementedError

    @abc.abstractmethod
    async def status(self, model: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError
