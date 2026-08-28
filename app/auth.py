"""Admin authentication: password hashing, sessions, route guard.

Standard-library only:

* passwords — ``hashlib.scrypt`` with a per-user random salt;
* sessions — random URL-safe tokens; only their SHA-256 is persisted, so a
  database leak does not yield usable cookies.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.store import Store

SESSION_COOKIE = "observer_session"
SESSION_TTL_DAYS = 7
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$", 2)
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(store: Store, username: str, password: str) -> str | None:
    """Validate credentials and create a session; returns the raw token."""
    user = store.get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    store.create_session(token_hash(token), user["id"], expires)
    return token


def logout(store: Store, token: str | None) -> None:
    if token:
        store.delete_session(token_hash(token))


def current_user(request: Request, store: Store = None) -> dict[str, Any] | None:
    """Resolve the session cookie to a user payload (or None)."""
    store = store or request.app.state.store
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return store.get_session(token_hash(token))


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "authentication required"})
    return user
