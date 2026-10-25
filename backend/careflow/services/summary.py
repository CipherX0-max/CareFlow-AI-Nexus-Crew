"""Structured, schema-validated patient case summary for human reviewers (Stage 2: MedGemma + deterministic synthesis).

Synthesises validated structured facts, transcripts, lab findings, contradictions, missing information,
urgency indicators, confidence/verification status, and follow-up Q&A into a cohesive evidence-grounded overview.
Stored against the case (`Case.patient_summary`) and regenerated only when the case `inputs_digest` changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Assessment, Case, Conflict, ExtractionRun, FollowUpQuestion, Message, PatientProfile, Upload, utcnow
from . import facts as F, lab_extractor as LE, llm, priority_scorer as PS, prompts, reconcile as R

log = logging.getLogger("careflow.summary")


class SummaryFactItem(BaseModel):
    concept: str
    label: str
    polarity: str  # present | denied | uncertain
    temporal: str
    detail: Optional[str] = None
    source_label: str  # patient_reported | machine_extracted | clinician_confirmed | ai_inferred
    locator: Optional[str] = None
    verification: str
    subject: str = "patient"
    relationship_to_patient: str = "patient"
    assertion_status: str = "present"
    section_heading: Optional[str] = None


class SummaryLabItem(BaseModel):
    test_name: str
    test_id: Optional[str] = None
    value: str
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    interpretation: str
    flag: Optional[str] = None
    report_date: Optional[str] = None
    locator: str
    review_needed: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    verification: str = "unverified"


class PatientSummarySchema(BaseModel):
    status: str = "ok"  # ok | degraded | failed
    analysis_status: str = "deterministic_fallback"  # medgemma_complete | deterministic_fallback | failed
    generated_at: str
    inputs_digest: str
    method: str = "deterministic"  # deterministic | deterministic+llm | deterministic+medgemma
    model: Optional[str] = None
    ai_status_note: Optional[str] = None
    sections_present: list[str] = Field(default_factory=list)
    presenting_concern: dict[str, Any]
    symptoms: Optional[dict[str, list[SummaryFactItem]]] = None
    relevant_history: Optional[dict[str, list[SummaryFactItem]]] = None
    vital_signs: Optional[list[dict[str, Any]]] = None
    report_findings: Optional[list[SummaryLabItem]] = None
    important_findings: Optional[list[dict[str, Any]]] = None
    positive_and_negative_findings: Optional[dict[str, Any]] = None
    clinical_relevance: Optional[list[dict[str, Any]]] = None
    preliminary_interpretation: Optional[str] = None
    contradictions_and_missing: Optional[dict[str, Any]] = None
    urgency_indicators: Optional[dict[str, Any]] = None
    proposed_priority: Optional[dict[str, Any]] = None
    confidence_and_review: Optional[dict[str, Any]] = None
    pending_followups: Optional[list[dict[str, Any]]] = None
    answered_followups: Optional[list[dict[str, Any]]] = None
    clinician_summary: Optional[str] = None
    limitations_and_sources: Optional[dict[str, Any]] = None
    patient_draft_summary: Optional[str] = None


def _provenance_label(f) -> str:
    if f.verification == "staff_verified" or f.source_type == "staff":
        return "clinician_confirmed"
    if f.origin == "inferred":
        return "ai_inferred"
    if f.source_type in ("document", "audio") and f.verification != "patient_confirmed":
        return "machine_extracted"
    return "patient_reported"


def _fact_item(f) -> SummaryFactItem:
    bits = []
    for k, v in (("onset", f.onset), ("duration", f.duration), ("frequency", f.frequency), ("severity", f.severity), ("course", f.progression)):
        if v:
            bits.append(f"{k}: {v}")
    if f.value_num is not None:
        val_str = f"{f.value_num:g}/{f.value_num2:g}" if f.value_num2 is not None else f"{f.value_num:g}"
        bits.append(f"{val_str} {f.unit or ''}".strip())
    elif f.value_text:
        bits.append(f.value_text)
    return SummaryFactItem(
        concept=f.concept,
        label=f.label or f.concept.replace("_", " "),
        polarity=f.polarity,
        temporal=f.temporal,
        detail="; ".join(bits) if bits else None,
        source_label=_provenance_label(f),
        locator=f"{f.source_type}{': ' + f.locator if f.locator else ''}",
        verification=f.verification,
        subject=getattr(f, "subject", "patient") or "patient",
        relationship_to_patient=getattr(f, "relationship_to_patient", "patient") or "patient",
        assertion_status=getattr(f, "assertion_status", "present") or "present",
        section_heading=getattr(f, "section_heading", None),
    )


def validate_summary_claims(
    narrative: str,
    allowed_tokens: set[str],
    numbers_in_facts: set[str],
    family_only_concepts: set[str] = (),
    denied_concepts: set[str] = (),
) -> bool:
    """Rigorous Claim-to-Evidence Validator:
    - Never accept narrative attributing a family-only condition to the patient personally.
    - Never accept narrative describing an explicitly denied symptom/condition as present.
    - Ground all numbers and clinical tokens in the validated facts.
    """
    text = (narrative or "").strip()
    if len(text) < 10 or len(text) > 950:
        return False
    nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    if not nums <= numbers_in_facts:
        return False
    lower = text.casefold()
    if not any(tok in lower for tok in allowed_tokens if len(tok) >= 3):
        return False

    for fam_c in family_only_concepts:
        fam_norm = fam_c.replace("_", " ").lower()
        if fam_norm in lower:
            sentences = [s.strip() for s in re.split(r"[.!?\n]", lower) if fam_norm in s]
            for s in sentences:
                if not any(f_ind in s for f_ind in ("family", "mother", "father", "brother", "sister", "relative", "maternal", "paternal")):
                    log.warning("MedGemma narrative falsely attributed family condition %s to patient: %s", fam_norm, s)
                    return False

    for den_c in denied_concepts:
        den_norm = den_c.replace("_", " ").lower()
        if den_norm in lower:
            sentences = [s.strip() for s in re.split(r"[.!?\n]", lower) if den_norm in s]
            for s in sentences:
                if not any(neg in s for neg in ("no ", "not ", "denies ", "denied ", "without ", "absent ", "negative ", "never ")):
                    log.warning("MedGemma narrative claimed denied concept %s as present: %s", den_norm, s)
                    return False

    return True


def _validate_llm_narrative(narrative: str, allowed_tokens: set[str], numbers_in_facts: set[str]) -> bool:
    return validate_summary_claims(narrative, allowed_tokens, numbers_in_facts)


def _build_deterministic_preliminary_interpretation(
    sym_present: list[SummaryFactItem],
    hist_cond: list[SummaryFactItem],
    hist_meds: list[SummaryFactItem],
    report_findings: Optional[list[SummaryLabItem]],
    rel_items: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if rel_items:
        for r in rel_items[:3]:
            parts.append(r.get("cautious_note") or "")
    elif report_findings:
        abn = [f for f in report_findings if f.interpretation in ("above_reference", "below_reference") or f.flag in ("high", "low")]
        if abn and sym_present:
            abn_str = ", ".join(f"{f.test_name} ({f.value} {f.unit or ''}, {f.flag or f.interpretation})".strip() for f in abn[:3])
            sym_str = ", ".join(s.label.lower() for s in sym_present[:3])
            parts.append(
                f"Abnormal report finding(s) ({abn_str}) are noted alongside reported {sym_str}; "
                "a clinician should correlate these findings in context rather than treating them as a standalone diagnosis."
            )
        elif abn:
            abn_str = ", ".join(f"{f.test_name} ({f.value} {f.unit or ''})".strip() for f in abn[:3])
            parts.append(f"Report includes out-of-range finding(s) ({abn_str}) requiring clinical correlation.")
    if hist_cond and sym_present:
        cond_names = ", ".join(c.label for c in hist_cond if c.polarity == "present")
        if cond_names:
            parts.append(f"Reported background of {cond_names} provides relevant clinical context for evaluating {', '.join(s.label.lower() for s in sym_present[:2])}.")
    if hist_meds and any(m.polarity == "present" for m in hist_meds):
        med_names = ", ".join(m.label.replace("Medicine: ", "") for m in hist_meds if m.polarity == "present")
        parts.append(f"Active medication(s) ({med_names}) should be reviewed alongside presenting symptoms and any uploaded lab results.")
    if not parts:
        if sym_present:
            parts.append(
                f"Assessment is currently based on patient-reported {', '.join(s.label.lower() for s in sym_present[:3])}; "
                "no abnormal laboratory correlations or conflicting clinical findings are established."
            )
        else:
            parts.append("Insufficient clinical findings to establish a symptom-to-report correlation; human intake review is required.")
    return " ".join(p for p in parts if p).strip()


def _build_deterministic_clinician_summary(
    det_narrative: str,
    sym_present: list[SummaryFactItem],
    sym_denied: list[SummaryFactItem],
    hist_cond: list[SummaryFactItem],
    hist_meds: list[SummaryFactItem],
    hist_allg: list[SummaryFactItem],
    vitals_sec: Optional[list[dict[str, Any]]],
    report_findings: Optional[list[SummaryLabItem]],
    open_conflicts: list[dict[str, Any]],
    missing_info: list[dict[str, Any]],
    answered_qs: list[dict[str, Any]],
    proposed_queue: Optional[str],
    ranking_explanation: Optional[str],
) -> str:
    sentences: list[str] = [det_narrative]
    if sym_denied:
        neg_labels = ", ".join(s.label.lower() for s in sym_denied[:4])
        sentences.append(f"Pertinent negatives explicitly denied: {neg_labels}.")
    bg_bits: list[str] = []
    pos_cond_personal = [c.label for c in hist_cond if c.polarity == "present" and getattr(c, "subject", "patient") == "patient"]
    pos_cond_family = [
        f"{c.label} ({getattr(c, 'relationship_to_patient', 'family member') if getattr(c, 'relationship_to_patient', 'family member') != 'unknown' else 'family member'})"
        for c in hist_cond
        if getattr(c, "subject", "patient") == "family_member" or getattr(c, "assertion_status", "").startswith("family-history-positive")
    ]
    pos_meds = [m.label.replace("Medicine: ", "") for m in hist_meds if m.polarity == "present"]
    pos_allg = [a.label.replace("Allergy: ", "") for a in hist_allg if a.polarity == "present"]
    if pos_cond_personal:
        bg_bits.append(f"history of {', '.join(pos_cond_personal)}")
    if pos_cond_family:
        bg_bits.append(f"family history of {', '.join(pos_cond_family)}")
    if pos_meds:
        bg_bits.append(f"medications: {', '.join(pos_meds)}")
    if pos_allg:
        bg_bits.append(f"allergies: {', '.join(pos_allg)}")
    if bg_bits:
        sentences.append(f"Background includes {'; '.join(bg_bits)}.")
    if vitals_sec:
        v_str = ", ".join(f"{v['label']} {v['value']} {v.get('unit') or ''}".strip() for v in vitals_sec[:3])
        sentences.append(f"Recorded vitals: {v_str}.")
    if report_findings:
        abn = [f for f in report_findings if f.flag in ("high", "low") or f.interpretation in ("above_reference", "below_reference")]
        if abn:
            abn_s = ", ".join(f"{f.test_name} {f.value} {f.unit or ''} ({f.flag or f.interpretation}; ref {f.reference_range or 'n/a'})".strip() for f in abn[:3])
            sentences.append(f"Notable report findings: {abn_s}.")
        else:
            norm_s = ", ".join(f"{f.test_name} {f.value} {f.unit or ''}".strip() for f in report_findings[:3])
            sentences.append(f"Extracted report findings within printed reference range: {norm_s}.")
    if answered_qs:
        ans_s = "; ".join(f"{q['key'].split(':')[0]}: {q['answer']}" for q in answered_qs[:3])
        sentences.append(f"Follow-up clarifications provided: {ans_s}.")
    if open_conflicts:
        c_s = "; ".join(c["description"] for c in open_conflicts[:2])
        sentences.append(f"Attention — {len(open_conflicts)} unresolved contradiction(s) require reviewer verification: {c_s}")
    if missing_info:
        m_s = ", ".join(m.get("label", "") for m in missing_info[:3] if m.get("label"))
        if m_s:
            sentences.append(f"Still missing: {m_s}.")
    if ranking_explanation:
        sentences.append(ranking_explanation)
    elif proposed_queue:
        sentences.append(f"Proposed intake category: {proposed_queue} (non-diagnostic triage support for clinician confirmation).")
    return " ".join(s.strip() for s in sentences if s and s.strip())


def _build_patient_draft_summary(
    age_val: Optional[int],
    sym_present: list[SummaryFactItem],
    sym_denied: list[SummaryFactItem],
    hist_cond: list[SummaryFactItem],
    hist_meds: list[SummaryFactItem],
    hist_allg: list[SummaryFactItem],
    report_findings: Optional[list[SummaryLabItem]],
    open_qs: list[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if sym_present:
        s_list = ", ".join(f"{s.label.lower()}{f' ({s.detail})' if s.detail else ''}" for s in sym_present[:4])
        parts.append(f"You reported {s_list}.")
    else:
        parts.append("Your intake submission has been recorded for clinical review.")
    if sym_denied:
        parts.append(f"You noted no {', '.join(s.label.lower() for s in sym_denied[:3])}.")
    bg: list[str] = []
    if age_val:
        bg.append(f"age {age_val}")
    pos_personal = [c.label for c in hist_cond if c.polarity == "present" and getattr(c, "subject", "patient") == "patient"]
    pos_family = [
        f"{c.label} ({getattr(c, 'relationship_to_patient', 'family member') if getattr(c, 'relationship_to_patient', 'family member') != 'unknown' else 'family member'})"
        for c in hist_cond
        if getattr(c, "subject", "patient") == "family_member" or getattr(c, "assertion_status", "").startswith("family-history-positive")
    ]
    if pos_personal:
        bg.append(f"conditions: {', '.join(pos_personal)}")
    if pos_family:
        bg.append(f"family history noted: {', '.join(pos_family)}")
    pos_m = [m.label.replace("Medicine: ", "") for m in hist_meds if m.polarity == "present"]
    if pos_m:
        bg.append(f"medicines: {', '.join(pos_m)}")
    pos_a = [a.label.replace("Allergy: ", "") for a in hist_allg if a.polarity == "present"]
    if pos_a:
        bg.append(f"allergies: {', '.join(pos_a)}")
    if bg:
        parts.append(f"Recorded background: {'; '.join(bg)}.")
    if report_findings:
        r_list = ", ".join(f"{f.test_name} ({f.value} {f.unit or ''})".strip() for f in report_findings[:3])
        parts.append(f"Extracted from your uploaded report(s): {r_list}.")
    if open_qs:
        parts.append(f"There {'is' if len(open_qs) == 1 else 'are'} {len(open_qs)} follow-up question(s) below to help the care team complete your intake.")
    else:
        parts.append("Your summary has been prepared for the clinical team.")
    return " ".join(parts)


def generate_summary(db: Session, case: Case, asm: Optional[Assessment] = None, *, force: bool = False) -> dict:
    """Build or reuse the validated structured summary for `case`."""
    if asm is None and case.current_assessment_id:
        asm = db.get(Assessment, case.current_assessment_id)

    facts = F.effective_facts(db, case.id)
    msgs = list(db.execute(select(Message).where(Message.case_id == case.id, Message.kind != "staff_note").order_by(Message.seq)).scalars())
    ups = list(db.execute(select(Upload).where(Upload.case_id == case.id, Upload.status != "discarded").order_by(Upload.created_at)).scalars())
    conflicts = list(db.execute(select(Conflict).where(Conflict.case_id == case.id).order_by(Conflict.created_at)).scalars())
    qs = list(db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == case.id).order_by(FollowUpQuestion.priority, FollowUpQuestion.asked_at)).scalars())
    runs = list(db.execute(select(ExtractionRun).where(ExtractionRun.case_id == case.id)).scalars())
    prof = db.get(PatientProfile, case.patient_id)

    digest_blob = json.dumps([
        asm.inputs_digest if asm else "",
        case.confirmed_queue,
        [(f.id, f.verification, f.polarity, f.temporal, f.duration, f.severity, f.progression, f.value_num, f.value_text) for f in facts],
        [(m.id, m.seq, m.text) for m in msgs],
        [(u.id, u.status, len(u.lab_results or []), str(u.confirmed_at)) for u in ups],
        [(c.id, c.status, c.clarification) for c in conflicts],
        [(q.id, q.status, q.answer_text, q.answer_choice) for q in qs],
    ], default=str)
    digest = hashlib.sha256(digest_blob.encode()).hexdigest()[:24]
    existing = case.patient_summary
    if isinstance(existing, dict) and existing.get("inputs_digest") == digest and not force:
        return existing

    # 1. Presenting concern
    first_msg = next((m for m in msgs if m.kind == "text"), None)
    audio_ups = [u for u in ups if u.kind == "audio" and (u.confirmed_text or u.extracted_text)]
    primary_text = (first_msg.text if first_msg else (audio_ups[0].confirmed_text or audio_ups[0].extracted_text if audio_ups else case.chief_complaint or "")).strip()
    primary_en = (first_msg.text_en if first_msg and first_msg.text_en else (audio_ups[0].translated_text if audio_ups else None))
    age_val = prof.age if prof and prof.age else next((int(f.value_num) for f in facts if f.concept == "age" and f.value_num is not None), None)

    sym_present = [_fact_item(f) for f in facts if f.category == "symptom" and f.polarity == "present"]
    sym_denied = [_fact_item(f) for f in facts if f.category == "symptom" and f.polarity == "denied"]
    sym_uncertain = [_fact_item(f) for f in facts if f.category == "symptom" and f.polarity == "uncertain"]

    if sym_present:
        lead_syms = ", ".join(
            f"{s.label} ({s.detail})" if s.detail else s.label for s in sym_present[:4]
        )
        age_phrase = f"{age_val}-year-old patient" if age_val else "Patient"
        det_narrative = f"{age_phrase} presenting with {lead_syms} (patient-reported)."
    elif primary_text:
        det_narrative = f"Patient-reported concern: \"{primary_text[:220]}\""
    else:
        det_narrative = "No presenting symptoms have been recorded yet."

    presenting_concern: dict[str, Any] = {
        "summary_text": det_narrative,
        "patient_words": primary_text[:300] if primary_text else None,
        "english_rendering": primary_en[:300] if primary_en and primary_en != primary_text else None,
        "language": case.language,
        "age": age_val,
        "source_type": "message" if first_msg else ("audio" if audio_ups else "none"),
    }
    sections_present = ["presenting_concern"]

    # 2. Symptoms
    symptoms_sec = None
    if sym_present or sym_denied or sym_uncertain:
        symptoms_sec = {
            "positive": sym_present,
            "negative_explicitly_denied": sym_denied,
            "uncertain": sym_uncertain,
        }
        sections_present.append("symptoms")

    # 3. Relevant history (conditions, medications, allergies)
    hist_cond_personal = [_fact_item(f) for f in facts if f.category in ("history", "context") and getattr(f, "subject", "patient") == "patient"]
    hist_cond_family = [_fact_item(f) for f in facts if f.category in ("history", "context") and (getattr(f, "subject", "patient") == "family_member" or getattr(f, "assertion_status", "").startswith("family-history"))]
    hist_cond = [_fact_item(f) for f in facts if f.category in ("history", "context")]
    hist_meds = [_fact_item(f) for f in facts if f.category == "medication"]
    hist_allg = [_fact_item(f) for f in facts if f.category == "allergy"]
    history_sec = None
    if hist_cond_personal or hist_cond_family or hist_meds or hist_allg:
        history_sec = {
            "conditions": hist_cond_personal,
            "family_history": hist_cond_family,
            "medications": hist_meds,
            "allergies": hist_allg,
        }
        sections_present.append("relevant_history")

    # Vitals
    vital_facts = [f for f in facts if f.category == "vital"]
    vitals_sec = None
    if vital_facts:
        vitals_sec = [
            {
                "concept": f.concept,
                "label": f.label or f.concept.replace("_", " "),
                "value": f"{f.value_num:g}/{f.value_num2:g}" if f.concept == "blood_pressure" and f.value_num2 is not None else (f"{f.value_num:g}" if f.value_num is not None else f.value_text),
                "unit": f.unit,
                "source_label": _provenance_label(f),
                "locator": f.locator,
                "flags": f.flags or [],
            }
            for f in vital_facts
        ]
        sections_present.append("vital_signs")

    # 4. Report findings & Important findings
    raw_labs = [{**r, "upload_id": u.id, "filename": u.original_filename} for u in ups for r in (u.lab_results or [])]
    report_findings_sec: Optional[list[SummaryLabItem]] = None
    important_findings_sec: Optional[list[dict[str, Any]]] = None
    if raw_labs:
        report_findings_sec = [
            SummaryLabItem(
                test_name=r["test_name"],
                test_id=r.get("test_id"),
                value=str(r.get("raw_value", "")),
                unit=r.get("unit"),
                reference_range=r.get("ref_text"),
                interpretation=r.get("interpretation", "not_assessed"),
                flag=r.get("computed_flag") or r.get("printed_flag"),
                report_date=r.get("report_date"),
                locator=f"{r.get('filename', 'report')} ({r.get('locator', '')})",
                review_needed=bool(r.get("review_needed")),
                review_reasons=list(r.get("review_reasons") or []),
                verification=r.get("verification", "unverified"),
            )
            for r in raw_labs
        ]
        important_findings_sec = [
            {
                "test": r["test_name"],
                "value": str(r.get("raw_value", "")),
                "unit": r.get("unit"),
                "reference_range": r.get("ref_text"),
                "flag": r.get("computed_flag") or r.get("printed_flag") or ("normal" if r.get("interpretation") == "within_reference" else "unverified"),
                "interpretation": r.get("interpretation", "not_assessed"),
                "report_date": r.get("report_date"),
                "locator": f"{r.get('filename', 'report')} ({r.get('locator', '')})",
                "review_needed": bool(r.get("review_needed")),
            }
            for r in raw_labs
        ]
        sections_present.append("report_findings")

    # 5. Positive and Negative Findings overview
    pos_neg_sec = {
        "important_positives": [
            *(f"{s.label}{f' ({s.detail})' if s.detail else ''}" for s in sym_present),
            *(f"{c.label}" for c in hist_cond_personal if c.polarity == "present"),
            *(f"Family history: {c.label} ({getattr(c, 'relationship_to_patient', 'family member') if getattr(c, 'relationship_to_patient', 'family member') != 'unknown' else 'family member'})" for c in hist_cond_family if c.polarity == "present"),
            *(f"{f.test_name}: {f.value} {f.unit or ''} ({f.flag})" for f in (report_findings_sec or []) if f.flag in ("high", "low")),
        ],
        "important_negatives": [
            *(f"Denies {s.label.lower()}" for s in sym_denied),
            *(f"Denies {c.label.lower()}" for c in hist_cond + hist_meds + hist_allg if c.polarity == "denied"),
        ],
        "uncertain_or_unverified": [
            *(f"Uncertain: {s.label}" for s in sym_uncertain),
            *(f"Unverified report value: {f.test_name} ({f.value})" for f in (report_findings_sec or []) if f.review_needed),
        ],
    }
    sections_present.append("positive_and_negative_findings")

    # 6. Contradictions and missing information (with both-sides detail)
    open_conflicts = []
    for c in conflicts:
        if c.status != "reviewer_resolved":
            detail = R.describe_conflict_detail(db, c)
            open_conflicts.append({
                "id": c.id,
                "concept": c.concept,
                "kind": c.kind,
                "safety_critical": c.safety_critical,
                "status": c.status,
                "description": c.description,
                **detail,
            })
    missing_info = list(asm.missing_information or []) if asm else []
    dq_flags = list(asm.data_quality or []) if asm else []
    contradictions_sec = None
    if open_conflicts or missing_info or dq_flags:
        contradictions_sec = {
            "contradictions": open_conflicts,
            "missing_information": missing_info,
            "data_quality": dq_flags,
        }
        sections_present.append("contradictions_and_missing")

    # 7. Clinical relevance & Preliminary non-diagnostic interpretation
    symptom_concepts = {f.concept for f in facts if f.category == "symptom" and f.polarity == "present"}
    rel_items = LE.relevant_to_symptoms(raw_labs, symptom_concepts) if raw_labs else []
    clinical_relevance_sec = rel_items if rel_items else None
    if clinical_relevance_sec:
        sections_present.append("clinical_relevance")

    det_prelim_interp = _build_deterministic_preliminary_interpretation(
        sym_present, hist_cond, hist_meds, report_findings_sec, rel_items
    )
    sections_present.append("preliminary_interpretation")

    # 8. Pending and Answered Follow-ups
    open_qs = [
        {"id": q.id, "key": q.key, "text": q.text, "reason": q.reason, "priority": q.priority}
        for q in qs
        if q.status == "open"
    ]
    answered_qs = [
        {
            "id": q.id,
            "key": q.key,
            "question": q.text,
            "answer": q.answer_text or q.answer_choice or "skipped",
            "status": q.status,
            "answered_at": q.answered_at.isoformat() if q.answered_at else None,
        }
        for q in qs
        if q.status in ("answered", "skipped")
    ]
    pending_followups_sec = open_qs if open_qs else None
    if pending_followups_sec:
        sections_present.append("pending_followups")
    answered_followups_sec = answered_qs if answered_qs else None
    if answered_followups_sec:
        sections_present.append("answered_followups")

    # 9. Urgency indicators & Proposed priority (with explainable ranking factors, no numeric score exposed)
    triggered_rules = list(asm.triggered_rules or []) if asm else []
    urgency_indicators_sec = None
    if triggered_rules or case.emergency_flagged or case.escalation_level:
        urgency_indicators_sec = {
            "emergency_flagged": case.emergency_flagged,
            "escalation_level": case.escalation_level,
            "triggered_rules": triggered_rules,
        }
        sections_present.append("urgency_indicators")

    from . import pipeline as P
    try:
        ruleset = P.active_ruleset()
    except Exception:
        ruleset = {"safety_critical_concepts": [], "priority_scoring": {}}

    prio_eval = PS.score_case(
        case,
        facts,
        conflicts,
        asm,
        ruleset,
        summary_payload={"report_findings": {"important_findings": important_findings_sec or []}},
    )

    proposed_priority_sec = None
    if asm:
        proposed_priority_sec = {
            "proposed_queue": asm.proposed_queue,
            "confirmed_queue": case.confirmed_queue,
            "rationale": asm.summary,
            "ranking_explanation": prio_eval.ranking_explanation,
            "ranking_factors": prio_eval.ranking_factors,
            "ruleset_version": asm.ruleset_version,
            "scoring_version": prio_eval.scoring_version,
            "demonstration_only": asm.demonstration_only,
            "sort_tier": asm.sort_tier,
            "sort_escalation": asm.sort_escalation,
        }
        sections_present.append("proposed_priority")

    # 10. Confidence, review status, and Limitations & Sources
    unverified_facts = [
        {"concept": f.concept, "label": f.label or f.concept, "source": f.source_type, "confidence": f.confidence, "flags": f.flags}
        for f in facts
        if f.verification == "unverified" and (f.confidence is not None and f.confidence < 0.75 or f.flags)
    ]
    unconfirmed_uploads = [u.original_filename for u in ups if u.status in ("pending", "processing", "needs_confirmation")]
    failed_uploads = [u.original_filename for u in ups if u.status == "failed"]
    degraded_runs = [r for r in runs if r.status in ("degraded", "failed")]

    status_level = "ok"
    analysis_status = "deterministic_fallback"
    ai_status_note = None
    if asm and asm.failure:
        status_level = "failed"
        analysis_status = "failed"
        ai_status_note = f"Automatic processing encountered an error ({asm.failure}). Verify source records directly."
    elif degraded_runs:
        status_level = "degraded"
        analysis_status = "deterministic_fallback"
        ai_status_note = "Local AI model was unavailable for one or more inputs; structured summary was built from deterministic extraction."

    det_clinician_summary = _build_deterministic_clinician_summary(
        det_narrative,
        sym_present,
        sym_denied,
        hist_cond,
        hist_meds,
        hist_allg,
        vitals_sec,
        report_findings_sec,
        open_conflicts,
        missing_info,
        answered_qs,
        asm.proposed_queue if asm else case.proposed_queue,
        prio_eval.ranking_explanation,
    )
    clinician_summary_text = det_clinician_summary
    prelim_interp_text = det_prelim_interp

    # Stage 2: Local MedGemma (medgemma1.5) evidence analysis and clinician summary synthesis
    method = "deterministic"
    model_used = None
    s = get_settings()
    if s.llm_enabled and (sym_present or report_findings_sec or hist_cond) and status_level == "ok":
        try:
            structured_payload = {
                "age": age_val,
                "symptoms_present": [{"label": x.label, "detail": x.detail, "source": x.source_label} for x in sym_present],
                "symptoms_denied": [x.label for x in sym_denied],
                "history_conditions": [{"label": x.label, "polarity": x.polarity} for x in hist_cond],
                "medications": [{"label": x.label, "polarity": x.polarity} for x in hist_meds],
                "allergies": [{"label": x.label, "polarity": x.polarity} for x in hist_allg],
                "vitals": vitals_sec or [],
                "lab_abnormal": [
                    {"test": r.test_name, "value": r.value, "unit": r.unit, "ref": r.reference_range, "interp": r.interpretation}
                    for r in (report_findings_sec or [])
                    if r.interpretation in ("above_reference", "below_reference") or r.flag in ("high", "low")
                ],
                "contradictions": [{"concept": c["concept"], "description": c["description"]} for c in open_conflicts],
                "missing_information": [m.get("label") for m in missing_info if m.get("label")],
                "answered_followups": [{"key": q["key"], "answer": q["answer"]} for q in answered_qs],
                "proposed_queue": asm.proposed_queue if asm else case.proposed_queue,
                "ranking_factors": prio_eval.ranking_factors[:4],
            }
            target_model = s.medgemma_model or s.ollama_model
            data, meta = llm.chat_json(
                prompts.SUMMARY_SYSTEM,
                json.dumps(structured_payload),
                prompts.SUMMARY_SCHEMA,
                num_predict=450,
                model=target_model,
                timeout=s.medgemma_timeout_seconds,
            )
            cand = str(data.get("presenting_concern_narrative") or "").strip()
            cand_clin = str(data.get("clinician_summary") or "").strip()
            cand_interp = str(data.get("preliminary_interpretation") or data.get("clinical_relevance_note") or "").strip()

            allowed_words = {
                w
                for x in (sym_present + hist_cond + hist_meds + hist_allg)
                for w in re.findall(r"\w+", x.label.casefold())
            } | {
                w
                for r in (report_findings_sec or [])
                for w in re.findall(r"\w+", r.test_name.casefold())
            }
            nums_allowed = set(re.findall(r"\b\d+(?:\.\d+)?\b", json.dumps(structured_payload))) | {"1", "2", "3"}
            family_only_concepts = {
                c.concept
                for c in hist_cond_family
                if not any(p.concept == c.concept for p in hist_cond_personal)
            }
            denied_concepts = {
                f.concept
                for f in facts
                if f.polarity == "denied"
            }

            if validate_summary_claims(cand, allowed_words, nums_allowed, family_only_concepts, denied_concepts):
                presenting_concern["ai_synthesis"] = cand
                method = "deterministic+llm"
                model_used = meta.get("model")
                analysis_status = "medgemma_complete"
            if validate_summary_claims(cand_clin, allowed_words, nums_allowed, family_only_concepts, denied_concepts):
                clinician_summary_text = cand_clin
                method = "deterministic+llm"
                model_used = meta.get("model")
                analysis_status = "medgemma_complete"
            else:
                ai_status_note = (ai_status_note or "") + (" " if ai_status_note else "") + "[Claim validation: clinician summary grounded in deterministic synthesis]"
            if validate_summary_claims(cand_interp, allowed_words, nums_allowed, family_only_concepts, denied_concepts):
                prelim_interp_text = cand_interp
        except llm.LLMUnavailable as exc:
            status_level = "degraded"
            analysis_status = "deterministic_fallback"
            ai_status_note = f"Local MedGemma/LLM summary analysis unavailable ({str(exc)[:120]}); showing deterministic structured summary."

    confidence_sec = {
        "unverified_count": sum(1 for f in facts if f.verification == "unverified"),
        "clinician_verified_count": sum(1 for f in facts if f.verification == "staff_verified"),
        "patient_confirmed_count": sum(1 for f in facts if f.verification == "patient_confirmed"),
        "flagged_items": unverified_facts,
        "unconfirmed_uploads": unconfirmed_uploads,
        "failed_uploads": failed_uploads,
        "ai_status": status_level,
        "analysis_status": analysis_status,
        "ai_status_note": ai_status_note,
    }
    sections_present.append("confidence_and_review")
    sections_present.append("clinician_summary")

    limitations_sec = {
        "non_diagnostic_disclaimer": "CareFlow AI provides intake structuring and decision support only; it does not diagnose or replace clinical judgement.",
        "demo_ruleset_notice": "Urgency categories and within-category rankings use unvalidated engineering rules (DEMONSTRATION ONLY).",
        "analysis_status": analysis_status,
        "model_used": model_used or "deterministic_rules_only",
        "unverified_fact_count": confidence_sec["unverified_count"],
        "open_contradiction_count": len(open_conflicts),
        "missing_item_count": len(missing_info),
        "sources_used": [
            *([f"Patient messages ({len(msgs)})"] if msgs else []),
            *([f"Uploaded files ({len(ups)}: {', '.join(u.original_filename for u in ups[:4])})"] if ups else []),
            *([f"Follow-up answers ({len(answered_qs)})"] if answered_qs else []),
        ],
    }
    sections_present.append("limitations_and_sources")

    patient_draft = _build_patient_draft_summary(
        age_val, sym_present, sym_denied, hist_cond, hist_meds, hist_allg, report_findings_sec, open_qs
    )

    validated = PatientSummarySchema(
        status=status_level,
        analysis_status=analysis_status,
        generated_at=utcnow().isoformat(),
        inputs_digest=digest,
        method=method,
        model=model_used,
        ai_status_note=ai_status_note,
        sections_present=sections_present,
        presenting_concern=presenting_concern,
        symptoms=symptoms_sec,
        relevant_history=history_sec,
        vital_signs=vitals_sec,
        report_findings=report_findings_sec,
        important_findings=important_findings_sec,
        positive_and_negative_findings=pos_neg_sec,
        clinical_relevance=clinical_relevance_sec,
        preliminary_interpretation=prelim_interp_text,
        contradictions_and_missing=contradictions_sec,
        urgency_indicators=urgency_indicators_sec,
        proposed_priority=proposed_priority_sec,
        confidence_and_review=confidence_sec,
        pending_followups=pending_followups_sec,
        answered_followups=answered_followups_sec,
        clinician_summary=clinician_summary_text,
        limitations_and_sources=limitations_sec,
        patient_draft_summary=patient_draft,
    )
    dumped = validated.model_dump(mode="json")
    case.patient_summary = dumped
    return dumped
