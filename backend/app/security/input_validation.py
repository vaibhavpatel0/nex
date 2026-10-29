"""Validation and canonicalisation of tool-call arguments.

Error messages are built from field locations and error types only, so invalid input
values are never echoed into decisions, events or logs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from app.adapters.tools.base import StrictArgs, ToolSpec

MAX_ARGS_JSON_BYTES = 16 * 1024


def sanitized_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('type', 'invalid')}")
    return "; ".join(parts)


def validate_tool_args(spec: Optional[ToolSpec], raw: Any) -> Tuple[Optional[StrictArgs], Dict[str, Any], Optional[str]]:
    """Return (validated model, plain dict, error). ``error`` is None when valid."""
    if not isinstance(raw, dict):
        return None, {}, "(root): must be an object"
    try:
        encoded = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        return None, {}, "(root): not JSON serialisable"
    if len(encoded.encode()) > MAX_ARGS_JSON_BYTES:
        return None, {}, "(root): arguments exceed size limit"
    if spec is None:
        return None, dict(raw), None
    try:
        model = spec.args_model.model_validate(raw)
    except ValidationError as exc:
        return None, dict(raw), sanitized_validation_error(exc)
    return model, model.model_dump(), None


def canonical_args_hash(agent_id: str, tool_name: str, args: Dict[str, Any]) -> str:
    """Hash binding an action to its agent, tool and exact arguments (used by approvals)."""
    payload = json.dumps({"agent": agent_id, "tool": tool_name, "args": args}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
