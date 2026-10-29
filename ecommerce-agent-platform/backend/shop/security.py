"""Authentication for the store: customers and staff sign in with email and password.

- scrypt password hashes; session tokens stored only as SHA-256 digests.
- The customer identity used by the shopping agent always comes from the authenticated session,
  never from the model or from request bodies.
- AEGIS authenticates to the internal test API with SHOP_INTERNAL_TOKEN (compared in constant time).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from shop.config import get_settings
from shop.db import get_db, utcnow
from shop.models import AuthSession, User


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    d = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(d).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, s, h = encoded.split("$")
        exp = base64.b64decode(h)
        d = hashlib.scrypt(password.encode(), salt=base64.b64decode(s), n=int(n), r=int(r), p=int(p), dklen=len(exp))
        return scheme == "scrypt" and hmac.compare_digest(d, exp)
    except Exception:
        return False


_DUMMY = hash_password("not-a-real-password")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Throttle:
    def __init__(self, limit: int = 5, window: int = 300) -> None:
        self.limit, self.window = limit, window
        self._hits: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._hits[key] = [t for t in self._hits.get(key, []) if now - t < self.window]
            return len(self._hits[key]) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._hits.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


throttle = Throttle()


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str  # customer | staff
    customer_id: Optional[str]
    display_name: str
    session_id: str


def login(db: Session, email: str, password: str, client: str) -> "tuple[str, User]":
    key = f"{email.lower()}|{client}"
    if throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Wait 5 minutes or restart the backend.")
    user = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
    if user is None:
        verify_password(password, _DUMMY)
        throttle.fail(key)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.disabled or not verify_password(password, user.password_hash):
        throttle.fail(key)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    throttle.reset(key)
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(id=new_id("ses"), user_id=user.id, token_hash=digest(token), created_at=utcnow(),
                       expires_at=utcnow() + timedelta(minutes=get_settings().session_ttl_minutes)))
    return token, user


def resolve(db: Session, token: Optional[str]) -> Optional[Principal]:
    if not token or len(token) > 256:
        return None
    row = db.execute(select(AuthSession, User).join(User, User.id == AuthSession.user_id)
                     .where(AuthSession.token_hash == digest(token))).first()
    if row is None:
        return None
    s, u = row
    if s.revoked_at is not None or s.expires_at <= utcnow() or u.disabled:
        return None
    return Principal(user_id=u.id, email=u.email, role=u.role, customer_id=u.customer_id, display_name=u.display_name,
                     session_id=s.id)


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def current(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve(db, _bearer(request))
    if p is None:
        raise HTTPException(status_code=401, detail="Sign in required", headers={"WWW-Authenticate": "Bearer"})
    return p


def customer(p: Principal = Depends(current)) -> Principal:
    if p.role != "customer" or not p.customer_id:
        raise HTTPException(status_code=403, detail="Customer account required")
    return p


def staff(p: Principal = Depends(current)) -> Principal:
    if p.role != "staff":
        raise HTTPException(status_code=403, detail="Staff account required for the Agent Workbench")
    return p


def internal(request: Request) -> str:
    expected = get_settings().internal_token
    got = _bearer(request) or ""
    if not expected or not hmac.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid internal token")
    return "aegis"
