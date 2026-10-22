"""Contradiction detection and evidence reconciliation (between extraction and urgency assessment).

Deterministic checks decide WHETHER a conflict exists; the optional LLM only adds an advisory opinion on its
nature and can never resolve or close one. Resolution only happens through an explicit patient clarification or an
authorised reviewer, and the original conflict + evidence are retained."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Conflict, Fact, User, utcnow
from . import facts as F
from . import llm, prompts

ACTIVE_T = ("current", "unknown")
PRESENCE_OUTCOMES = {
    "present_now": ("present", "current"),
    "earlier_resolved": ("present", "resolved"),
    "not_present": ("denied", "current"),
}
VALUE_OUTCOMES = ("use_a", "use_b")
LABEL = {"present": "has", "denied": "does not have", "uncertain": "is unsure about"}


def _ev(f: Fact) -> dict:
    return {"fact_id": f.id, "source_type": f.source_type, "source_id": f.source_id, "locator": f.locator, "excerpt": f.excerpt,
            "polarity": f.polarity, "temporal": f.temporal, "stated_at": f.stated_at.isoformat() if f.stated_at else None}


def _where(f: Fact) -> str:
    kind = {"message": "typed message", "audio": "voice recording", "document": "uploaded report", "followup": "follow-up answer",
            "staff": "staff entry", "profile": "profile"}.get(f.source_type, f.source_type)
    return f"{kind}{' (' + f.locator + ')' if f.locator else ''}"


def _temp_c(f: Fact) -> Optional[float]:
    if f.value_num is None:
        return None
    return round((f.value_num - 32) * 5 / 9, 1) if (f.unit or "").upper() == "F" else f.value_num


def _existing(db: Session, case_id: str) -> dict[str, list[Conflict]]:
    out: dict[str, list[Conflict]] = {}
    for c in db.execute(select(Conflict).where(Conflict.case_id == case_id).order_by(Conflict.created_at)).scalars():
        out.setdefault(c.key.rsplit("#", 1)[0], []).append(c)
    return out


def _upsert(db: Session, case_id: str, base_key: str, existing: dict, *, kind: str, concept: str, a: Fact, b: Optional[Fact],
            safety: bool, description: str, evidence: list[dict], created: list) -> None:
    """One open conflict per base_key. If earlier ones were all resolved, a NEW contradiction after that opens a new generation."""
    group = existing.setdefault(base_key, [])
    ev_ids = {e["fact_id"] for e in evidence}
    open_ = [c for c in group if c.status == "unresolved"]
    if open_:
        c = open_[-1]
        known = {e["fact_id"] for e in c.evidence_considered}
        if ev_ids - known:
            c.evidence_considered = evidence
            c.description = description
            c.fact_b_id = b.id if b else None
        return
    # previously resolved: only reopen when evidence includes facts not seen in any earlier generation
    seen = {e["fact_id"] for c in group for e in c.evidence_considered}
    if group and not (ev_ids - seen):
        return
    c = Conflict(case_id=case_id, key=f"{base_key}#{len(group) + 1}", kind=kind, concept=concept, fact_a_id=a.id, fact_b_id=b.id if b else None,
                 safety_critical=safety, description=description, evidence_considered=evidence)
    db.add(c)
    db.flush()
    group.append(c)
    created.append(c)


def detect(db: Session, case_id: str, ruleset: dict) -> list[Conflict]:
    safety_set = set(ruleset["safety_critical_concepts"])
    facts = F.effective_facts(db, case_id)
    existing = _existing(db, case_id)
    created: list[Conflict] = []

    # --- presence / absence (symptoms, history, context) -------------------------------------------------
    by_concept: dict[tuple, list[Fact]] = {}
    for f in facts:
        by_concept.setdefault((f.category, f.concept), []).append(f)
    for (cat, concept), group in by_concept.items():
        if cat in ("vital", "demographic"):
            continue
        # a clarification newer than the statements settles the concept, so don't re-flag the same evidence
        pos = [f for f in group if f.polarity == "present" and f.temporal in ACTIVE_T]
        neg = [f for f in group if f.polarity == "denied" and f.temporal in ACTIVE_T]
        if pos and neg:
            a, b = pos[0], neg[-1]
            if cat == "allergy" or cat == "medication":
                kind = cat
            else:
                kind = "presence"
            label = concept.replace("_", " ").replace(":", " ")
            desc = (f"Conflicting statements about {label}: the {a.speaker} {LABEL['present']} it per {_where(a)} (\"{a.excerpt[:90]}\") "
                    f"but {LABEL['denied']} it per {_where(b)} (\"{b.excerpt[:90]}\"). Not the same as a change over time unless clarified.")
            _upsert(db, case_id, f"presence:{concept}", existing, kind=kind, concept=concept, a=a, b=b, safety=concept in safety_set,
                    description=desc, evidence=[_ev(x) for x in pos + neg], created=created)

    # --- medication / allergy "none" vs a named item ---------------------------------------------------
    for any_c, prefix, kind in (("medication_any", "medication:", "medication"), ("allergy_any", "allergy:", "allergy")):
        denied = [f for f in facts if f.concept == any_c and f.polarity == "denied"]
        named = [f for f in facts if f.concept.startswith(prefix) and f.polarity == "present"]
        for n in named:
            if denied:
                d = denied[-1]
                desc = (f"The patient reports no {kind} ({_where(d)}: \"{d.excerpt[:70]}\") but {n.label or n.concept} appears in {_where(n)}: \"{n.excerpt[:90]}\".")
                _upsert(db, case_id, f"{kind}:{n.concept}", existing, kind=kind, concept=n.concept, a=d, b=n, safety=False, description=desc,
                        evidence=[_ev(d), _ev(n)], created=created)

    # --- age ------------------------------------------------------------------------------------------
    ages = [f for f in facts if f.concept == "age" and f.value_num is not None]
    for i in range(len(ages)):
        for j in range(i + 1, len(ages)):
            if abs(ages[i].value_num - ages[j].value_num) > 1:
                a, b = ages[i], ages[j]
                _upsert(db, case_id, "age", existing, kind="age", concept="age", a=a, b=b, safety=False,
                        description=f"Different ages recorded: {a.value_num:g} ({_where(a)}) vs {b.value_num:g} ({_where(b)}).",
                        evidence=[_ev(a), _ev(b)], created=created)

    # --- vitals: same measurement, different values at the same / unknown time --------------------------
    vit: dict[str, list[Fact]] = {}
    for f in facts:
        if f.category == "vital" and f.value_num is not None:
            vit.setdefault(f.concept, []).append(f)
    for name, group in vit.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.measured_at and b.measured_at and a.measured_at != b.measured_at:
                    continue  # repeated measurements at different times are a series, not a contradiction
                va, vb = (_temp_c(a), _temp_c(b)) if name == "temperature" else (a.value_num, b.value_num)
                tol = 0.5 if name == "temperature" else 0.0
                same = abs(va - vb) <= tol and (name != "blood_pressure" or (a.value_num2 or 0) == (b.value_num2 or 0))
                if same:
                    continue
                note = "" if (a.measured_at and b.measured_at) else " Measurement times are not both known, so this may be two different readings."
                _upsert(db, case_id, f"value:{name}", existing, kind="value", concept=name, a=a, b=b, safety=False,
                        description=f"Different {name.replace('_', ' ')} values recorded: {_fmt(a)} ({_where(a)}) vs {_fmt(b)} ({_where(b)}).{note}",
                        evidence=[_ev(a), _ev(b)], created=created)

    # --- implausible value / unit problems --------------------------------------------------------------
    for f in facts:
        if f.category == "vital" and not f.verification.endswith("confirmed") and f.verification != "staff_verified" and \
           any(x in ("implausible_value", "unit_missing") or x.startswith("unit_may_be") for x in (f.flags or [])):
            hint = " The unit may be wrong (e.g. °F vs °C)." if any(x.startswith("unit_may_be") for x in f.flags) else ""
            what = "has no unit" if "unit_missing" in f.flags and "implausible_value" not in f.flags else "is not physiologically plausible as written"
            _upsert(db, case_id, f"unit:{f.id}", existing, kind="unit", concept=f.concept, a=f, b=None, safety=False,
                    description=f"{f.concept.replace('_', ' ').title()} {_fmt(f)} {what} ({_where(f)}).{hint} Treated as unreliable until clarified.",
                    evidence=[_ev(f)], created=created)

    # --- possible negation / translation errors --------------------------------------------------------
    for f in facts:
        flag = next((x for x in (f.flags or []) if x.startswith("translation_disagreement") or x.startswith("extractor_disagreement")), None)
        if flag and f.category == "symptom":
            _upsert(db, case_id, f"negation_risk:{f.id}", existing, kind="negation_risk", concept=f.concept, a=f, b=None, safety=f.concept in safety_set,
                    description=(f"The two readings of this statement disagree on whether {f.concept.replace('_', ' ')} is present ({flag.split(':', 1)[1]}). "
                                 f"A missed or added negation (e.g. speech-recognition or translation error) is possible. Source: {_where(f)} \"{f.excerpt[:90]}\"."),
                    evidence=[_ev(f)], created=created)

    for c in created:
        _maybe_llm_opinion(db, c)
    return created


def _fmt(f: Fact) -> str:
    if f.concept == "blood_pressure":
        return f"{f.value_num:g}/{(f.value_num2 or 0):g} {f.unit or ''}".strip()
    return f"{f.value_num:g} {f.unit or ''}".strip()


def _maybe_llm_opinion(db: Session, c: Conflict) -> None:
    if not get_settings().llm_conflict_opinions or c.kind not in ("presence", "negation_risk"):
        return
    ev = c.evidence_considered[:4]
    user = "\n".join(f"Statement {i + 1} [{e['source_type']}, {e.get('locator') or ''}, {e['polarity']}/{e['temporal']}]: {e['excerpt']}" for i, e in enumerate(ev))
    try:
        out, meta = llm.chat_json(prompts.CONFLICT_SYSTEM, user, prompts.CONFLICT_SCHEMA, num_predict=200)
        if out.get("classification") in prompts.CONFLICT_SCHEMA["properties"]["classification"]["enum"]:
            c.llm_opinion = {"classification": out["classification"], "rationale": str(out.get("rationale", ""))[:300], "model": meta["model"],
                             "prompt_version": prompts.CONFLICT_PROMPT_VERSION, "advisory_only": True}
    except llm.LLMUnavailable:
        pass


# ------------------------------------------------------------------ resolution ---------------------------
def conflict_facts(db: Session, c: Conflict) -> list[Fact]:
    ids = {e["fact_id"] for e in (c.evidence_considered or [])} | {c.fact_a_id} | ({c.fact_b_id} if c.fact_b_id else set())
    return [db.get(Fact, i) for i in ids if db.get(Fact, i)]


def outcomes_for(c: Conflict) -> list[str]:
    if c.kind in ("presence", "negation_risk"):
        return ["present_now", "earlier_resolved", "not_present"]
    if c.kind in ("value", "age", "allergy", "medication"):
        return ["use_a", "use_b"] if c.kind in ("value", "age") else ["present_now", "not_present"]
    if c.kind == "unit":
        return ["unit_F", "unit_C"]
    return []


def apply_resolution(db: Session, c: Conflict, outcome: str, *, by_user: Optional[User], by_patient: bool, text: str = "", reviewed: Optional[list] = None) -> Optional[Fact]:
    """Record a clarification (patient) or a reviewer resolution. Original facts stay as evidence; they are flagged superseded
    where the outcome settles them. 'unsure' leaves the conflict open (a missing answer is never a negative answer)."""
    if outcome == "unsure":
        return None
    allowed = outcomes_for(c)
    if outcome not in allowed:
        raise ValueError(f"outcome must be one of {allowed + ['unsure']}")
    facts = conflict_facts(db, c)
    a = db.get(Fact, c.fact_a_id)
    now = utcnow()
    new: Optional[Fact] = None
    src = "followup" if by_patient else "staff"
    ver = "patient_confirmed" if by_patient else "staff_verified"
    speaker = "patient" if by_patient else "staff"
    if outcome in PRESENCE_OUTCOMES:
        pol, temp = PRESENCE_OUTCOMES[outcome]
        base = db.get(Fact, c.fact_b_id) if c.kind in ("allergy", "medication") and c.fact_b_id else a
        concept, cat, label = base.concept, base.category, base.label
        new = Fact(case_id=c.case_id, category=cat, concept=concept, label=label, polarity=pol, temporal=temp, origin="explicit", source_type=src,
                   source_id=c.id, locator=f"clarification of conflict {c.id[:6]}", speaker=speaker, excerpt=text[:300] or f"Clarified: {outcome.replace('_', ' ')}",
                   stated_at=now, confidence=1.0, method="clarification", verification=ver, flags=["clarification", f"clarifies:{c.id}"])
        # carry measurable attributes from the statement that remains true
        keep = next((f for f in facts if f.polarity == pol), None)
        if keep and pol == "present":
            for fld in ("duration", "onset", "frequency", "severity", "progression"):
                setattr(new, fld, getattr(keep, fld))
        db.add(new)
        db.flush()
        for f in facts:
            if f.id != new.id:
                F.mark_superseded(f, new.id)
    elif outcome in ("use_a", "use_b"):
        keep_id = a.id if outcome == "use_a" else c.fact_b_id
        for f in facts:
            if f.id == keep_id:
                f.verification = ver
            else:
                F.mark_superseded(f, keep_id)
    elif outcome in ("unit_F", "unit_C"):
        unit = "F" if outcome == "unit_F" else "C"
        new = Fact(case_id=c.case_id, category=a.category, concept=a.concept, label=a.label, polarity="present", temporal="current", value_num=a.value_num,
                   value_num2=a.value_num2, unit=unit, source_type=src, source_id=c.id, locator=f"unit confirmation {c.id[:6]}", speaker=speaker,
                   excerpt=text[:300] or f"Unit confirmed as {unit}", stated_at=now, measured_at=a.measured_at, confidence=1.0, method="clarification", verification=ver,
                   flags=["clarification", f"clarifies:{c.id}", "measurement_time_unknown"])
        from .lexicon import _plausibility, FactDraft  # reuse plausibility bounds
        probe = FactDraft(category=a.category, concept=a.concept, value_num=a.value_num, value_num2=a.value_num2, unit=unit)
        new.flags += _plausibility(probe)
        db.add(new)
        db.flush()
        for f in facts:
            F.mark_superseded(f, new.id)
    c.status = "clarified" if by_patient else "reviewer_resolved"
    c.clarification = outcome
    c.resolution_text = text[:1000] or None
    c.resolved_by = None if by_patient else (by_user.id if by_user else None)
    c.resolved_at = now
    if reviewed:
        c.evidence_considered = list(c.evidence_considered) + [{"reviewer_considered": reviewed}]
    return new
