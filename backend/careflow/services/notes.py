"""Draft referral / triage note. Generated from structured evidence (deterministically, no LLM prose) so every
sentence is traceable. It is labelled DRAFT until an authorised clinician edits and approves it."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Assessment, Case, ClinicalNote, Conflict, FollowUpQuestion, Message, User, utcnow
from . import facts as F

DISCLAIMER = ("AI-ASSISTED DRAFT - not clinician-approved. Generated from patient-provided information and a rules-based proposal; "
              "it contains no diagnosis. A qualified clinician must review, edit and approve it before it is relied upon.")
EDITABLE = ["presenting_complaint", "symptom_timeline", "relevant_history", "vital_signs", "findings", "report_findings", "proposed_urgency",
            "outstanding_and_uncertain", "reviewer_comments", "final_disposition"]


def _src(f) -> str:
    return f"[{f.source_type}{': ' + f.locator if f.locator else ''}]"


def build_content(db: Session, case: Case) -> dict:
    facts = F.effective_facts(db, case.id)
    asm: Optional[Assessment] = db.get(Assessment, case.current_assessment_id) if case.current_assessment_id else None
    conflicts = list(db.execute(select(Conflict).where(Conflict.case_id == case.id)).scalars())
    qs = list(db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == case.id)).scalars())
    first = db.execute(select(Message).where(Message.case_id == case.id, Message.kind == "text").order_by(Message.seq).limit(1)).scalar_one_or_none()

    def line(f) -> str:
        bits = [f.label or f.concept]
        for lab, v in (("onset", f.onset), ("duration", f.duration), ("frequency", f.frequency), ("severity (as reported)", f.severity), ("course", f.progression)):
            if v:
                bits.append(f"{lab}: {v}")
        if f.temporal in ("past", "resolved"):
            bits.append(f"timing: {f.temporal}")
        return f"{'; '.join(bits)} {_src(f)}"

    sym_present = [f for f in facts if f.category == "symptom" and f.polarity == "present"]
    sym_denied = [f for f in facts if f.category == "symptom" and f.polarity == "denied"]
    sym_unc = [f for f in facts if f.category == "symptom" and f.polarity == "uncertain"]
    vitals = [f for f in facts if f.category == "vital"]
    history = [f for f in facts if f.category in ("history", "medication", "allergy", "context")]
    reports = [f for f in facts if f.source_type == "document"]

    def vline(f) -> str:
        val = f"{f.value_num:g}/{(f.value_num2 or 0):g}" if f.concept == "blood_pressure" else f"{f.value_num:g}"
        when = f.measured_at.isoformat() if f.measured_at else "time not recorded"
        warn = " - UNRELIABLE (" + ", ".join(x for x in f.flags if x in ("implausible_value", "unit_missing") or x.startswith("unit_may")) + ")" if any(
            x in ("implausible_value", "unit_missing") or x.startswith("unit_may") for x in f.flags) else ""
        return f"{f.label}: {val} {f.unit or '(unit not stated)'}, {when}, source {f.source_type}{warn}"

    findings = {
        "positive": [line(f) for f in sym_present if f.temporal in ("current", "unknown")],
        "resolved_or_past": [line(f) for f in sym_present if f.temporal in ("past", "resolved")],
        "negative_explicitly_denied": [f"{f.label or f.concept} {_src(f)}" for f in sym_denied],
        "uncertain": [f"{f.label or f.concept} {_src(f)}" for f in sym_unc],
        "conflicting": [f"{c.concept}: {c.description} [{c.status}]" for c in conflicts],
        "not_provided": [m["label"] for m in (asm.missing_information if asm else [])],
    }
    proposed = ""
    if asm:
        proposed = (f"Proposed queue (rules engine, NOT a clinical decision): {asm.proposed_queue}"
                    f"{' - DEMONSTRATION ruleset ' + asm.ruleset_version if asm.demonstration_only else ' - ruleset ' + asm.ruleset_version}.\n{asm.summary}")
        for r in asm.triggered_rules:
            proposed += f"\n- {r['rule_id']}: {r['name']} - {r['rationale']}"
    return {
        "disclaimer": DISCLAIMER,
        "presenting_complaint": (first.text if first else "")[:600],
        "symptom_timeline": [line(f) for f in sym_present],
        "relevant_history": [f"{f.label or f.concept}: {f.polarity}{' (' + f.temporal + ')' if f.temporal not in ('unknown',) else ''} {_src(f)}" for f in history],
        "vital_signs": [vline(f) for f in vitals],
        "findings": findings,
        "report_findings": [f"{f.label or f.concept} ({f.polarity}) {_src(f)} \"{f.excerpt[:100]}\"" for f in reports],
        "proposed_urgency": proposed,
        "outstanding_and_uncertain": {
            "open_questions": [q.text for q in qs if q.status == "open"],
            "data_quality": [d["message"] for d in (asm.data_quality if asm else [])],
            "unresolved_conflicts": [c.description for c in conflicts if c.status == "unresolved"],
        },
        "reviewer_comments": "",
        "final_disposition": "",
        "meta": {"assessment_id": asm.id if asm else None, "inputs_digest": asm.inputs_digest if asm else None, "generated_at": utcnow().isoformat()},
    }


def latest(db: Session, case_id: str) -> Optional[ClinicalNote]:
    return db.execute(select(ClinicalNote).where(ClinicalNote.case_id == case_id).order_by(ClinicalNote.version.desc()).limit(1)).scalar_one_or_none()


def create_draft(db: Session, case: Case, user: Optional[User]) -> ClinicalNote:
    prev = latest(db, case.id)
    if prev and prev.status == "draft":
        prev.status = "superseded"
    note = ClinicalNote(case_id=case.id, version=(prev.version + 1) if prev else 1, status="draft", ai_generated=True, content=build_content(db, case),
                        created_by=user.id if user else None)
    db.add(note)
    db.flush()
    return note


def is_stale(db: Session, case: Case, note: ClinicalNote) -> bool:
    asm = db.get(Assessment, case.current_assessment_id) if case.current_assessment_id else None
    return bool(asm and note.content.get("meta", {}).get("inputs_digest") != asm.inputs_digest)


def apply_edit(note: ClinicalNote, changes: dict, user: User) -> list[dict]:
    diff = []
    content = dict(note.content)
    for k, v in changes.items():
        if k not in EDITABLE:
            raise ValueError(f"'{k}' is not an editable section")
        if content.get(k) != v:
            diff.append({"section": k, "before": content.get(k), "after": v, "by": user.id, "at": utcnow().isoformat()})
            content[k] = v
    if diff:
        note.content = content
        note.ai_generated = False if note.ai_generated and diff else note.ai_generated
        note.edited_by = user.id
        note.change_summary = [*(note.change_summary or []), *diff]
    return diff


def render_text(note: ClinicalNote) -> str:
    c = note.content
    status = {"draft": "DRAFT (not approved)", "approved": "APPROVED", "rejected": "REJECTED", "superseded": "SUPERSEDED"}[note.status]
    out = [f"CAREFLOW TRIAGE / REFERRAL NOTE - {status} (v{note.version})", c.get("disclaimer", ""), "",
           "PRESENTING COMPLAINT", str(c.get("presenting_complaint", "")), "", "SYMPTOMS AND TIMELINE", *[f"- {x}" for x in c.get("symptom_timeline", [])], "",
           "RELEVANT HISTORY / MEDICINES / ALLERGIES", *[f"- {x}" for x in c.get("relevant_history", [])], "", "VITAL SIGNS", *[f"- {x}" for x in c.get("vital_signs", [])], ""]
    for k, lab in (("positive", "POSITIVE"), ("negative_explicitly_denied", "EXPLICITLY DENIED"), ("uncertain", "UNCERTAIN"), ("conflicting", "CONFLICTING"), ("not_provided", "NOT PROVIDED")):
        items = c.get("findings", {}).get(k, [])
        if items:
            out += [lab, *[f"- {x}" for x in items], ""]
    out += ["REPORT FINDINGS", *[f"- {x}" for x in c.get("report_findings", [])], "", "PROPOSED URGENCY", str(c.get("proposed_urgency", "")), "", "OUTSTANDING / UNCERTAIN"]
    o = c.get("outstanding_and_uncertain", {})
    out += [f"- {x}" for k in ("open_questions", "data_quality", "unresolved_conflicts") for x in o.get(k, [])]
    out += ["", "REVIEWER COMMENTS", str(c.get("reviewer_comments", "")), "", "FINAL DISPOSITION", str(c.get("final_disposition", ""))]
    if note.status == "approved":
        out += ["", f"Approved by clinician id {note.reviewed_by} at {note.reviewed_at.isoformat() if note.reviewed_at else ''}"]
    return "\n".join(out)
