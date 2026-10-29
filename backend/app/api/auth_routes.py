from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.core.timeutil import iso, new_id, utcnow
from app.database.base import get_db
from app.models import ApiSession, User
from app.security.authentication import Principal, authenticate, create_session, current_principal, hash_password
from app.security.authorization import ROLE_PERMISSIONS, require

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    user = authenticate(db, body.username, body.password, client)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token, sess = create_session(db, user)
    return {"token": token, "expires_at": iso(sess.expires_at),
            "user": {"id": user.id, "username": user.username, "role": user.role,
                     "permissions": sorted(ROLE_PERMISSIONS[user.role])}}


@router.post("/auth/logout")
def logout(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    sess = db.get(ApiSession, p.session_id)
    if sess:
        sess.revoked_at = utcnow()
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal)):
    return {"id": p.user_id, "username": p.username, "role": p.role, "permissions": sorted(ROLE_PERMISSIONS[p.role])}


# ------------------------------------------------------------------ user administration
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(pattern=r"^(admin|operator|viewer)$")


class UserPatch(BaseModel):
    role: "str | None" = Field(default=None, pattern=r"^(admin|operator|viewer)$")
    disabled: "bool | None" = None


def _user(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role, "disabled": u.disabled,
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at)}


@router.get("/users")
def list_users(p: Principal = Depends(require("users:manage")), db: Session = Depends(get_db)):
    return {"items": [_user(u) for u in db.execute(select(User).order_by(User.created_at)).scalars()]}


@router.post("/users", status_code=201)
def create_user(body: UserIn, p: Principal = Depends(require("users:manage")), db: Session = Depends(get_db)):
    if db.execute(select(User).where(User.username == body.username)).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    u = User(id=new_id("usr"), username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.flush()
    return _user(u)


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, p: Principal = Depends(require("users:manage")), db: Session = Depends(get_db)):
    u = get_or_404(db, User, user_id, "User")
    if u.id == p.user_id and (body.disabled or (body.role and body.role != "admin")):
        raise HTTPException(status_code=400, detail="You cannot disable or demote your own account")
    if body.role:
        u.role = body.role
    if body.disabled is not None:
        u.disabled = body.disabled
        if body.disabled:
            for s in db.execute(select(ApiSession).where(ApiSession.user_id == u.id, ApiSession.revoked_at.is_(None))).scalars():
                s.revoked_at = utcnow()
    return _user(u)
