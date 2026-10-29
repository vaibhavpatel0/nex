"""Redaction of secrets and confidential values before anything is persisted or logged.

Two layers:
1. Field-level: fields a tool declares as sensitive (e-mail bodies, update values)
   are replaced by a length + short digest marker.
2. Pattern-level: any remaining string is scanned for credential and PII patterns
   and canary tokens, which are masked.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, List, Pattern, Tuple

MAX_STRING = 600

_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("api_key", re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|proj)?[-_]?[A-Za-z0-9]{16,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("password_assignment", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*\S+")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("canary", re.compile(r"\bCANARY-[A-Z0-9]{4,}(?:-[A-Z0-9]{2,})*\b")),
]


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_sensitive(text: str) -> List[str]:
    """Return the names of sensitive pattern classes present in ``text``."""
    found: List[str] = []
    for name, pat in _PATTERNS:
        for m in pat.finditer(text):
            if name == "card_number" and not _luhn_ok(m.group(0)):
                continue
            found.append(name)
            break
    return found


def find_canaries(text: str) -> List[str]:
    return sorted(set(m.group(0) for m in _PATTERNS[-1][1].finditer(text)))


def redact_text(text: str, max_len: int = MAX_STRING) -> str:
    out = text
    for name, pat in _PATTERNS:
        if name == "card_number":
            out = pat.sub(lambda m: "[REDACTED:card_number]" if _luhn_ok(m.group(0)) else m.group(0), out)
        elif name == "password_assignment":
            out = pat.sub(lambda m: f"{m.group(1)}=[REDACTED]", out)
        else:
            out = pat.sub(f"[REDACTED:{name}]", out)
    if len(out) > max_len:
        out = out[:max_len] + f"... [truncated {len(out) - max_len} chars]"
    return out


def fingerprint(value: Any) -> dict:
    s = value if isinstance(value, str) else repr(value)
    return {"redacted": True, "length": len(s), "sha256_prefix": hashlib.sha256(s.encode()).hexdigest()[:12]}


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[REDACTED:depth]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k)[:64]: redact_value(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        items = [redact_value(v, depth + 1) for v in list(value)[:50]]
        if len(value) > 50:
            items.append(f"[truncated {len(value) - 50} items]")
        return items
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(repr(value))


def redact_args(args: dict, sensitive_fields: Iterable[str]) -> dict:
    sensitive = set(sensitive_fields)
    out = {}
    for k, v in args.items():
        out[k] = fingerprint(v) if k in sensitive else redact_value(v)
    return out
