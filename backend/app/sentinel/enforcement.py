"""Turns a policy evaluation plus an optional approval into the enforced decision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from app.security.policy_engine import PolicyEvaluation


@dataclass
class EnforcedDecision:
    decision: str
    reason: str
    matched_policy_key: Optional[str]
    approval_used: Optional[str] = None
    approval_rejected_reason: Optional[str] = None


def enforce(
    evaluation: PolicyEvaluation,
    approval_id: Optional[str],
    check_binding: Callable[[str], Tuple[bool, str]],
) -> EnforcedDecision:
    """Apply approval semantics.

    - BLOCK stays BLOCK, whether or not an approval is supplied.
    - REQUIRE_APPROVAL becomes ALLOW only if the supplied approval is valid for exactly
      this agent, tool and argument hash; otherwise it remains REQUIRE_APPROVAL.
    - ALLOW stays ALLOW; a supplied approval is not consumed.
    """
    if evaluation.decision == "BLOCK":
        extra = " An approval cannot override a block." if approval_id else ""
        return EnforcedDecision("BLOCK", evaluation.reason + extra, evaluation.matched_policy_key)
    if evaluation.decision == "REQUIRE_APPROVAL":
        if not approval_id:
            return EnforcedDecision("REQUIRE_APPROVAL", evaluation.reason, evaluation.matched_policy_key)
        ok, why = check_binding(approval_id)
        if ok:
            return EnforcedDecision(
                "ALLOW",
                f"{evaluation.reason}. Human approval {approval_id} verified for this exact action.",
                evaluation.matched_policy_key,
                approval_used=approval_id,
            )
        return EnforcedDecision(
            "REQUIRE_APPROVAL",
            f"{evaluation.reason}. Supplied approval rejected: {why}.",
            evaluation.matched_policy_key,
            approval_rejected_reason=why,
        )
    return EnforcedDecision("ALLOW", evaluation.reason, evaluation.matched_policy_key)
