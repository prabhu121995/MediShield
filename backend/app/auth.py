"""
Authentication and role checks.

- Passwords: PBKDF2-SHA256 (PASSWORD_HASH_ITERATIONS), random salt per user (stdlib only).
- Tokens: HS256 JWT, JWT_TTL_HOURS lifetime, signed with JWT_SECRET. All three from .env.
- Roles: admin (everything, including policy ingestion) and agent (cases, upload, review).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config, db

ROLES = ("admin", "agent")
TOKEN_TTL = timedelta(hours=config.JWT_TTL_HOURS)
ITERATIONS = config.PASSWORD_HASH_ITERATIONS
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def _signing_key() -> str:
    return config.JWT_SECRET


def authenticate(username: str, password: str) -> dict | None:
    user = db.get_user(username)
    if user is None or not verify_password(password, user["password_hash"]):
        return None
    return {"username": user["username"], "role": user["role"], "full_name": user["full_name"]}


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": user["username"], "role": user["role"], "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(claims, _signing_key(), algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in")
    try:
        claims = jwt.decode(creds.credentials, _signing_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired, sign in again")
    user = db.get_user(claims["sub"])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return {"username": user["username"], "role": user["role"], "full_name": user["full_name"]}


def require_role(*allowed: str) -> Callable:
    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {', '.join(allowed)}")
        return user
    return dependency
