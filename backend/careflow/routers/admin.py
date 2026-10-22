import secrets
import shutil
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..deps import err, require_admin
from ..models import (AuditEvent, AuthSession, Case, ClinicalNote, Conflict, ExtractionRun, Fact, FollowUpQuestion, Message, Upload, User, utcnow)
from ..security import hash_password
from ..services import knowledge, llm, ocr, pipeline, stt, uploads as U

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _u(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "is_active": u.is_active, "created_at": u.created_at,
            "email_verified": u.email_verified_at is not None, "is_synthetic": u.is_synthetic, "locked": bool(u.locked_until and u.locked_until > utcnow())}


@router.get("/users")
def users(role: Optional[Literal["patient", "staff", "admin"]] = None, q: Optional[str] = Query(default=None, max_length=100), page: int = Query(1, ge=1),
          page_size: int = Query(25, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if role:
        conds.append(User.role == role)
    if q:
        conds.append(func.lower(User.email).like(f"%{q.lower()}%") | func.lower(User.display_name).like(f"%{q.lower()}%"))
    total = db.scalar(select(func.count()).select_from(User).where(*conds)) or 0
    rows = db.execute(select(User).where(*conds).order_by(User.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return {"users": [_u(u) for u in rows], "total": total}


class NewStaff(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["staff", "admin"]


@router.post("/users", status_code=201)
def create_staff(body: NewStaff, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Only an authorised administrator can create staff/admin accounts. A one-time temporary password is returned (no email service)."""
    email = body.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise err(409, "email_in_use", "An account with this email already exists.")
    temp = secrets.token_urlsafe(12) + "9!"
    u = User(email=email, display_name=body.display_name.strip(), role=body.role, password_hash=hash_password(temp), must_change_password=True,
             email_verified_at=utcnow(), created_by=admin.id)
    db.add(u)
    db.flush()
    audit.record(db, "admin.user_created", actor=admin, entity_type="user", entity_id=u.id, details={"role": body.role})
    return {"user": _u(u), "temporary_password": temp, "note": "Shown once. Share it securely; the user must change it at first sign-in."}


class UserPatch(BaseModel):
    role: Optional[Literal["patient", "staff", "admin"]] = None
    is_active: Optional[bool] = None


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise err(404, "user_not_found", "User not found.")
    if u.id == admin.id and (body.is_active is False or (body.role and body.role != "admin")):
        raise err(409, "self_lockout", "You cannot disable or demote your own account.")
    if body.role and body.role != u.role:
        if u.role == "patient" or body.role == "patient":
            raise err(409, "role_change_not_allowed", "Patient accounts cannot be converted to or from staff roles. Create a separate staff account.")
        audit.record(db, "admin.role_changed", actor=admin, entity_type="user", entity_id=u.id, details={"from": u.role, "to": body.role})
        u.role = body.role
    if body.is_active is not None and body.is_active != u.is_active:
        u.is_active = body.is_active
        if not body.is_active:
            for s in db.execute(select(AuthSession).where(AuthSession.user_id == u.id)).scalars():
                db.delete(s)
        audit.record(db, "admin.user_enabled" if body.is_active else "admin.user_disabled", actor=admin, entity_type="user", entity_id=u.id)
    return {"user": _u(u)}


@router.get("/audit")
def audit_log(case_id: Optional[str] = None, action: Optional[str] = Query(default=None, max_length=48), actor_id: Optional[str] = None, page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    conds = []
    if case_id:
        conds.append(AuditEvent.case_id == case_id)
    if action:
        conds.append(AuditEvent.action.like(f"{action}%"))
    if actor_id:
        conds.append(AuditEvent.actor_id == actor_id)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conds)) or 0
    rows = db.execute(select(AuditEvent).where(*conds).order_by(AuditEvent.id.desc()).limit(page_size).offset((page - 1) * page_size)).scalars().all()
    names = {u.id: u.display_name or u.email for u in db.execute(select(User).where(User.id.in_([r.actor_id for r in rows if r.actor_id]))).scalars()}
    return {"events": [{"id": e.id, "ts": e.ts, "actor": names.get(e.actor_id, e.actor_role), "role": e.actor_role, "action": e.action, "entity_type": e.entity_type,
                        "entity_id": e.entity_id, "case_id": e.case_id, "details": e.details, "hash": e.hash[:12]} for e in rows], "total": total}


@router.get("/audit/verify")
def audit_verify(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return audit.verify_chain(db)


@router.get("/system")
def system(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    rs = pipeline.active_ruleset()
    try:
        knowledge_status = {"available": True, **knowledge.catalog_status(rs)}
    except Exception as exc:  # noqa: BLE001 -- expose degraded configuration without failing health checks
        knowledge_status = {"available": False, "detail": type(exc).__name__}
    try:
        db.execute(select(1))
        dbok = {"available": True, "detail": s.database_url.split("://")[0]}
    except Exception as exc:  # noqa: BLE001
        dbok = {"available": False, "detail": type(exc).__name__}
    return {"database": dbok, "llm": llm.status(), "speech": stt.status(), "ocr": ocr.tesseract_status(), "knowledge": knowledge_status,
            "ruleset": {"version": rs["version"], "name": rs.get("name"), "clinically_approved": rs.get("clinically_approved", False), "note": rs.get("approval_note"),
                        "urgent_rules": [{"id": r["id"], "name": r["name"], "tier": r["tier"], "placeholder": bool(r.get("placeholder"))} for r in rs["urgent_rules"]],
                        "safety_critical_concepts": rs["safety_critical_concepts"], "ordering_policy": rs.get("ordering_policy")},
            "limits": {"max_upload_mb": s.max_upload_mb, "max_audio_mb": s.max_audio_mb, "max_pdf_pages": s.max_pdf_pages, "max_audio_seconds": s.max_audio_seconds},
            "environment": s.app_env, "expose_dev_tokens": s.expose_dev_tokens, "cookie_secure": s.cookie_secure,
            "counts": {"cases": db.scalar(select(func.count()).select_from(Case)), "users": db.scalar(select(func.count()).select_from(User)),
                       "processing": db.scalar(select(func.count()).select_from(Case).where(Case.processing_state == "processing"))}}


@router.get("/deletion-requests")
def deletion_requests(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Case).where(Case.status == "deletion_requested").order_by(Case.deletion_requested_at)).scalars().all()
    out = []
    for c in rows:
        held = db.scalar(select(func.count()).select_from(ClinicalNote).where(ClinicalNote.case_id == c.id, ClinicalNote.status == "approved")) or 0
        out.append({"id": c.id, "case_number": c.case_number, "requested_at": c.deletion_requested_at, "retention_hold": held > 0,
                    "hold_reason": "An approved clinical note exists; retention policy applies." if held else None})
    return {"requests": out}


class PurgeIn(BaseModel):
    confirm_case_number: str = Field(max_length=16)
    override_retention_reason: str = Field(default="", max_length=500)


@router.post("/cases/{cid}/purge")
def purge(cid: str, body: PurgeIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Fulfil a patient's deletion request: removes content and files; keeps a tombstone case row and the audit trail."""
    c = db.get(Case, cid)
    if not c or c.status != "deletion_requested":
        raise err(404, "request_not_found", "No pending deletion request for this case.")
    if body.confirm_case_number != c.case_number:
        raise err(422, "confirmation_mismatch", "Type the case number exactly to confirm.")
    held = db.scalar(select(func.count()).select_from(ClinicalNote).where(ClinicalNote.case_id == c.id, ClinicalNote.status == "approved")) or 0
    if held and len(body.override_retention_reason.strip()) < 15:
        raise err(409, "retention_hold", "An approved clinical note exists. Deletion needs a documented retention-override reason (15+ characters).")
    for up in db.execute(select(Upload).where(Upload.case_id == c.id)).scalars():
        try:
            U.path_for(up).unlink(missing_ok=True)
        except OSError:
            pass
    shutil.rmtree(get_settings().upload_dir / c.id, ignore_errors=True)
    for model in (Conflict, FollowUpQuestion, Fact, ExtractionRun, Message, Upload, ClinicalNote):
        for row in db.execute(select(model).where(model.case_id == c.id)).scalars():
            db.delete(row)
    c.chief_complaint, c.proposed_queue, c.confirmed_queue = "", None, None
    c.status = "deletion_requested"
    c.language = "-"
    audit.record(db, "case.purged", actor=admin, entity_type="case", entity_id=c.id, case_id=c.id, details={"retention_override": bool(held)})
    return {"ok": True}
