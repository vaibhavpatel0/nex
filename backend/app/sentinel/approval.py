"""Human approval workflow for REQUIRE_APPROVAL decisions.

Binding rules
- An approval is bound to (agent, tool, canonical argument hash). Any change to the
  arguments produces a different hash, so the approval no longer applies.
- Approvals expire (``AEGIS_APPROVAL_TTL_SECONDS``) and are single use: executing an
  approved action marks it ``consumed`` atomically.
- Approving does not execute anything. Execution is a new request that passes through the
  full policy evaluation again; BLOCK decisions are never overridden by an approval.
- The original raw arguments are kept only in process memory until expiry so that an
  approved action can be re-submitted. They are never written to disk. After a restart
  the agent must be re-run (documented limitation).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.timeutil import new_id, utcnow
from app.models import ApprovalRequest


@dataclass
class PendingAction:
    approval_id: str
    agent_id: str
    tool_name: str
    args: Dict[str, Any]
    correlation_id: str
    run_id: Optional[str]
    sandbox_root: str
    session_subject: Optional[str]
    faults: Dict[str, str]
    expires_at: Any


class ApprovalService:
    def __init__(self) -> None:
        self._pending: Dict[str, PendingAction] = {}
        self._lock = threading.Lock()

    def create(
        self,
        db: Session,
        *,
        decision_id: str,
        tool_call_id: str,
        correlation_id: str,
        run_id: Optional[str],
        agent_id: str,
        tool_name: str,
        args_hash: str,
        args_redacted: Dict[str, Any],
        raw_args: Dict[str, Any],
        reason: str,
        matched_policy_key: Optional[str],
        sandbox_root: str,
        session_subject: Optional[str],
        faults: Dict[str, str],
    ) -> ApprovalRequest:
        ttl = get_settings().approval_ttl_seconds
        now = utcnow()
        appr = ApprovalRequest(
            id=new_id("apr"),
            decision_id=decision_id,
            tool_call_id=tool_call_id,
            correlation_id=correlation_id,
            run_id=run_id,
            agent_id=agent_id,
            tool_name=tool_name,
            args_hash=args_hash,
            args_redacted=args_redacted,
            reason=reason,
            matched_policy_key=matched_policy_key,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        db.add(appr)
        with self._lock:
            self._pending[appr.id] = PendingAction(
                approval_id=appr.id, agent_id=agent_id, tool_name=tool_name, args=dict(raw_args),
                correlation_id=correlation_id, run_id=run_id, sandbox_root=sandbox_root,
                session_subject=session_subject, faults=dict(faults), expires_at=appr.expires_at,
            )
        return appr

    def pending_action(self, approval_id: str) -> Optional[PendingAction]:
        with self._lock:
            pa = self._pending.get(approval_id)
            if pa and pa.expires_at <= utcnow():
                self._pending.pop(approval_id, None)
                return None
            return pa

    def forget(self, approval_id: str) -> None:
        with self._lock:
            self._pending.pop(approval_id, None)

    @staticmethod
    def expire_stale(db: Session) -> int:
        res = db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.status.in_(("pending", "approved")), ApprovalRequest.expires_at <= utcnow())
            .values(status="expired")
        )
        return res.rowcount or 0

    def decide(self, db: Session, approval_id: str, approve: bool, decided_by: str, note: str = "") -> Tuple[Optional[ApprovalRequest], str]:
        self.expire_stale(db)
        appr = db.get(ApprovalRequest, approval_id)
        if appr is None:
            return None, "not_found"
        if appr.status != "pending":
            return appr, f"not_pending:{appr.status}"
        res = db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == "pending")
            .values(
                status="approved" if approve else "denied",
                decided_by=decided_by,
                decided_at=utcnow(),
                decision_note=note[:500],
            )
        )
        if (res.rowcount or 0) != 1:
            return appr, "conflict"
        db.refresh(appr)
        if not approve:
            self.forget(approval_id)
        return appr, "ok"

    def check_binding(
        self, db: Session, approval_id: str, agent_id: str, tool_name: str, args_hash: str
    ) -> Tuple[bool, str]:
        self.expire_stale(db)
        appr = db.get(ApprovalRequest, approval_id)
        if appr is None:
            return False, "approval not found"
        if appr.status == "consumed":
            return False, "approval has already been used"
        if appr.status == "expired" or appr.expires_at <= utcnow():
            return False, "approval has expired"
        if appr.status != "approved":
            return False, f"approval is {appr.status}"
        if appr.agent_id != agent_id or appr.tool_name != tool_name:
            return False, "approval was issued for a different agent or tool"
        if appr.args_hash != args_hash:
            return False, "arguments differ from the approved action"
        return True, "ok"

    def consume(self, db: Session, approval_id: str, tool_call_id: str) -> bool:
        res = db.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.status == "approved")
            .values(status="consumed", consumed_at=utcnow(), consumed_by_call_id=tool_call_id)
        )
        ok = (res.rowcount or 0) == 1
        if ok:
            self.forget(approval_id)
        return ok


approval_service = ApprovalService()


def pending_count(db: Session) -> int:
    ApprovalService.expire_stale(db)
    return len(db.execute(select(ApprovalRequest.id).where(ApprovalRequest.status == "pending")).all())
