from datetime import datetime
import math
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case as sa_case, func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import err, load_case_for, require_staff
from ..models import (Assessment, AuditEvent, Case, ClinicalNote, Conflict, Decision, Fact, FollowUpQuestion, Message, Upload, User, utcnow)
from .. import serializers as S
from ..services import facts as F, followup, notes as N, pipeline, reconcile

router = APIRouter(prefix="/api/staff", tags=["staff"])
OPEN_STATUSES = ("submitted", "in_review", "awaiting_patient")
QUEUES = ("URGENT", "NORMAL", "NEEDS_REVIEW")
TRANSITIONS = {"submitted": {"in_review", "awaiting_patient", "resolved"}, "in_review": {"awaiting_patient", "resolved", "submitted"},
               "awaiting_patient": {"in_review", "resolved"}, "resolved": {"in_review"}}


def eff_queue_expr():
    return sa_case((and_(Case.confirmed_queue.isnot(None), Case.reassessment_pending.is_(False)), Case.confirmed_queue), else_=Case.proposed_queue)


def _case(db: Session, user: User, cid: str) -> Case:
    c = load_case_for(user, cid, db)
    if c.status == "deletion_requested":
        raise err(409, "case_closed", "This case has a pending deletion request.")
    return c


def _touch(case: Case) -> None:
    case.updated_at = utcnow()


