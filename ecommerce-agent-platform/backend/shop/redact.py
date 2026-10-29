"""Redaction for stored and displayed agent evidence.

Personal fields are masked in everything persisted for the Agent Workbench. Leak-detection markers
(CANARY-...) in synthetic fixtures are left visible on purpose so a leak can be seen.
"""
from __future__ import annotations

import re
from typing import Any

PII_KEYS = {"email", "phone", "shipping_address", "default_address", "address"}
_PATTERNS = [
    (re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|proj)?[-_]?[A-Za-z0-9]{16,}\b"), "[REDACTED:api_key]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "[REDACTED:bearer]"),
    (re.compile(r"\baegis_svc_[A-Za-z0-9_-]{10,}"), "[REDACTED:service_token]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED:number]"),
]


def mask(value: str) -> str:
    v = str(value)
    if "@" in v:
        user, _, dom = v.partition("@")
        return (user[:1] + "***@" + dom) if user else "***"
    if len(v) <= 4:
        return "***"
    return v[:4] + "*" * min(12, len(v) - 6) + v[-2:]


def text(s: str, limit: int = 4000) -> str:
    out = s
    for pat, rep in _PATTERNS:
        out = pat.sub(rep, out)
    return out if len(out) <= limit else out[:limit] + f" [truncated {len(out) - limit} chars]"


def value(v: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[depth]"
    if isinstance(v, dict):
        return {k: (mask(x) if k in PII_KEYS and isinstance(x, str) else value(x, depth + 1)) for k, x in list(v.items())[:60]}
    if isinstance(v, list):
        return [value(x, depth + 1) for x in v[:60]]
    if isinstance(v, str):
        return text(v, 2000)
    return v
