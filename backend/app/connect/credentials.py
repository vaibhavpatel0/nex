"""Scoped credentials for connected agents.

- Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The key comes from AEGIS_CREDENTIAL_KEY or,
  when that is unset, from data/credential.key, generated once with mode 0600 (git-ignored).
- Secrets are never returned by the API, logged or sent to the browser; only a short fingerprint is shown.
- Credentials are attached only to requests for the connection's host (see netguard).
- Model-provider API keys are refused: a connection needs a token scoped to the agent endpoint, not an
  unrestricted key for a model account.
- OAuth 2.0 client credentials: the token endpoint must be on the connection's host; access tokens are kept
  in memory only and refreshed before expiry.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import get_settings
from app.connect.netguard import EgressError, parse_target, safe_request
from app.core.timeutil import new_id, utcnow
from app.models import AgentConnection, ConnectionCredential

HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
FORBIDDEN_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection", "cookie", "accept"}
MODEL_KEY_PATTERNS = [
    (re.compile(r"^sk-(ant-|proj-|or-)?[A-Za-z0-9_-]{16,}$"), "OpenAI, Anthropic or OpenRouter"),
    (re.compile(r"^AIza[0-9A-Za-z_-]{30,}$"), "Google"),
    (re.compile(r"^gsk_[A-Za-z0-9]{20,}$"), "Groq"),
    (re.compile(r"^hf_[A-Za-z0-9]{20,}$"), "Hugging Face"),
    (re.compile(r"^xai-[A-Za-z0-9]{20,}$"), "xAI"),
    (re.compile(r"^r8_[A-Za-z0-9]{20,}$"), "Replicate"),
    (re.compile(r"^[0-9a-f]{32}\.[A-Za-z0-9_-]{20,}$"), "Ollama"),
]


class CredentialError(Exception):
    pass


_fernet: Optional[Fernet] = None
_oauth_cache: Dict[str, Tuple[str, float]] = {}


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        s = get_settings()
        key = s.credential_key.strip()
        if not key:
            path = s.data_dir / "credential.key"
            if not path.exists():
                s.data_dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_text().strip()
        try:
            _fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise CredentialError("AEGIS_CREDENTIAL_KEY is not a valid Fernet key") from exc
    return _fernet


def reset_key_cache() -> None:
    global _fernet
    _fernet = None
    _oauth_cache.clear()


def looks_like_model_key(secret: str) -> Optional[str]:
    for pat, vendor in MODEL_KEY_PATTERNS:
        if pat.match(secret.strip()):
            return vendor
    return None


def store(db, conn: AgentConnection, *, kind: str, secret: str, header_name: Optional[str], token_url: Optional[str],
          client_id: Optional[str], scopes: Optional[list], expires_at: Optional[datetime], actor: str) -> ConnectionCredential:
    secret = (secret or "").strip()
    if not 8 <= len(secret) <= 4096:
        raise CredentialError("The secret must be 8 to 4096 characters")
    vendor = looks_like_model_key(secret)
    if vendor:
        raise CredentialError(f"This looks like a {vendor} model API key. Do not connect an unrestricted model key; "
                              "issue a token scoped to this agent endpoint instead.")
    meta: dict = {}
    if kind == "header":
        if not header_name or not HEADER_RE.match(header_name) or header_name.lower() in FORBIDDEN_HEADERS:
            raise CredentialError("Choose a valid custom header name (letters, digits and dashes)")
    elif kind == "oauth2_client_credentials":
        try:
            t = parse_target(token_url or "")
        except EgressError as exc:
            raise CredentialError(f"Token URL: {exc.message}") from exc
        if t.host != conn.host:
            raise CredentialError(f"The token endpoint must be on {conn.host}; credentials are never sent to another host")
        if not client_id or len(client_id) > 200:
            raise CredentialError("Client ID is required")
        clean_scopes = [x for x in (scopes or []) if isinstance(x, str) and re.match(r"^[\w:./-]{1,64}$", x)][:10]
        meta = {"token_url": t.url, "client_id": client_id, "scopes": clean_scopes}
        header_name = None
    elif kind == "bearer":
        header_name = None
    else:
        raise CredentialError("Unsupported credential type")
    if expires_at is not None and expires_at <= utcnow():
        raise CredentialError("The expiry must be in the future")
    for old in db.query(ConnectionCredential).filter(ConnectionCredential.connection_id == conn.id,
                                                     ConnectionCredential.revoked_at.is_(None)):
        old.revoked_at = utcnow()
        _oauth_cache.pop(old.id, None)
    db.flush()
    cred = ConnectionCredential(
        id=new_id("crd"), connection_id=conn.id, kind=kind, header_name=header_name,
        ciphertext=fernet().encrypt(secret.encode()).decode(), meta=meta,
        fingerprint=hashlib.sha256(secret.encode()).hexdigest()[:12], expires_at=expires_at, created_by=actor,
        created_at=utcnow(),
    )
    db.add(cred)
    db.flush()
    return cred


def active(db, conn: AgentConnection) -> Optional[ConnectionCredential]:
    return (db.query(ConnectionCredential).filter(ConnectionCredential.connection_id == conn.id,
                                                  ConnectionCredential.revoked_at.is_(None))
            .order_by(ConnectionCredential.created_at.desc()).first())


def revoke_all(db, conn: AgentConnection) -> int:
    n = 0
    for c in db.query(ConnectionCredential).filter(ConnectionCredential.connection_id == conn.id,
                                                   ConnectionCredential.revoked_at.is_(None)):
        c.revoked_at = utcnow()
        _oauth_cache.pop(c.id, None)
        n += 1
    db.flush()
    return n


def public_view(c: Optional[ConnectionCredential]) -> Optional[dict]:
    if c is None:
        return None
    from app.core.timeutil import iso

    return {"id": c.id, "kind": c.kind, "header_name": c.header_name, "fingerprint": c.fingerprint,
            "token_url": c.meta.get("token_url"), "client_id": c.meta.get("client_id"), "scopes": c.meta.get("scopes", []),
            "expires_at": iso(c.expires_at) if c.expires_at else None, "created_by": c.created_by,
            "created_at": iso(c.created_at), "expired": bool(c.expires_at and c.expires_at <= utcnow())}


def _decrypt(c: ConnectionCredential) -> str:
    try:
        return fernet().decrypt(c.ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialError("The stored credential cannot be decrypted (was the credential key changed?)") from exc


async def headers_for(cred: Optional[ConnectionCredential]) -> Dict[str, str]:
    """Request headers for the connection's host. Empty when the endpoint needs no credential."""
    if cred is None:
        return {}
    if cred.revoked_at is not None:
        raise CredentialError("The credential was revoked")
    if cred.expires_at is not None and cred.expires_at <= utcnow():
        raise CredentialError("The credential has expired; the owner must authorize again")
    secret = _decrypt(cred)
    if cred.kind == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if cred.kind == "header":
        return {cred.header_name or "X-Api-Key": secret}
    cached = _oauth_cache.get(cred.id)
    if cached and cached[1] > time.monotonic() + 30:
        return {"Authorization": f"Bearer {cached[0]}"}
    form = {"grant_type": "client_credentials", "client_id": cred.meta["client_id"], "client_secret": secret}
    if cred.meta.get("scopes"):
        form["scope"] = " ".join(cred.meta["scopes"])
    try:
        r = await safe_request(cred.meta["token_url"], method="POST", form_body=form, follow_redirects=False,
                               accept=("application/json",), max_bytes=64_000)
    except EgressError as exc:
        raise CredentialError(f"OAuth token request failed: {exc.message}") from exc
    if r.status != 200:
        raise CredentialError(f"The OAuth token endpoint answered HTTP {r.status}")
    try:
        d = r.json()
    except EgressError as exc:
        raise CredentialError("The OAuth token endpoint returned invalid JSON") from exc
    tok = d.get("access_token") if isinstance(d, dict) else None
    if not isinstance(tok, str) or not tok or str(d.get("token_type", "bearer")).lower() != "bearer":
        raise CredentialError("The OAuth token endpoint did not return a bearer access token")
    ttl = d.get("expires_in") if isinstance(d.get("expires_in"), (int, float)) else 300
    _oauth_cache[cred.id] = (tok, time.monotonic() + max(30.0, float(ttl)))
    return {"Authorization": f"Bearer {tok}"}


def b64(x: bytes) -> str:  # small helper for tests
    return base64.urlsafe_b64encode(x).decode()