# ------------------------------------------------------------------------------------------ dashboard
@router.get("/queues/summary")
def summary(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    q = eff_queue_expr()
    rows = db.execute(select(q, func.count()).where(Case.status.in_(OPEN_STATUSES)).group_by(q)).all()
    counts = {k: 0 for k in QUEUES}
    for name, n in rows:
        if name in counts:
            counts[name] = n
    unassigned = db.scalar(select(func.count()).where(Case.status.in_(OPEN_STATUSES), Case.assigned_to.is_(None))) or 0
    pending = db.scalar(select(func.count()).where(Case.status.in_(OPEN_STATUSES), Case.reassessment_pending.is_(True))) or 0
    version = db.scalar(select(func.coalesce(func.max(AuditEvent.id), 0))) or 0
    workflow_rows = db.execute(select(Case.status, func.count()).where(Case.status.notin_(("draft", "deletion_requested"))).group_by(Case.status)).all()
    workflow_counts = {k: 0 for k in ("submitted", "in_review", "awaiting_patient", "resolved")}
    for status_name, count in workflow_rows:
        workflow_counts[status_name] = count
    return {"counts": counts, "total_open": sum(counts.values()), "unassigned": unassigned, "reassessment_pending": pending, "version": version,
            "processing": db.scalar(select(func.count()).where(Case.processing_state == "processing")) or 0,
            "total_cases": sum(workflow_counts.values()), "workflow_counts": workflow_counts}


@router.get("/cases")
def list_cases(queue: Optional[Literal["URGENT", "NORMAL", "NEEDS_REVIEW"]] = None, status: Optional[str] = None, q: Optional[str] = Query(default=None, max_length=100),
               assigned: Optional[str] = Query(default=None, max_length=40), date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
               source: Optional[Literal["text", "voice", "document"]] = None,
               page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=20, ge=1, le=100),
               user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Deterministic ordering: rule tier asc, worsening indicator desc, longest-waiting first (submitted_at asc), then case id."""
    eq = eff_queue_expr()
    conds = [Case.status != "draft", Case.status != "deletion_requested"]
    if status:
        sts = [x for x in status.split(",") if x]
        conds.append(Case.status.in_(sts))
    else:
        conds.append(Case.status.in_(OPEN_STATUSES))
    if queue:
        conds.append(eq == queue)
    if assigned:
        conds.append(Case.assigned_to.is_(None) if assigned == "unassigned" else Case.assigned_to == (user.id if assigned == "me" else assigned))
    if date_from:
        conds.append(Case.submitted_at >= date_from)
    if date_to:
        conds.append(Case.submitted_at <= date_to)
    if q:
        like = f"%{q.strip().lower()}%"
        fact_hit = select(Fact.case_id).where(or_(func.lower(Fact.concept).like(like), func.lower(Fact.label).like(like)))
        patient_hit = select(User.id).where(User.role == "patient", or_(func.lower(User.display_name).like(like), func.lower(User.email).like(like)))
        conds.append(or_(func.lower(Case.case_number).like(like), func.lower(Case.chief_complaint).like(like), Case.id.in_(fact_hit), Case.patient_id.in_(patient_hit)))
    if source == "text":
        conds.append(select(Message.id).where(Message.case_id == Case.id, Message.author_role == "patient", Message.kind == "text").exists())
    elif source == "voice":
        conds.append(select(Upload.id).where(Upload.case_id == Case.id, Upload.kind == "audio", Upload.status != "discarded").exists())
    elif source == "document":
        conds.append(select(Upload.id).where(Upload.case_id == Case.id, Upload.kind.in_(("pdf", "image")), Upload.status != "discarded").exists())
    total = db.scalar(select(func.count()).select_from(Case).where(*conds)) or 0
    rows = db.execute(select(Case).where(*conds).order_by(Case.sort_tier.asc(), Case.sort_escalation.desc(), Case.submitted_at.asc(), Case.id.asc())
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    names = S.user_names(db, [c.patient_id for c in rows] + [c.assigned_to for c in rows])
    return {"cases": [S.case_row(c, names.get(c.patient_id, ""), names.get(c.assigned_to, ""), db) for c in rows], "total": total, "page": page, "page_size": page_size,
            "ordering": "queue; rule tier asc; worsening indicator desc; waiting time (longest first); case id"}


@router.get("/reviewers")
def reviewers(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return {"reviewers": [{"id": u.id, "name": u.display_name or u.email} for u in db.execute(select(User).where(User.role == "staff", User.is_active.is_(True)).order_by(User.display_name)).scalars()]}


# ------------------------------------------------------------------------------------------ detail
def case_detail(db: Session, case: Case, viewer: User) -> dict:
    msgs = db.execute(select(Message).where(Message.case_id == case.id).order_by(Message.seq)).scalars().all()
    ups = db.execute(select(Upload).where(Upload.case_id == case.id).order_by(Upload.created_at)).scalars().all()
    facts = F.all_facts(db, case.id)
    conflicts = db.execute(select(Conflict).where(Conflict.case_id == case.id).order_by(Conflict.created_at)).scalars().all()
    qs = db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == case.id).order_by(FollowUpQuestion.asked_at)).scalars().all()
    assessments = db.execute(select(Assessment).where(Assessment.case_id == case.id).order_by(Assessment.created_at.desc())).scalars().all()
    decisions = db.execute(select(Decision).where(Decision.case_id == case.id).order_by(Decision.decided_at.desc())).scalars().all()
    notes = db.execute(select(ClinicalNote).where(ClinicalNote.case_id == case.id).order_by(ClinicalNote.version.desc())).scalars().all()
    events = db.execute(select(AuditEvent).where(AuditEvent.case_id == case.id).order_by(AuditEvent.id.desc()).limit(200)).scalars().all()
    names = S.user_names(db, [case.patient_id, case.assigned_to, case.confirmed_by] + [d.decided_by for d in decisions] + [c.resolved_by for c in conflicts] + [e.actor_id for e in events])
    by_fact: dict[str, list] = {}
    for c in conflicts:
        for fid in [c.fact_a_id, c.fact_b_id] + [e.get("fact_id") for e in c.evidence_considered if isinstance(e, dict)]:
            if fid:
                by_fact.setdefault(fid, []).append(c.id)
    current = next((a for a in assessments if a.id == case.current_assessment_id), None)
    row = S.case_row(case, names.get(case.patient_id, ""), names.get(case.assigned_to, ""), db)
    return {
        "case": {**row, "confirmed_by": names.get(case.confirmed_by), "confirmed_at": case.confirmed_at, "created_at": case.created_at},
        "messages": [S.message_dict(m) for m in msgs], "uploads": [S.upload_dict(u) for u in ups if u.status != "discarded"],
        "facts": [F.fact_dict(f, by_fact) for f in facts], "conflicts": [S.conflict_dict(c, names) for c in conflicts],
        "questions": [S.question_dict(q) for q in qs], "assessment": S.assessment_dict(current) if current else None,
        "assessment_history": [S.assessment_dict(a) for a in assessments[:25]], "decisions": [S.decision_dict(d, names) for d in decisions],
        "notes": [S.note_dict(n, stale=(n.status == "draft" and N.is_stale(db, case, n))) for n in notes],
        "audit": [{"id": e.id, "ts": e.ts, "actor": names.get(e.actor_id, e.actor_role), "role": e.actor_role, "action": e.action, "details": e.details} for e in events],
        "conflict_outcomes": {c.id: reconcile.outcomes_for(c) for c in conflicts},
    }


@router.get("/cases/{case_id}")
def get_case(case_id: str, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    audit.record(db, "case.viewed", actor=user, entity_type="case", entity_id=case.id, case_id=case.id)
    return case_detail(db, case, user)


# ------------------------------------------------------------------------------------------ workflow
class AssignIn(BaseModel):
    user_id: Optional[str] = None


@router.post("/cases/{case_id}/assign")
def assign(case_id: str, body: AssignIn, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    target = None
    if body.user_id:
        target = db.get(User, body.user_id)
        if not target or target.role != "staff" or not target.is_active:
            raise err(422, "invalid_reviewer", "Reviewer must be an active staff member.")
    prev = case.assigned_to
    case.assigned_to = target.id if target else None
    if target and case.status == "submitted":
        case.status = "in_review"
    _touch(case)
    audit.record(db, "case.assigned", actor=user, entity_type="case", entity_id=case.id, case_id=case.id, details={"from": prev, "to": case.assigned_to})
    return {"assigned_to": case.assigned_to, "status": case.status}


class StatusIn(BaseModel):
    status: Literal["submitted", "in_review", "awaiting_patient", "resolved"]
    note: str = Field(default="", max_length=500)


@router.post("/cases/{case_id}/status")
def set_status(case_id: str, body: StatusIn, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    if body.status != case.status and body.status not in TRANSITIONS.get(case.status, set()):
        raise err(409, "invalid_transition", f"Cannot move a case from {case.status} to {body.status}.")
    if body.status == "resolved" and not case.confirmed_queue:
        raise err(409, "decision_required", "Record a human decision on the urgency category before resolving the case.")
    if body.status == "resolved" and case.reassessment_pending:
        raise err(409, "reassessment_pending", "New information changed the proposal since your decision. Review and re-confirm before resolving.")
    prev, case.status = case.status, body.status
    _touch(case)
    audit.record(db, "case.status_changed", actor=user, entity_type="case", entity_id=case.id, case_id=case.id, details={"from": prev, "to": body.status})
    return {"status": case.status}


class DecisionIn(BaseModel):
    assessment_id: str = Field(max_length=32)
    final_queue: Literal["URGENT", "NORMAL", "NEEDS_REVIEW"]
    reason: str = Field(default="", max_length=2000)


@router.post("/cases/{case_id}/decision", status_code=201)
def decide(case_id: str, body: DecisionIn, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    if case.status == "draft":
        raise err(409, "not_submitted", "Case has not been submitted.")
    asm = db.get(Assessment, case.current_assessment_id) if case.current_assessment_id else None
    if not asm:
        raise err(409, "no_assessment", "No assessment exists yet. Wait for processing or use reassess.")
    if body.assessment_id != asm.id:
        raise err(409, "stale_assessment", "The proposal changed since you opened this case. Reload to review the latest evidence before deciding.")
    if case.processing_state == "processing":
        raise err(409, "processing", "The case is still being processed. Try again in a moment.")
    proposed = asm.proposed_queue
    if body.final_queue == proposed:
        action = "confirm"
    elif body.final_queue == "URGENT":
        action = "escalate"
    elif proposed == "URGENT":
        action = "downgrade"
    else:
        action = "override"
    reason = body.reason.strip()
    if action != "confirm" and len(reason) < 10:
        raise err(422, "reason_required", "A reason of at least 10 characters is required when you change the proposed category.")
    if action == "confirm" and proposed == "NEEDS_REVIEW":
        raise err(422, "cannot_confirm_needs_review", "NEEDS_REVIEW means the information is not sufficient. Resolve the open items or choose URGENT/NORMAL with a reason.")
    d = Decision(case_id=case.id, assessment_id=asm.id, proposed_queue=proposed, final_queue=body.final_queue, action=action, reason=reason or "Confirmed proposal", decided_by=user.id)
    db.add(d)
    case.confirmed_queue, case.confirmed_by, case.confirmed_at, case.reassessment_pending = body.final_queue, user.id, utcnow(), False
    if case.status == "submitted":
        case.status = "in_review"
    _touch(case)
    db.flush()
    audit.record(db, "decision.recorded", actor=user, entity_type="decision", entity_id=d.id, case_id=case.id,
                 details={"action": action, "proposed": proposed, "final": body.final_queue, "assessment": asm.id, "ruleset": asm.ruleset_version})
    return {"decision": S.decision_dict(d, {user.id: user.display_name}), "queue": S.effective_queue(case)}


class ResolveIn(BaseModel):
    outcome: str = Field(max_length=30)
    note: str = Field(min_length=10, max_length=2000)
    evidence_considered: list[str] = Field(default_factory=list, max_length=20)


@router.post("/cases/{case_id}/conflicts/{cid}/resolve")
def resolve_conflict(case_id: str, cid: str, body: ResolveIn, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    c = db.get(Conflict, cid)
    if not c or c.case_id != case.id:
        raise err(404, "conflict_not_found", "Conflict not found.")
    if c.status == "reviewer_resolved":
        raise err(409, "already_resolved", "This conflict was already resolved by a reviewer; the original record is kept.")
    try:
        reconcile.apply_resolution(db, c, body.outcome, by_user=user, by_patient=False, text=body.note, reviewed=body.evidence_considered)
    except ValueError as e:
        raise err(422, "invalid_outcome", str(e))
    _touch(case)
    audit.record(db, "conflict.resolved", actor=user, entity_type="conflict", entity_id=c.id, case_id=case.id, details={"outcome": body.outcome, "kind": c.kind, "concept": c.concept})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "conflict_resolved")
    return {"conflict": S.conflict_dict(c)}


class VerifyIn(BaseModel):
    verification: Literal["staff_verified", "rejected"]
    reason: str = Field(min_length=5, max_length=500)


class FactCorrectionIn(BaseModel):
    changes: dict = Field(min_length=1)
    reason: str = Field(min_length=10, max_length=500)


@router.post("/cases/{case_id}/facts/{fid}/correct", status_code=201)
def correct_fact(case_id: str, fid: str, body: FactCorrectionIn, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Append a staff-verified correction and supersede the extraction; the original source fact remains in the record."""
    case = _case(db, user, case_id)
    original = db.get(Fact, fid)
    if not original or original.case_id != case.id:
        raise err(404, "fact_not_found", "Fact not found.")
    if original.verification == "rejected" or F.is_superseded(original):
        raise err(409, "fact_not_current", "Only a current, non-rejected fact can be corrected.")
    allowed = {"label", "polarity", "temporal", "value_text", "value_num", "value_num2", "unit", "onset", "duration", "frequency", "severity", "progression"}
    if not body.changes or set(body.changes) - allowed:
        raise err(422, "invalid_fact_correction", "Only the displayed fact details can be corrected.")
    choices = {"polarity": {"present", "denied", "uncertain"}, "temporal": {"current", "past", "resolved", "unknown"},
               "progression": {None, "improving", "stable", "worsening", "rapidly_worsening"}}
    for key, value in body.changes.items():
        if key in choices and (not (isinstance(value, str) or value is None) or value not in choices[key]):
            raise err(422, "invalid_fact_correction", f"Invalid {key} value.")
        if key in {"label", "value_text", "unit", "onset", "duration", "frequency", "severity"} and value is not None:
            if not isinstance(value, str) or len(value) > 200:
                raise err(422, "invalid_fact_correction", f"{key} must be text up to 200 characters.")
        if key in {"value_num", "value_num2"} and value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise err(422, "invalid_fact_correction", f"{key} must be a finite number or null.")
    corrected = Fact(case_id=case.id, category=original.category, concept=original.concept,
        label=body.changes.get("label", original.label), polarity=body.changes.get("polarity", original.polarity),
        temporal=body.changes.get("temporal", original.temporal), value_text=body.changes.get("value_text", original.value_text),
        value_num=body.changes.get("value_num", original.value_num), value_num2=body.changes.get("value_num2", original.value_num2),
        unit=body.changes.get("unit", original.unit), onset=body.changes.get("onset", original.onset), duration=body.changes.get("duration", original.duration),
        frequency=body.changes.get("frequency", original.frequency), severity=body.changes.get("severity", original.severity),
        progression=body.changes.get("progression", original.progression), origin="explicit", source_type="staff", source_id=original.id,
        locator=f"correction to {original.source_type} evidence", speaker="staff", excerpt=f"Staff correction: {body.reason.strip()}"[:300],
        stated_at=utcnow(), measured_at=original.measured_at, confidence=1.0, method="staff_correction", verification="staff_verified",
        flags=["staff_correction", f"supersedes:{original.id}"])
    db.add(corrected)
    db.flush()
    F.mark_superseded(original, corrected.id)
    case.updated_at = utcnow()
    audit.record(db, "fact.corrected", actor=user, entity_type="fact", entity_id=corrected.id, case_id=case.id,
                 details={"supersedes": original.id, "changes": body.changes, "reason": body.reason.strip()})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "staff_fact_correction")
    return {"fact": F.fact_dict(corrected), "supersedes": original.id}


