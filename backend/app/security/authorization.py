"""Role-based access control for the platform's own API (not for agents).

Agent permissions are enforced separately by SENTINEL's policy engine.
"""
from __future__ import annotations

from typing import Callable, Dict, FrozenSet

from fastapi import Depends, HTTPException, status

from app.security.authentication import Principal, current_principal

ALL_PERMISSIONS: FrozenSet[str] = frozenset(
    {
        "read",
        "tests:run",
        "runtime:invoke",
        "approvals:decide",
        "reports:generate",
        "investigations:write",
        "agents:write",
        "policies:write",
        "integrations:write",
        "users:manage",
        "data:delete",
    }
)

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "viewer": frozenset({"read"}),
    "operator": frozenset(
        {"read", "tests:run", "runtime:invoke", "approvals:decide", "reports:generate", "investigations:write"}
    ),
    "admin": ALL_PERMISSIONS,
}


def has_permission(principal: Principal, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(principal.role, frozenset())


def require(permission: str) -> Callable[..., Principal]:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission {permission}")

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not has_permission(principal, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({principal.role}) does not grant '{permission}'",
            )
        return principal

    return dependency
