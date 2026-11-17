"""Authentication: password hashing, JWT access tokens, and FastAPI dependencies.

* Passwords: salted PBKDF2-SHA256 (Python standard library), constant-time comparison.
* Tokens: signed JWT (HS256) with the user id and role, expiring after 8 hours.
* Every request re-reads the user from the database, so a deactivated user is locked out at once.
"""
import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import connect

ITERATIONS = 200_000
TOKEN_HOURS = 8
ALGORITHM = "HS256"
bearer = HTTPBearer(auto_error=False)


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set in .env (at least 32 characters)")
    return secret


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algorithm, iterations, salt, digest = (stored or "").split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    return hmac.compare_digest(candidate, base64.b64decode(digest))


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    auth_subject: str
    role: str
    display_name: str
    patient_id: str | None


def authenticate(username: str, password: str) -> CurrentUser | None:
    with connect() as conn:
        row = conn.execute("""SELECT user_id, auth_subject, role, display_name, patient_id, password_hash, is_active
                              FROM users WHERE auth_subject = %s""", (username.strip().lower(),)).fetchone()
    # verify even when the user is unknown, so response time doesn't reveal which usernames exist
    ok = verify_password(password, row["password_hash"] if row else hash_password("dummy-password"))
    if not row or not ok or not row["is_active"]:
        return None
    return CurrentUser(row["user_id"], row["auth_subject"], row["role"], row["display_name"], row["patient_id"])


def create_token(user: CurrentUser) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": user.auth_subject, "uid": user.user_id, "role": user.role,
              "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized from None
    with connect() as conn:
        row = conn.execute("""SELECT user_id, auth_subject, role, display_name, patient_id, is_active
                              FROM users WHERE auth_subject = %s""", (claims.get("sub"),)).fetchone()
    if not row or not row["is_active"] or row["user_id"] != claims.get("uid"):
        raise unauthorized
    return CurrentUser(row["user_id"], row["auth_subject"], row["role"], row["display_name"], row["patient_id"])


def require_role(*roles: str):
    def check(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")
        return user
    return check
