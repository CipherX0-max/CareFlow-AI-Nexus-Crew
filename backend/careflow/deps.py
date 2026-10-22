"""Authentication / authorisation dependencies. Every protected route goes through these (server-side)."""
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuthSession, Case, User, utcnow
from .security import COOKIE_NAME, CSRF_HEADER, rate_limiter, token_digest

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def get_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise err(401, "not_authenticated", "Please sign in.")
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_digest(token))).scalar_one_or_none()
    if not sess or sess.expires_at <= utcnow():
        raise err(401, "session_expired", "Your session has expired. Please sign in again.")
    if not sess.user.is_active:
        raise err(403, "account_disabled", "This account is disabled.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or sent != sess.csrf_token:
            raise err(403, "csrf_failed", "Missing or invalid CSRF token.")
        origin = request.headers.get("origin")
        s = get_settings()
        if origin and origin not in s.cors_list and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise err(403, "origin_not_allowed", "Cross-origin request refused.")
    return sess


def current_user(sess: AuthSession = Depends(get_session)) -> User:
    return sess.user


def require_roles(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise err(403, "forbidden", "You do not have permission to perform this action.")
        return user
    return dep


require_patient = require_roles("patient")
require_staff = require_roles("staff")
require_admin = require_roles("admin")


def rate_limit_auth(request: Request) -> None:
    s = get_settings()
    if not s.rate_limit_enabled:
        return
    ip = request.client.host if request.client else "?"
    if not rate_limiter.allow(f"auth:{ip}:{request.url.path}", s.rate_limit_auth_per_minute):
        raise err(429, "rate_limited", "Too many attempts. Please wait a minute and try again.")


def load_case_for(user: User, case_id: str, db: Session) -> Case:
    """Ownership gate. Patients see only their own cases; staff may see any submitted case; admins none.
    A patient probing another patient's id gets a 404 (no existence oracle)."""
    case = db.get(Case, case_id)
    if case is None:
        raise err(404, "case_not_found", "Case not found.")
    if user.role == "patient":
        if case.patient_id != user.id:
            raise err(404, "case_not_found", "Case not found.")
    elif user.role == "admin":
        raise err(403, "forbidden", "Administrators manage accounts and audit logs but cannot open case contents.")
    elif case.status == "draft":
        raise err(404, "case_not_found", "Case not found.")
    return case


def session_expiry():
    return utcnow() + timedelta(minutes=get_settings().session_ttl_minutes)
