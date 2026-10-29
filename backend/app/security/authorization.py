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
        # Used by other applications (service tokens) that rely on SENTINEL:
        "sentinel:evaluate",   # ask for a decision and report the tool result
        "sentinel:ingest",     # create runtime traces and record model events
        "tools:register",      # register the application's tool definitions
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
    if principal.permissions is not None:
        return permission in principal.permissions
    return permission in ROLE_PERMISSIONS.get(principal.role, frozenset())


def require(permission: str, allow_service: bool = False) -> Callable[..., Principal]:
    """Route dependency. Service tokens are refused everywhere except routes that opt in
    (the SENTINEL integration API), where they are additionally bound to specific agents."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission {permission}")

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.is_service and not allow_service:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Service tokens can only call the /sentinel integration API")
        if not has_permission(principal, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({principal.role}) does not grant '{permission}'",
            )
        return principal

    return dependency
