import re
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..content import CONSENT_TEXT, CONSENT_VERSION
from ..db import get_db
from ..deps import current_user, err, load_case_for, require_patient
from ..models import Case, Consent, Fact, FollowUpQuestion, Message, PatientProfile, Upload, User, utcnow
from ..validation import normalise_mobile, validate_age
from .. import serializers as S
from ..services import escalation, facts as F, followup, lexicon as L, lifecycle, pipeline, safety, uploads as U

router = APIRouter(prefix="/api", tags=["patient"])


# -------------------------------------------------------------------------------------------- public config
@router.get("/config/public")
def public_config():
    rs = pipeline.active_ruleset()
    s = get_settings()
    from ..services import llm, ocr, stt
    return {"consent": CONSENT_TEXT, "emergency": {"message_en": rs["emergency"]["message_en"], "message_hi": rs["emergency"]["message_hi"],
                                                  "message_hi_Latn": rs["emergency"]["message_hi_Latn"], "number": s.emergency_number,
                                                  "clinician_reviewed": rs["emergency"].get("reviewed_by_clinician", False)},
            "demonstration_rules": not rs.get("clinically_approved", False), "ruleset_version": rs["version"],
            "limits": {"max_upload_mb": s.max_upload_mb, "max_audio_mb": s.max_audio_mb, "max_audio_seconds": s.max_audio_seconds, "max_message_chars": s.max_message_chars},
            "languages_supported": ["en", "hi", "mr", "bn", "hinglish", "auto"], "development_mode": not s.is_production,
            "features": {"llm": llm.status()["available"], "speech": stt.status()["available"], "ocr": ocr.tesseract_status()["available"]}}


# -------------------------------------------------------------------------------------------- consent / profile
class ConsentIn(BaseModel):
    accept: bool
    text_version: str


