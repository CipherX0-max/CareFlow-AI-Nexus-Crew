from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..deps import current_user, err, get_session, rate_limit_auth, session_expiry
from ..models import AuthSession, Consent, PasswordReset, PatientProfile, User, utcnow
from ..security import (COOKIE_NAME, hash_password, new_token, password_problem, token_digest, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_HASH = hash_password("not-a-real-password-for-timing")


class SignUp(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)
    birth_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    preferred_language: str = Field(default="en", pattern="^(en|hi|hi-Latn|ta|te|bn|mr|gu|kn|ml|pa|or|ur)$")

    @field_validator("email")
    @classmethod
    def lower(cls, v):
        return v.lower()


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


def user_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "email_verified": u.email_verified_at is not None,
            "must_change_password": u.must_change_password, "is_synthetic": u.is_synthetic}


def _start_session(db: Session, user: User, response: Response) -> dict:
    token, csrf = new_token(), new_token()
    db.add(AuthSession(token_hash=token_digest(token), csrf_token=csrf, user_id=user.id, expires_at=session_expiry()))
    s = get_settings()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=s.cookie_secure, max_age=s.session_ttl_minutes * 60, path="/")
    return {"csrf_token": csrf}


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    s = get_settings()
    if origin and origin not in s.cors_list and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise err(403, "origin_not_allowed", "Cross-origin request refused.")


@router.post("/signup", status_code=201, dependencies=[Depends(rate_limit_auth)])
def signup(body: SignUp, request: Request, response: Response, db: Session = Depends(get_db)):
    """Public sign-up ALWAYS creates a patient. Staff/admin accounts can only be created by an administrator."""
    _check_origin(request)
    if (p := password_problem(body.password)):
        raise err(422, "weak_password", p)
    if db.execute(select(User).where(User.email == body.email)).scalar_one_or_none():
        raise err(409, "email_in_use", "An account with this email already exists. Try signing in.")
    verify = new_token()
    u = User(email=body.email, password_hash=hash_password(body.password), role="patient", display_name=body.display_name.strip(),
             verification_token_hash=token_digest(verify))
    db.add(u)
    db.flush()
    db.add(PatientProfile(user_id=u.id, birth_year=body.birth_year, preferred_language=body.preferred_language))
    audit.record(db, "auth.signup", actor=u, entity_type="user", entity_id=u.id)
    out = {"user": user_dict(u), **_start_session(db, u, response)}
    if get_settings().expose_dev_tokens:
        out["dev_verification_token"] = verify
        out["dev_note"] = "No email service is configured. In development the verification token is returned here instead of being emailed."
    return out


@router.post("/login", dependencies=[Depends(rate_limit_auth)])
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    _check_origin(request)
    s = get_settings()
    u = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if u is None:
        verify_password(_DUMMY_HASH, body.password)  # equalise timing
        raise err(401, "invalid_credentials", "Incorrect email or password.")
    if u.locked_until and u.locked_until > utcnow():
        raise err(423, "account_locked", "Too many failed attempts. Try again later or reset your password.")
    if not verify_password(u.password_hash, body.password):
        u.failed_logins += 1
        if u.failed_logins >= s.max_failed_logins:
            u.locked_until = utcnow() + timedelta(minutes=s.lockout_minutes)
            u.failed_logins = 0
            audit.record(db, "auth.locked", actor=u, entity_type="user", entity_id=u.id)
        db.commit()  # persist counters even though we raise
        raise err(401, "invalid_credentials", "Incorrect email or password.")
    if not u.is_active:
        raise err(403, "account_disabled", "This account is disabled. Contact your administrator.")
    u.failed_logins, u.locked_until = 0, None
    audit.record(db, "auth.login", actor=u, entity_type="user", entity_id=u.id)
    return {"user": user_dict(u), **_start_session(db, u, response)}


