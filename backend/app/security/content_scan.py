"""Deterministic scan of tool output for embedded instructions (indirect prompt injection).

SENTINEL runs this on every tool result it records. A hit adds the flag
``suspected_injection`` to the tool call. The ``tainted_session`` policy rule can then require
approval for write or egress actions later in the same session.

This is a heuristic signal, not a guarantee: paraphrased or encoded instructions can evade it.
It never blocks content by itself and never asks a model to judge safety.
"""
from __future__ import annotations

import re
from typing import List

_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)", re.I),
    re.compile(r"\b(?:system|admin(?:istrator)?)\s+(?:override|notice|instruction|prompt|message)\b", re.I),
    re.compile(r"\b(?:note|message|instruction)s?\s+(?:to|for)\s+(?:the\s+)?(?:ai|assistant|agent|model|chatbot|llm)s?\b", re.I),
    re.compile(r"\b(?:ai|assistant|agent|chatbot)\s+(?:instruction|directive)s?\s*:", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\b(?:maintenance|developer|debug)\s+mode\b", re.I),
    re.compile(r"\bdo\s+not\s+(?:tell|mention|inform|reveal\s+this\s+to)\s+(?:the\s+)?(?:user|customer)\b", re.I),
    re.compile(r"<!--[^>]{0,400}\b(?:assistant|ai|call|send|read|issue|refund|ignore)\b", re.I | re.S),
    re.compile(r"^\s*(?:assistant|system)\s*:", re.I | re.M),
]

FLAG = "suspected_injection"


def scan_for_instructions(text: str) -> List[str]:
    """Return the list of pattern indexes that matched (empty when clean)."""
    if not text:
        return []
    sample = text[:20000]
    return [str(i) for i, p in enumerate(_PATTERNS) if p.search(sample)]


def flags_for(text: str) -> List[str]:
    return [FLAG] if scan_for_instructions(text) else []