@router.post("/patient/consent", status_code=201)
def give_consent(body: ConsentIn, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    if not body.accept or body.text_version != CONSENT_VERSION:
        raise err(422, "consent_invalid", "Consent must be given to the current notice.")
    db.add(Consent(user_id=user.id, text_version=CONSENT_VERSION))
    audit.record(db, "consent.granted", actor=user, entity_type="user", entity_id=user.id, details={"version": CONSENT_VERSION})
    return {"granted": True, "text_version": CONSENT_VERSION}


@router.post("/patient/consent/withdraw")
def withdraw_consent(user: User = Depends(require_patient), db: Session = Depends(get_db)):
    now = utcnow()
    n = 0
    for c in db.execute(select(Consent).where(Consent.user_id == user.id, Consent.withdrawn_at.is_(None))).scalars():
        c.withdrawn_at = now
        n += 1
    audit.record(db, "consent.withdrawn", actor=user, entity_type="user", entity_id=user.id)
    return {"granted": False, "withdrawn": n, "note": "No new cases can be started. To delete existing cases, use 'Request deletion' on each case."}


class ProfileIn(BaseModel):
    birth_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    age: Optional[int] = Field(default=None, ge=1, le=150)
    mobile: Optional[str] = Field(default=None, max_length=24)
    preferred_language: str = Field(default="en", pattern="^(en|hi|hi-Latn|ta|te|bn|mr|gu|kn|ml|pa|or|ur)$")


@router.put("/patient/profile")
def update_profile(body: ProfileIn, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    p = db.get(PatientProfile, user.id) or PatientProfile(user_id=user.id)
    changed = []
    try:
        if body.age is not None:
            p.age = validate_age(body.age)
            changed.append("age")
        if body.mobile is not None:
            p.mobile_e164 = normalise_mobile(body.mobile)
            changed.append("mobile")
    except ValueError as e:
        raise err(422, "invalid_profile", str(e))
    p.birth_year, p.preferred_language = body.birth_year, body.preferred_language
    db.add(p)
    audit.record(db, "profile.updated", actor=user, entity_type="user", entity_id=user.id, details={"fields": changed})  # field names only, never values
    return {"birth_year": p.birth_year, "age": p.age, "mobile": p.mobile_e164, "preferred_language": p.preferred_language}


def _consented(db: Session, user: User) -> bool:
    return db.execute(select(Consent.id).where(Consent.user_id == user.id, Consent.withdrawn_at.is_(None), Consent.text_version == CONSENT_VERSION)).first() is not None


def _own_case(db: Session, user: User, case_id: str, *, writable: bool = False) -> Case:
    case = load_case_for(user, case_id, db)
    if writable:
        if case.status == "deletion_requested":
            raise err(409, "case_closed", "This case has a pending deletion request and cannot be changed.")
        if not _consented(db, user):
            raise err(403, "consent_required", "Consent is required before submitting or processing health information.")
    return case


# -------------------------------------------------------------------------------------------- cases
@router.post("/cases", status_code=201)
def create_case(user: User = Depends(require_patient), db: Session = Depends(get_db)):
    if not _consented(db, user):
        raise err(403, "consent_required", "Please read and accept the consent notice before starting.")
    prof = db.get(PatientProfile, user.id)
    c = Case(case_number="CF-" + uuid.uuid4().hex[:8].upper(), patient_id=user.id, status="draft", is_synthetic=user.is_synthetic or not get_settings().is_production,
             language=prof.preferred_language if prof else "en")
    db.add(c)
    db.flush()
    audit.record(db, "case.created", actor=user, entity_type="case", entity_id=c.id, case_id=c.id)
    return patient_case_view(db, c)


@router.get("/cases")
def my_cases(user: User = Depends(require_patient), db: Session = Depends(get_db)):
    rows = db.execute(select(Case).where(Case.patient_id == user.id, Case.status != "deleted").order_by(Case.created_at.desc())).scalars().all()
    return {"cases": [{"id": c.id, "case_number": c.case_number, "status": c.status, "status_label": _status_label(c), "chief_complaint": c.chief_complaint,
                       "created_at": c.created_at, "submitted_at": c.submitted_at, "open_questions": db.scalar(select(func.count()).select_from(FollowUpQuestion).where(
                           FollowUpQuestion.case_id == c.id, FollowUpQuestion.status == "open"))} for c in rows]}


def _status_label(c: Case) -> str:
    return {"draft": "Not yet submitted", "submitted": "Submitted - waiting for the care team to review", "in_review": "Being reviewed by the care team",
            "awaiting_patient": "The care team has asked you a question", "resolved": "Reviewed by the care team",
            "deletion_requested": "Deletion requested", "deleted": "Deleted"}[c.status]


def patient_case_view(db: Session, c: Case) -> dict:
    msgs = db.execute(select(Message).where(Message.case_id == c.id).order_by(Message.seq)).scalars().all()
    ups = db.execute(select(Upload).where(Upload.case_id == c.id).order_by(Upload.created_at)).scalars().all()
    qs = db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == c.id).order_by(FollowUpQuestion.asked_at)).scalars().all()
    facts = F.effective_facts(db, c.id)

    def _understood_label(f: Fact) -> str:
        base = f.label or f.concept
        subj = getattr(f, "subject", "patient") or "patient"
        if subj == "family_member":
            rel = getattr(f, "relationship_to_patient", "family member") or "family member"
            rel_str = rel if rel != "unknown" else "family member"
            return f"{base} ({rel_str})"
        return base

    def _understood_polarity(f: Fact) -> str:
        subj = getattr(f, "subject", "patient") or "patient"
        status = getattr(f, "assertion_status", "present") or "present"
        if subj == "family_member" or status.startswith("family-history"):
            return "family_history_positive" if f.polarity == "present" else "family_history_negative"
        if status in ("suspected", "possible"):
            return "suspected"
        return f.polarity

    understood = [{"id": f.id, "label": _understood_label(f), "polarity": _understood_polarity(f), "temporal": f.temporal, "category": f.category,
                   "detail": ", ".join(x for x in (f.duration, f.onset, f.severity, f.progression) if x), "value": f.value_num, "unit": f.unit,
                   "subject": getattr(f, "subject", "patient") or "patient", "relationship_to_patient": getattr(f, "relationship_to_patient", "patient") or "patient",
                   "assertion_status": getattr(f, "assertion_status", "present") or "present"}
                  for f in facts if f.category != "demographic" or True]
    ps = c.patient_summary if isinstance(c.patient_summary, dict) else {}
    draft_summary = ps.get("patient_draft_summary")
    if not draft_summary and (facts or msgs or ups):
        from ..services import summary as SM
        try:
            ps = SM.generate_summary(db, c)
            draft_summary = ps.get("patient_draft_summary")
        except Exception:
            draft_summary = None
    return {"id": c.id, "case_number": c.case_number, "status": c.status, "status_label": _status_label(c), "created_at": c.created_at, "submitted_at": c.submitted_at,
            "processing": c.processing_state == "processing", "messages": [S.message_dict(m) for m in msgs if m.kind != "staff_note"],
            "uploads": [S.upload_dict(u) for u in ups if u.status != "discarded"], "questions": [S.question_dict(q) for q in qs if q.status not in ("withdrawn", "deferred")],
            "understood": understood, "draft_summary": draft_summary, "emergency_flagged": c.emergency_flagged, "language": c.language, "can_submit": c.status == "draft", "deletion_requested_at": c.deletion_requested_at}