@router.post("/logout")
def logout(response: Response, sess: AuthSession = Depends(get_session), db: Session = Depends(get_db)):
    audit.record(db, "auth.logout", actor=sess.user, entity_type="user", entity_id=sess.user_id)
    db.delete(sess)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(sess: AuthSession = Depends(get_session), db: Session = Depends(get_db)):
    u = sess.user
    consent = db.execute(select(Consent).where(Consent.user_id == u.id, Consent.withdrawn_at.is_(None)).order_by(Consent.granted_at.desc())).scalars().first()
    prof = db.get(PatientProfile, u.id)
    return {"user": user_dict(u), "csrf_token": sess.csrf_token,
            "consent": {"granted": consent is not None, "text_version": consent.text_version if consent else None},
            "profile": {"birth_year": prof.birth_year, "preferred_language": prof.preferred_language} if prof else None}


class Verify(BaseModel):
    token: str = Field(max_length=200)


@router.post("/verify-email", dependencies=[Depends(rate_limit_auth)])
def verify_email(body: Verify, db: Session = Depends(get_db)):
    u = db.execute(select(User).where(User.verification_token_hash == token_digest(body.token))).scalar_one_or_none()
    if not u:
        raise err(400, "invalid_token", "This verification link is invalid or has already been used.")
    u.email_verified_at, u.verification_token_hash = utcnow(), None
    audit.record(db, "auth.email_verified", actor=u, entity_type="user", entity_id=u.id)
    return {"ok": True}


class Forgot(BaseModel):
    email: EmailStr


@router.post("/password/forgot", dependencies=[Depends(rate_limit_auth)])
def forgot(body: Forgot, db: Session = Depends(get_db)):
    """Always answers the same way (no account-existence oracle)."""
    out = {"ok": True, "message": "If an account exists for that email, reset instructions have been sent."}
    u = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if u and u.is_active:
        tok = new_token()
        db.add(PasswordReset(user_id=u.id, token_hash=token_digest(tok), expires_at=utcnow() + timedelta(minutes=30)))
        audit.record(db, "auth.password_reset_requested", actor=u, entity_type="user", entity_id=u.id)
        if get_settings().expose_dev_tokens:
            out["dev_reset_token"] = tok
            out["dev_note"] = "No email service is configured; in development the reset token is shown here instead of being emailed."
    return out


class Reset(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


@router.post("/password/reset", dependencies=[Depends(rate_limit_auth)])
def reset(body: Reset, db: Session = Depends(get_db)):
    pr = db.execute(select(PasswordReset).where(PasswordReset.token_hash == token_digest(body.token))).scalar_one_or_none()
    if not pr or pr.used_at or pr.expires_at <= utcnow():
        raise err(400, "invalid_token", "This reset link is invalid or has expired.")
    if (p := password_problem(body.new_password)):
        raise err(422, "weak_password", p)
    u = db.get(User, pr.user_id)
    u.password_hash, u.failed_logins, u.locked_until, u.must_change_password = hash_password(body.new_password), 0, None, False
    pr.used_at = utcnow()
    for s in db.execute(select(AuthSession).where(AuthSession.user_id == u.id)).scalars():
        db.delete(s)  # sign out everywhere
    audit.record(db, "auth.password_reset", actor=u, entity_type="user", entity_id=u.id)
    return {"ok": True}


class Change(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


@router.post("/password/change")
def change(body: Change, sess: AuthSession = Depends(get_session), db: Session = Depends(get_db)):
    u = sess.user
    if not verify_password(u.password_hash, body.current_password):
        raise err(403, "invalid_credentials", "Current password is incorrect.")
    if (p := password_problem(body.new_password)):
        raise err(422, "weak_password", p)
    u.password_hash, u.must_change_password = hash_password(body.new_password), False
    for s in db.execute(select(AuthSession).where(AuthSession.user_id == u.id, AuthSession.id != sess.id)).scalars():
        db.delete(s)
    audit.record(db, "auth.password_changed", actor=u, entity_type="user", entity_id=u.id)
    return {"ok": True}
