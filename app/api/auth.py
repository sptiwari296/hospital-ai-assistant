"""Authentication: password hashing, JWT access tokens, cookies, lockout, and FastAPI dependencies.

* Passwords: salted PBKDF2-SHA256 (standard library), constant-time comparison.
* Tokens: signed JWT (HS256), 8 hours. The browser receives it in an httpOnly, SameSite=Strict
  cookie that JavaScript cannot read (an XSS bug cannot steal it). API clients may send
  "Authorization: Bearer <token>" instead.
* CSRF: a request authenticated by COOKIE that changes data must carry the header
  X-Requested-With: hospital-web. Other sites cannot add custom headers cross-origin.
* Lockout: 5 failed logins lock the account for 15 minutes.
* Every request re-reads the user, so deactivation and lockout apply immediately.
"""
import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import connect

ITERATIONS = 200_000
TOKEN_HOURS = 8
ALGORITHM = "HS256"
COOKIE_NAME = "hs_session"
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "hospital-web"
MAX_FAILED_LOGINS, LOCK_MINUTES = 5, 15
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
bearer = HTTPBearer(auto_error=False)
_DUMMY_HASH = None


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


def authenticate(username: str, password: str) -> tuple[CurrentUser | None, str]:
    """Returns (user, outcome) where outcome is 'success', 'invalid' or 'locked'."""
    global _DUMMY_HASH
    with connect() as conn:
        row = conn.execute(
            """SELECT user_id, auth_subject, role, display_name, patient_id, password_hash, is_active,
                      (locked_until IS NOT NULL AND locked_until > now()) AS is_locked
               FROM users WHERE auth_subject = %s""", (username.strip().lower(),)).fetchone()
        # always run the slow hash, so response time doesn't reveal whether the user exists
        _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_hex(8))
        ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
        if not row or not row["is_active"]:
            return None, "invalid"
        if row["is_locked"]:
            return None, "locked"
        if not ok:
            conn.execute(
                """UPDATE users SET failed_login_count = failed_login_count + 1,
                          locked_until = CASE WHEN failed_login_count + 1 >= %s
                                              THEN now() + make_interval(mins => %s) ELSE locked_until END
                   WHERE user_id = %s""", (MAX_FAILED_LOGINS, LOCK_MINUTES, row["user_id"]))
            return None, "invalid"
        conn.execute("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE user_id = %s",
                     (row["user_id"],))
    return CurrentUser(row["user_id"], row["auth_subject"], row["role"], row["display_name"], row["patient_id"]), \
        "success"


def create_token(user: CurrentUser) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": user.auth_subject, "uid": user.user_id, "role": user.role,
              "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=TOKEN_HOURS * 3600, httponly=True, samesite="strict",
                        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(request: Request,
                 credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials if credentials else request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized
    if credentials is None and request.method not in SAFE_METHODS \
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized from None
    with connect() as conn:
        row = conn.execute("""SELECT user_id, auth_subject, role, display_name, patient_id, is_active,
                                     (locked_until IS NOT NULL AND locked_until > now()) AS is_locked
                              FROM users WHERE auth_subject = %s""", (claims.get("sub"),)).fetchone()
    if not row or not row["is_active"] or row["is_locked"] or row["user_id"] != claims.get("uid"):
        raise unauthorized
    user = CurrentUser(row["user_id"], row["auth_subject"], row["role"], row["display_name"], row["patient_id"])
    request.state.audit_user = user            # picked up by the audit middleware
    return user


def require_role(*roles: str):
    def check(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role is not allowed to do this")
        return user
    return check
