"""Response shaping. Internal paths, hashes and other users' data are never serialised."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Assessment, Case, ClinicalNote, Conflict, Decision, Fact, FollowUpQuestion, Message, Upload, User, utcnow)
from .services import facts as F


def waiting_minutes(case: Case) -> Optional[int]:
    if not case.submitted_at:
        return None
    return max(0, int((utcnow() - case.submitted_at).total_seconds() // 60))


def effective_queue(case: Case) -> Optional[str]:
    if case.confirmed_queue and not case.reassessment_pending:
        return case.confirmed_queue
    return case.proposed_queue


def case_row(case: Case, patient_label: str = "", reviewer_label: str = "", db: Optional[Session] = None) -> dict:
    facts = F.effective_facts(db, case.id) if db is not None else []
    assessment = db.get(Assessment, case.current_assessment_id) if db is not None and case.current_assessment_id else None
    vitals = {f.concept: {"value": f.value_num, "value2": f.value_num2, "unit": f.unit} for f in facts if f.category == "vital"}
    symptom_facts = [f for f in facts if f.category == "symptom" and f.polarity == "present"]
    ages = [f.value_num for f in facts if f.category == "demographic" and f.concept == "age" and f.value_num is not None]
    sources = set()
    if db is not None:
        if db.query(Message.id).filter(Message.case_id == case.id, Message.author_role == "patient", Message.kind == "text").first():
            sources.add("text")
        for kind in db.query(Upload.kind).filter(Upload.case_id == case.id, Upload.status != "discarded").distinct():
            sources.add("voice" if kind[0] == "audio" else "document")
    return {"id": case.id, "case_number": case.case_number, "status": case.status, "queue": effective_queue(case), "proposed_queue": case.proposed_queue,
            "confirmed_queue": case.confirmed_queue, "reassessment_pending": case.reassessment_pending, "chief_complaint": case.chief_complaint,
            "language": case.language, "submitted_at": case.submitted_at, "updated_at": case.updated_at, "waiting_minutes": waiting_minutes(case),
            "assigned_to": case.assigned_to, "assigned_label": reviewer_label, "patient_label": patient_label, "emergency_flagged": case.emergency_flagged,
            "is_synthetic": case.is_synthetic, "sort_tier": case.sort_tier, "escalation": case.sort_escalation, "processing_state": case.processing_state,
            "age": max(ages) if ages else None, "symptom_duration": next((f.duration or f.onset for f in symptom_facts if f.duration or f.onset), None),
            "worsening": any(f.progression in ("worsening", "rapidly_worsening") for f in symptom_facts), "vitals": vitals,
            "reason": assessment.summary if assessment else "Assessment is still processing.",
            "missing_count": len(assessment.missing_information or []) if assessment else 0,
            "conflict_count": len(assessment.open_conflict_ids or []) if assessment else 0, "sources": sorted(sources)}


def message_dict(m: Message) -> dict:
    return {"id": m.id, "seq": m.seq, "author_role": m.author_role, "kind": m.kind, "text": m.text, "language": m.language, "language_support": m.language_support,
            "text_en": m.text_en, "translation_flags": m.translation_flags or [], "question_id": m.question_id, "created_at": m.created_at}


def upload_dict(u: Upload, include_text: bool = True) -> dict:
    d = {"id": u.id, "kind": u.kind, "filename": u.original_filename, "mime": u.mime, "size_bytes": u.size_bytes, "status": u.status, "error": u.error,
         "method": u.method, "detected_language": u.detected_language, "language_confidence": u.language_confidence, "flags": u.flags or [],
         "mean_confidence": u.mean_confidence, "confirmed": u.confirmed_at is not None, "created_at": u.created_at}
    if include_text:
        d.update(extracted_text=u.extracted_text, translated_text=u.translated_text, confirmed_text=u.confirmed_text,
                 segments=[{k: v for k, v in s.items()} for s in (u.segments or [])])
    return d


def question_dict(q: FollowUpQuestion) -> dict:
    return {"id": q.id, "key": q.key, "text": q.text, "reason": q.reason, "answer_type": q.answer_type, "options": q.options or [], "concept": q.concept,
            "status": q.status, "answer_text": q.answer_text, "answer_choice": q.answer_choice, "asked_at": q.asked_at, "answered_at": q.answered_at,
            "conflict_id": q.conflict_id}


def conflict_dict(c: Conflict, users: Optional[dict] = None) -> dict:
    return {"id": c.id, "kind": c.kind, "concept": c.concept, "safety_critical": c.safety_critical, "description": c.description, "status": c.status,
            "clarification": c.clarification, "resolution_text": c.resolution_text, "resolved_by": (users or {}).get(c.resolved_by, c.resolved_by),
            "resolved_at": c.resolved_at, "evidence": c.evidence_considered, "llm_opinion": c.llm_opinion, "created_at": c.created_at,
            "fact_ids": [x for x in (c.fact_a_id, c.fact_b_id) if x]}


def assessment_dict(a: Assessment) -> dict:
    return {"id": a.id, "ruleset_version": a.ruleset_version, "demonstration_only": a.demonstration_only, "trigger": a.trigger, "proposed_queue": a.proposed_queue,
            "summary": a.summary, "triggered_rules": a.triggered_rules, "supporting_fact_ids": a.supporting_fact_ids, "missing_information": a.missing_information,
            "open_conflict_ids": a.open_conflict_ids, "data_quality": a.data_quality, "failure": a.failure, "changed_from": a.changed_from, "created_at": a.created_at,
            "sort_tier": a.sort_tier, "escalation": a.sort_escalation}


def decision_dict(d: Decision, users: dict) -> dict:
    return {"id": d.id, "assessment_id": d.assessment_id, "proposed_queue": d.proposed_queue, "final_queue": d.final_queue, "action": d.action, "reason": d.reason,
            "decided_by": users.get(d.decided_by, d.decided_by), "decided_at": d.decided_at}


def note_dict(n: ClinicalNote, stale: bool = False) -> dict:
    return {"id": n.id, "version": n.version, "status": n.status, "ai_generated": n.ai_generated, "content": n.content, "change_summary": n.change_summary or [],
            "edited_by": n.edited_by, "reviewed_by": n.reviewed_by, "reviewed_at": n.reviewed_at, "review_reason": n.review_reason, "created_at": n.created_at,
            "updated_at": n.updated_at, "stale": stale,
            "label": {"draft": "AI-assisted DRAFT - not clinician-approved", "approved": "Approved by clinician", "rejected": "Rejected", "superseded": "Superseded"}[n.status]}


def user_names(db: Session, ids) -> dict:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: u.display_name or u.email for u in db.execute(select(User).where(User.id.in_(ids))).scalars()}
