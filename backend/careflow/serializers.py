"""Response shaping. Internal paths, hashes and other users' data are never serialised."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Assessment, Case, ClinicalNote, Conflict, Decision, Fact, FollowUpQuestion, Message, PatientProfile, Upload, User, utcnow)
from .services import facts as F


def waiting_minutes(case: Case) -> Optional[int]:
    if not case.submitted_at:
        return None
    return max(0, int((utcnow() - case.submitted_at).total_seconds() // 60))


def effective_queue(case: Case) -> Optional[str]:
    if case.confirmed_queue and not case.reassessment_pending:
        return case.confirmed_queue
    return case.proposed_queue


def case_row(
    case: Case,
    patient_label: str = "",
    reviewer_label: str = "",
    db: Optional[Session] = None,
    queue_rank: Optional[int] = None,
    category_rank: Optional[int] = None,
    ranking_explanation: Optional[str] = None,
    ranking_factors: Optional[list[str]] = None,
) -> dict:
    facts = F.effective_facts(db, case.id) if db is not None else []
    assessment = db.get(Assessment, case.current_assessment_id) if db is not None and case.current_assessment_id else None
    vitals = {f.concept: {"value": f.value_num, "value2": f.value_num2, "unit": f.unit} for f in facts if f.category == "vital"}
    symptom_facts = [f for f in facts if f.category == "symptom" and f.polarity == "present"]
    ages = [f.value_num for f in facts if f.category == "demographic" and f.concept == "age" and f.value_num is not None]
    prof = db.get(PatientProfile, case.patient_id) if db is not None else None
    age_val = int(max(ages)) if ages else (prof.age if prof and prof.age else None)
    sources = set()
    if db is not None:
        if db.query(Message.id).filter(Message.case_id == case.id, Message.author_role == "patient", Message.kind == "text").first():
            sources.add("text")
        for kind in db.query(Upload.kind).filter(Upload.case_id == case.id, Upload.status != "discarded").distinct():
            sources.add("voice" if kind[0] == "audio" else "document")
    red_flags = []
    if assessment and assessment.triggered_rules:
        red_flags.extend(f"{r['rule_id']}: {r['name']}" for r in assessment.triggered_rules[:3])
    if case.emergency_flagged and not any("emergency" in x.lower() for x in red_flags):
        red_flags.insert(0, "Emergency intake warning triggered")
    if case.escalation_level == "emergency":
        red_flags.insert(0, "Escalated to EMERGENCY")
    ps = case.patient_summary if isinstance(case.patient_summary, dict) else {}
    prop_prio = ps.get("proposed_priority") or {}
    if not ranking_explanation:
        ranking_explanation = prop_prio.get("ranking_explanation") or (assessment.summary if assessment else "Assessment is still processing.")
    if ranking_factors is None:
        ranking_factors = prop_prio.get("ranking_factors") or []
    analysis_status = ps.get("analysis_status") or ("failed" if case.processing_state == "failed" else "deterministic_fallback")
    clinician_summary_preview = ps.get("clinician_summary") or (ps.get("presenting_concern") or {}).get("summary_text")
    return {"id": case.id, "case_number": case.case_number, "queue_rank": queue_rank, "category_rank": category_rank or queue_rank,
            "status": case.status, "queue": effective_queue(case), "proposed_queue": case.proposed_queue,
            "confirmed_queue": case.confirmed_queue, "reassessment_pending": case.reassessment_pending, "chief_complaint": case.chief_complaint,
            "language": case.language, "submitted_at": case.submitted_at, "updated_at": case.updated_at, "waiting_minutes": waiting_minutes(case),
            "assigned_to": case.assigned_to, "assigned_label": reviewer_label, "patient_label": patient_label, "emergency_flagged": case.emergency_flagged,
            "is_synthetic": case.is_synthetic, "sort_tier": case.sort_tier, "escalation": case.sort_escalation, "processing_state": case.processing_state,
            "age": age_val, "symptom_duration": next((f.duration or f.onset for f in symptom_facts if f.duration or f.onset), None),
            "worsening": any(f.progression in ("worsening", "rapidly_worsening") for f in symptom_facts), "vitals": vitals,
            "red_flags": red_flags,
            "reason": assessment.summary if assessment else "Assessment is still processing.",
            "ranking_explanation": ranking_explanation,
            "ranking_factors": ranking_factors,
            "analysis_status": analysis_status,
            "clinician_summary_preview": clinician_summary_preview,
            "missing_count": len(assessment.missing_information or []) if assessment else 0,
            "conflict_count": len(assessment.open_conflict_ids or []) if assessment else 0, "sources": sorted(sources), **urgency_fields(case, assessment)}


def urgency_fields(case: Case, assessment: Optional[Assessment]) -> dict:
    """Urgency and 'needs review' are separate dimensions. Needs review can coexist with ANY urgency level and never lowers it.
    EMERGENCY comes only from deterministic escalation (red-flag screen / staff escalation); URGENT from the rules engine or a staff/clinician raise;
    NOT_DETERMINED means the rules could not propose a level from the available information (it is not 'standard')."""
    queue = effective_queue(case)
    if case.emergency_flagged or case.escalation_level == "emergency":
        urgency = "EMERGENCY"
    elif queue == "URGENT" or case.escalation_level == "urgent":
        urgency = "URGENT"
    elif queue == "NORMAL":
        urgency = "STANDARD"
    else:
        urgency = "NOT_DETERMINED"
    flags = []
    if assessment is not None:
        if assessment.open_conflict_ids:
            flags.append("unresolved_contradiction")
        if assessment.missing_information:
            flags.append("missing_information")
        flags.extend(x.get("code", "") for x in (assessment.data_quality or []))
    if queue == "NEEDS_REVIEW" or case.processing_state == "failed":
        flags.append("rules_could_not_propose_urgency")
    if case.review_requested:
        flags.append("clinician_review_requested")
    if case.reassessment_pending:
        flags.append("reassessment_pending")
    return {"urgency": urgency, "needs_review": bool(flags), "review_flags": sorted({f for f in flags if f}), "escalation_level": case.escalation_level,
            "review_requested": case.review_requested}


def message_dict(m: Message) -> dict:
    return {"id": m.id, "seq": m.seq, "author_role": m.author_role, "kind": m.kind, "text": m.text, "language": m.language, "language_support": m.language_support,
            "text_en": m.text_en, "translation_flags": m.translation_flags or [], "question_id": m.question_id, "created_at": m.created_at}


def upload_dict(u: Upload, include_text: bool = True) -> dict:
    d = {"id": u.id, "kind": u.kind, "filename": u.original_filename, "mime": u.mime, "size_bytes": u.size_bytes, "status": u.status, "error": u.error,
         "method": u.method, "selected_language": u.selected_language, "detected_language": u.detected_language, "language_confidence": u.language_confidence, "flags": u.flags or [],
         "mean_confidence": u.mean_confidence, "confirmed": u.confirmed_at is not None, "created_at": u.created_at}
    if include_text:
        d.update(extracted_text=u.extracted_text, translated_text=u.translated_text, confirmed_text=u.confirmed_text, lab_results=u.lab_results or [],
                 segments=[{k: v for k, v in s.items()} for s in (u.segments or [])])
    return d


def question_dict(q: FollowUpQuestion) -> dict:
    return {"id": q.id, "key": q.key, "text": q.text, "reason": q.reason, "answer_type": q.answer_type, "options": q.options or [], "concept": q.concept,
            "status": q.status, "answer_text": q.answer_text, "answer_choice": q.answer_choice, "asked_at": q.asked_at, "answered_at": q.answered_at,
            "conflict_id": q.conflict_id}


def conflict_dict(c: Conflict, users: Optional[dict] = None, db: Optional[Session] = None) -> dict:
    ev = c.evidence_considered or []
    stmt_a = ev[0].get("excerpt", "")[:160] if ev and isinstance(ev[0], dict) else ""
    src_a = ev[0].get("source_type", "unknown") if ev and isinstance(ev[0], dict) else "unknown"
    stmt_b = ev[1].get("excerpt", "")[:160] if len(ev) > 1 and isinstance(ev[1], dict) else None
    src_b = ev[1].get("source_type") if len(ev) > 1 and isinstance(ev[1], dict) else None
    if db is not None:
        from .services import reconcile as R
        detail = R.describe_conflict_detail(db, c)
    else:
        label = (c.concept or "").replace("_", " ").replace(":", " ")
        detail = {
            "statement_a": stmt_a,
            "source_a": src_a,
            "statement_b": stmt_b,
            "source_b": src_b,
            "why_they_disagree": c.description,
            "clarification_needed": f"Clarify {label} with the patient or source record.",
            "safety_critical": bool(c.safety_critical),
        }
    return {"id": c.id, "kind": c.kind, "concept": c.concept, "safety_critical": c.safety_critical, "description": c.description, "status": c.status,
            "clarification": c.clarification, "resolution_text": c.resolution_text, "resolved_by": (users or {}).get(c.resolved_by, c.resolved_by),
            "resolved_at": c.resolved_at, "evidence": c.evidence_considered, "llm_opinion": c.llm_opinion, "created_at": c.created_at,
            "fact_ids": [x for x in (c.fact_a_id, c.fact_b_id) if x], **detail}


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