@router.post("/cases/{case_id}/facts/{fid}/verify")
def verify_fact(case_id: str, fid: str, body: VerifyIn, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    f = db.get(Fact, fid)
    if not f or f.case_id != case.id:
        raise err(404, "fact_not_found", "Fact not found.")
    prev, f.verification = f.verification, body.verification
    _touch(case)
    audit.record(db, "fact.verified", actor=user, entity_type="fact", entity_id=f.id, case_id=case.id, details={"from": prev, "to": body.verification, "reason": body.reason[:200], "concept": f.concept})
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information")
    return {"fact": F.fact_dict(f)}


@router.post("/cases/{case_id}/messages/{mid}/acknowledge-flags")
def ack_flags(case_id: str, mid: str, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    m = db.get(Message, mid)
    if not m or m.case_id != case.id:
        raise err(404, "message_not_found", "Message not found.")
    m.translation_flags = [f if f.startswith("reviewed:") else f"reviewed:{f}" for f in (m.translation_flags or [])]
    audit.record(db, "message.flags_acknowledged", actor=user, entity_type="message", entity_id=m.id, case_id=case.id)
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information")
    return {"ok": True}


@router.post("/cases/{case_id}/uploads/{uid}/acknowledge-flags")
def ack_upload_flags(case_id: str, uid: str, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    up = db.get(Upload, uid)
    if not up or up.case_id != case.id:
        raise err(404, "upload_not_found", "File not found.")
    up.flags = [f if f.startswith("reviewed:") else f"reviewed:{f}" for f in (up.flags or [])]
    audit.record(db, "upload.flags_acknowledged", actor=user, entity_type="upload", entity_id=up.id, case_id=case.id)
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "new_information")
    return {"ok": True}


class AskIn(BaseModel):
    text: str = Field(min_length=5, max_length=500)
    reason: str = Field(min_length=5, max_length=500)


@router.post("/cases/{case_id}/questions", status_code=201)
def ask_patient(case_id: str, body: AskIn, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    import uuid
    q = FollowUpQuestion(case_id=case.id, key=f"staff:{uuid.uuid4().hex[:10]}", text=body.text.strip(), reason=body.reason.strip(), answer_type="text", priority=5)
    db.add(q)
    case.status = "awaiting_patient"
    _touch(case)
    db.flush()
    audit.record(db, "question.asked_by_staff", actor=user, entity_type="question", entity_id=q.id, case_id=case.id)
    return {"question": S.question_dict(q)}


@router.post("/cases/{case_id}/reassess")
def reassess(case_id: str, bg: BackgroundTasks, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    audit.record(db, "case.reassess_requested", actor=user, entity_type="case", entity_id=case.id, case_id=case.id)
    db.commit()
    bg.add_task(pipeline.process_case, case.id, "manual")
    return {"queued": True}


# ------------------------------------------------------------------------------------------ clinical note
def _note(db: Session, case: Case, nid: str) -> ClinicalNote:
    n = db.get(ClinicalNote, nid)
    if not n or n.case_id != case.id:
        raise err(404, "note_not_found", "Note not found.")
    return n


@router.post("/cases/{case_id}/notes/draft", status_code=201)
def draft_note(case_id: str, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    n = N.create_draft(db, case, user)
    audit.record(db, "note.draft_generated", actor=user, entity_type="note", entity_id=n.id, case_id=case.id, details={"version": n.version})
    return S.note_dict(n)


class NoteEdit(BaseModel):
    changes: dict = Field(min_length=1)


@router.put("/cases/{case_id}/notes/{nid}")
def edit_note(case_id: str, nid: str, body: NoteEdit, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    n = _note(db, case, nid)
    if n.status != "draft":
        raise err(409, "note_locked", f"A {n.status} note cannot be edited. Generate a new draft instead.")
    try:
        diff = N.apply_edit(n, body.changes, user)
    except ValueError as e:
        raise err(422, "invalid_section", str(e))
    audit.record(db, "note.edited", actor=user, entity_type="note", entity_id=n.id, case_id=case.id, details={"sections": [d["section"] for d in diff]})
    return S.note_dict(n)


class NoteReview(BaseModel):
    reason: str = Field(default="", max_length=1000)


@router.post("/cases/{case_id}/notes/{nid}/approve")
def approve_note(case_id: str, nid: str, body: NoteReview, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    n = _note(db, case, nid)
    if n.status != "draft":
        raise err(409, "note_locked", f"Note is already {n.status}.")
    if not case.confirmed_queue:
        raise err(409, "decision_required", "Record a human urgency decision before approving the note.")
    if case.reassessment_pending:
        raise err(409, "reassessment_pending", "New information changed the proposal since the decision. Re-confirm before approving.")
    if N.is_stale(db, case, n):
        raise err(409, "note_stale", "The case changed after this draft was generated. Regenerate the draft to include the latest evidence.")
    n.content = {**n.content, "final_disposition": n.content.get("final_disposition") or f"Urgency category confirmed by reviewer: {case.confirmed_queue}"}
    for old in db.execute(select(ClinicalNote).where(ClinicalNote.case_id == case.id, ClinicalNote.status == "approved")).scalars():
        old.status = "superseded"
    n.status, n.reviewed_by, n.reviewed_at, n.review_reason = "approved", user.id, utcnow(), body.reason or None
    _touch(case)
    audit.record(db, "note.approved", actor=user, entity_type="note", entity_id=n.id, case_id=case.id, details={"version": n.version, "edited": bool(n.change_summary)})
    return S.note_dict(n)


@router.post("/cases/{case_id}/notes/{nid}/reject")
def reject_note(case_id: str, nid: str, body: NoteReview, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    n = _note(db, case, nid)
    if n.status != "draft":
        raise err(409, "note_locked", f"Note is already {n.status}.")
    if len(body.reason.strip()) < 10:
        raise err(422, "reason_required", "Please say why the draft is rejected (10+ characters).")
    n.status, n.reviewed_by, n.reviewed_at, n.review_reason = "rejected", user.id, utcnow(), body.reason.strip()
    audit.record(db, "note.rejected", actor=user, entity_type="note", entity_id=n.id, case_id=case.id)
    return S.note_dict(n)


@router.get("/cases/{case_id}/notes/{nid}/text")
def note_text(case_id: str, nid: str, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    case = _case(db, user, case_id)
    return {"text": N.render_text(_note(db, case, nid))}