@router.get("/cases/{case_id}")
def get_my_case(case_id: str, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    return patient_case_view(db, _own_case(db, user, case_id))


class EmergencyIn(BaseModel):
    text: str = Field(max_length=5000)


@router.post("/cases/{case_id}/emergency-check")
def emergency_check(case_id: str, body: EmergencyIn, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    _own_case(db, user, case_id)
    return safety.screen(body.text, pipeline.active_ruleset())


class MessageIn(BaseModel):
    text: str = Field(min_length=1)
    client_token: Optional[str] = Field(default=None, max_length=64)


@router.post("/cases/{case_id}/messages", status_code=201)
def add_message(case_id: str, body: MessageIn, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    text = body.text.strip()
    if not text:
        raise err(422, "empty_message", "Please write something first.")
    if len(text) > get_settings().max_message_chars:
        raise err(422, "message_too_long", f"Messages are limited to {get_settings().max_message_chars} characters.")
    if body.client_token:
        dup = db.execute(select(Message).where(Message.case_id == case.id, Message.client_token == body.client_token)).scalar_one_or_none()
        if dup:  # idempotent retry (double click / flaky network)
            return {"message": S.message_dict(dup), "emergency": safety.screen(dup.text, pipeline.active_ruleset()), "duplicate": True}
    seq = (db.scalar(select(func.max(Message.seq)).where(Message.case_id == case.id)) or 0) + 1
    lang, support = L.detect_language(text)
    m = Message(case_id=case.id, seq=seq, author_role="patient", kind="text", text=text, language=lang, language_support=support, client_token=body.client_token)
    db.add(m)
    em = safety.screen(text, pipeline.active_ruleset())  # synchronous, before any AI work
    if em["triggered"]:
        case.emergency_flagged = True
        escalation.escalate(db, case, "emergency", source=f"patient message #{seq} (rule-based red-flag screen)", reason="; ".join(em.get("concepts", []))[:200])
    case.last_patient_activity_at = case.updated_at = utcnow()
    db.flush()
    audit.record(db, "message.added", actor=user, entity_type="message", entity_id=m.id, case_id=case.id, details={"seq": seq, "language": lang, "emergency_screen": em["triggered"]})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information" if case.status != "draft" else "draft_update")
    return {"message": S.message_dict(m), "emergency": em, "duplicate": False}


class AnswerIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    choice: Optional[str] = Field(default=None, max_length=40)


@router.post("/cases/{case_id}/questions/{qid}/answer")
def answer_question(case_id: str, qid: str, body: AnswerIn, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    q = db.get(FollowUpQuestion, qid)
    if not q or q.case_id != case.id:
        raise err(404, "question_not_found", "Question not found.")
    try:
        res = followup.answer(db, case, q, text=body.text, choice=body.choice, user=user)
    except ValueError as e:
        raise err(422, "invalid_answer", str(e))
    case.last_patient_activity_at = case.updated_at = utcnow()
    if body.text.strip():
        ans_em = safety.screen(body.text, pipeline.active_ruleset())  # a follow-up answer can reveal a red flag too
        if ans_em["triggered"]:
            escalation.escalate(db, case, "emergency", source="follow-up answer (rule-based red-flag screen)", reason="; ".join(ans_em.get("concepts", []))[:200])
    if case.status == "awaiting_patient" and not db.scalar(select(func.count()).select_from(FollowUpQuestion).where(
            FollowUpQuestion.case_id == case.id, FollowUpQuestion.status == "open", FollowUpQuestion.key.like("staff:%"))):
        case.status = "in_review"
    audit.record(db, "question.answered", actor=user, entity_type="question", entity_id=q.id, case_id=case.id, details={"key": q.key, "status": q.status})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "clarification")
    return {"question": S.question_dict(q), **res}


@router.post("/cases/{case_id}/submit")
def submit_case(case_id: str, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    if case.status != "draft":
        return {"status": case.status, "already_submitted": True}  # idempotent: no duplicate queue entry
    has_text = db.scalar(select(func.count()).select_from(Message).where(Message.case_id == case.id, Message.kind == "text")) or 0
    has_up = db.scalar(select(func.count()).select_from(Upload).where(Upload.case_id == case.id, Upload.status == "confirmed")) or 0
    if not has_text and not has_up:
        raise err(422, "nothing_to_submit", "Please describe your symptoms (or confirm an upload) before submitting.")
    case.status, case.submitted_at, case.updated_at = "submitted", utcnow(), utcnow()
    audit.record(db, "case.submitted", actor=user, entity_type="case", entity_id=case.id, case_id=case.id)
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "submission")
    return {"status": "submitted", "already_submitted": False}


class DeletionIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/cases/{case_id}/deletion-request")
def request_deletion(case_id: str, body: DeletionIn, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id)
    if case.status == "deletion_requested":
        return {"status": case.status, "already_requested": True}
    case.status, case.deletion_requested_at, case.updated_at = "deletion_requested", utcnow(), utcnow()
    audit.record(db, "case.deletion_requested", actor=user, entity_type="case", entity_id=case.id, case_id=case.id)
    return {"status": "deletion_requested", "note": "An administrator will review your request. Records that must be retained by law or policy (for example an approved clinical note) may be kept."}


@router.get("/cases/{case_id}/deletion-options")
def deletion_options(case_id: str, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    """Tell the patient, before they act, what deleting would do and why anything must be retained."""
    case = _own_case(db, user, case_id)
    ok, why = lifecycle.direct_delete_allowed(db, case)
    return {"can_delete_now": ok, "retention_notice": why, "can_request_deletion": case.status not in ("deletion_requested", "deleted"),
            "explanation": ("Deleting removes your messages, files and extracted information immediately." if ok else
                            "This record cannot be erased immediately. You can ask for deletion; restricted records are kept only as policy requires and are hidden from normal access.")}


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    """Direct, immediate deletion of the patient's own draft (or, under the synthetic-demo policy, an unreviewed submitted case)."""
    case = _own_case(db, user, case_id)
    ok, why = lifecycle.direct_delete_allowed(db, case)
    if not ok:
        raise err(409, "deletion_not_immediate", why or "This case cannot be deleted immediately. Use a deletion request.")
    was = case.status
    result = lifecycle.purge_case_content(db, case)
    audit.record(db, "case.deleted_by_patient", actor=user, entity_type="case", entity_id=case.id, case_id=case.id,
                 details={"previous_status": was, "outcome": "completed", **result})
    db.commit()  # success is reported only after the deletion is durable
    return {"deleted": True, "status": "deleted"}


@router.delete("/cases/{case_id}/uploads/{uid}")
def delete_upload(case_id: str, uid: str, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    """Delete an attachment the patient owns, while the case is still an unsubmitted draft (or the file was never confirmed)."""
    case = _own_case(db, user, case_id, writable=True)
    _, up = _own_upload(db, user, case_id, uid)
    if case.status != "draft" and up.status == "confirmed":
        raise err(409, "retained_attachment", "A confirmed file on a submitted case is part of the record. Use a case deletion request instead.")
    try:
        U.path_for(up).unlink(missing_ok=True)
    except OSError:
        raise err(500, "deletion_failed", "The file could not be removed. Nothing was deleted; please try again.")
    for f in db.execute(select(Fact).where(Fact.case_id == case.id, Fact.source_type.in_(("audio", "document")), Fact.source_id == up.id)).scalars():
        db.delete(f)
    kind = up.kind
    db.delete(up)
    case.updated_at = utcnow()
    audit.record(db, "upload.deleted_by_patient", actor=user, entity_type="upload", entity_id=uid, case_id=case.id, details={"kind": kind, "outcome": "completed"})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information" if case.status != "draft" else "draft_update")
    return {"deleted": True}


# -------------------------------------------------------------------------------------------- uploads
@router.post("/cases/{case_id}/uploads", status_code=202)
async def upload_file(
    case_id: str,
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    case = _own_case(db, user, case_id, writable=True)
    s = get_settings()
    data = await file.read(max(s.max_upload_mb, s.max_audio_mb) * 1024 * 1024 + 1)
    kind = None
    if data:  # Let store() preserve its established empty-file response for legacy clients.
        try:
            kind, _, _ = U.sniff(data)
        except U.UploadRejected as e:
            raise err(e.status, e.code, e.message)
    selected_language = language or "auto"
    if kind == "audio" and selected_language not in ("en", "hi", "mr", "bn", "hinglish", "auto"):
        raise err(422, "invalid_language", "Choose English, Hindi, Marathi, Bengali, Hinglish, or automatic detection.")
    count = db.scalar(select(func.count()).select_from(Upload).where(Upload.case_id == case.id, Upload.status != "discarded")) or 0
    try:
        up, is_new = U.store(db, case, data, file.filename or "upload", count)
    except U.UploadRejected as e:
        raise err(e.status, e.code, e.message)
    if is_new:
        audit.record(db, "upload.added", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id, details={"kind": up.kind, "size": up.size_bytes})
        db.commit()
        bg.add_task(U.process_upload, up.id, selected_language if up.kind == "audio" else None)
    elif up.kind == "audio" and (up.selected_language != selected_language or up.status == "failed") and up.status in ("needs_confirmation", "failed"):
        up.selected_language, up.status, up.error = selected_language, "pending", None
        up.detected_language, up.language_confidence = None, None
        up.extracted_text, up.translated_text, up.confirmed_text = "", None, None
        up.confirmed_at, up.segments, up.flags, up.mean_confidence = None, [], [], None
        audit.record(db, "upload.transcription_retried", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id, details={"selected_language": selected_language})
        db.commit()
        bg.add_task(U.process_upload, up.id, selected_language)
    return {"upload": S.upload_dict(up), "duplicate": not is_new}


class RetryAudioIn(BaseModel):
    language: str = "auto"
    translate: Optional[bool] = None


@router.post("/cases/{case_id}/uploads/{uid}/retry", status_code=202)
def retry_audio(case_id: str, uid: str, body: RetryAudioIn, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    _, up = _own_upload(db, user, case_id, uid)
    if up.kind != "audio":
        raise err(422, "not_audio", "Only audio uploads can be transcribed again.")
    if body.language not in ("en", "hi", "mr", "bn", "hinglish", "auto"):
        raise err(422, "invalid_language", "Choose English, Hindi, Marathi, Bengali, Hinglish, or automatic detection.")
    if up.status not in ("needs_confirmation", "failed"):
        raise err(409, "not_ready", "This recording cannot be retried in its current state.")
    up.selected_language, up.status, up.error = body.language, "pending", None
    up.detected_language, up.language_confidence = None, None
    up.extracted_text, up.translated_text, up.confirmed_text = "", None, None
    up.confirmed_at, up.segments, up.flags, up.mean_confidence = None, [], [], None
    audit.record(db, "upload.transcription_retried", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id, details={"selected_language": body.language})
    db.commit()
    if body.translate is not None:
        bg.add_task(U.process_upload, up.id, body.language, body.translate)
    else:
        bg.add_task(U.process_upload, up.id, body.language)
    return S.upload_dict(up)


def _own_upload(db: Session, user: User, case_id: str, uid: str) -> tuple[Case, Upload]:
    case = load_case_for(user, case_id, db)
    up = db.get(Upload, uid)
    if not up or up.case_id != case.id:
        raise err(404, "upload_not_found", "File not found.")
    return case, up


@router.get("/cases/{case_id}/uploads/{uid}")
def get_upload(case_id: str, uid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, up = _own_upload(db, user, case_id, uid)
    return S.upload_dict(up)


@router.get("/cases/{case_id}/uploads/{uid}/file")
def download_upload(case_id: str, uid: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    case, up = _own_upload(db, user, case_id, uid)
    if user.role == "admin":
        raise err(403, "forbidden", "Administrators cannot open case files.")
    if up.status == "discarded":
        raise err(404, "file_missing", "This file was discarded and is no longer available.")
    p = U.path_for(up)
    if not p.exists():
        raise err(404, "file_missing", "The stored file is no longer available.")
    file_size = p.stat().st_size
    headers = {
        "Content-Disposition": f'inline; filename="{up.id}{p.suffix}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; media-src 'self'; sandbox",
        "Accept-Ranges": "bytes",
    }
    range_header = request.headers.get("range")
    if range_header and file_size > 0:
        m = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})
        start_s, end_s = m.group(1), m.group(2)
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
        else:
            suffix_len = int(end_s)
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        if start >= file_size or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{file_size}"})
        end = min(end, file_size - 1)
        length = end - start + 1
        audit.record(db, "upload.viewed", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id, details={"range": f"{start}-{end}"})

        def _iter_range():
            with open(p, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            _iter_range(),
            status_code=206,
            media_type=up.mime,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{file_size}", "Content-Length": str(length)},
        )
    audit.record(db, "upload.viewed", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id)
    return FileResponse(p, media_type=up.mime, filename=up.original_filename, headers=headers)


class ConfirmIn(BaseModel):
    text: str = Field(min_length=1, max_length=60000)


@router.post("/cases/{case_id}/uploads/{uid}/confirm")
def confirm_upload(case_id: str, uid: str, body: ConfirmIn, bg: BackgroundTasks, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    _, up = _own_upload(db, user, case_id, uid)
    if up.status not in ("needs_confirmation", "confirmed"):
        raise err(409, "not_ready", "This file has not finished processing, or could not be read.")
    if up.status == "confirmed" and up.confirmed_text == body.text:
        return S.upload_dict(up)
    if up.status == "confirmed":
        raise err(409, "already_confirmed", "This file was already confirmed. Add a message to correct anything.")
    up.confirmed_text, up.confirmed_at, up.status = body.text, utcnow(), "confirmed"
    edited = body.text.strip() != up.extracted_text.strip()
    case.last_patient_activity_at = case.updated_at = utcnow()
    audit.record(db, "upload.confirmed", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id, details={"edited_by_patient": edited})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information" if case.status != "draft" else "draft_update")
    return S.upload_dict(up)


@router.post("/cases/{case_id}/uploads/{uid}/discard")
def discard_upload(case_id: str, uid: str, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    case = _own_case(db, user, case_id, writable=True)
    _, up = _own_upload(db, user, case_id, uid)
    if up.status == "confirmed":
        raise err(409, "already_confirmed", "A confirmed file is part of the record and cannot be discarded. Ask for case deletion instead.")
    try:
        U.path_for(up).unlink(missing_ok=True)
    except OSError:
        pass
    up.status, up.extracted_text, up.translated_text, up.segments = "discarded", "", None, []
    audit.record(db, "upload.discarded", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id)
    return {"ok": True}
