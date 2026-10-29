"""Local authentication: scrypt password hashing and opaque bearer session tokens.

- Passwords are hashed with ``hashlib.scrypt`` (stdlib, memory-hard) and a random salt.
- Session tokens are 256-bit random values. Only their SHA-256 digest is stored, so a
  database leak does not reveal usable tokens.
- Failed logins are throttled per username and per client address (in-memory).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.timeutil import new_id, utcnow
from app.database.base import get_db
from app.models import ApiSession, User

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


# A fixed dummy hash keeps login timing similar for unknown usernames.
_DUMMY_HASH = "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + base64.b64encode(b"\x00" * 32).decode()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: str
    session_id: Optional[str] = None


class LoginThrottle:
    """Sliding-window failure counter. Resets on process restart (documented)."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> List[float]:
        items = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = items
        return items

    def is_blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(len(self._prune(k, now)) >= self.max_failures for k in keys)

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                self._prune(k, now).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


login_throttle = LoginThrottle()


def authenticate(db: Session, username: str, password: str, client: str) -> Optional[User]:
    keys = (f"user:{username.lower()}", f"ip:{client}")
    if login_throttle.is_blocked(*keys):
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        verify_password(password, _DUMMY_HASH)
        login_throttle.record_failure(*keys)
        return None
    if user.disabled or not verify_password(password, user.password_hash):
        login_throttle.record_failure(*keys)
        return None
    login_throttle.reset(*keys)
    return user


def create_session(db: Session, user: User) -> "tuple[str, ApiSession]":
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    sess = ApiSession(
        id=new_id("ses"),
        user_id=user.id,
        token_hash=token_digest(token),
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(minutes=settings.session_ttl_minutes),
    )
    db.add(sess)
    user.last_login_at = utcnow()
    db.flush()
    return token, sess


def resolve_token(db: Session, token: Optional[str]) -> Optional[Principal]:
    if not token or len(token) > 256:
        return None
    row = db.execute(
        select(ApiSession, User)
        .join(User, User.id == ApiSession.user_id)
        .where(ApiSession.token_hash == token_digest(token))
    ).first()
    if row is None:
        return None
    sess, user = row
    if sess.revoked_at is not None or sess.expires_at <= utcnow() or user.disabled:
        return None
    return Principal(user_id=user.id, username=user.username, role=user.role, session_id=sess.id)


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    principal = resolve_token(db, _bearer(request))
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal
