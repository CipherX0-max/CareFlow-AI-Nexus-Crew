"""Configurable, deterministic within-category priority ranking engine.

Safety principles:
1. Safety gate runs FIRST (`rules_engine.evaluate`). Emergency red flags and configured URGENT rules are never
   downgraded by a numerical score, missing measurements, or LLM unavailability.
2. Each queue (`URGENT`, `NEEDS_REVIEW`, `NORMAL`) maintains its own independent 1-based rank order
   (`Urgent #1, #2...`, `Needs Review #1, #2...`, `Normal #1, #2...`).
3. The internal numerical score is used ONLY as a deterministic tie-breaking and within-category ordering signal
   and is NEVER exposed in patient, staff, or doctor API responses.
4. All weights come from the versioned `priority_scoring` section of `demo_ruleset.json` and are explicitly
   marked UNVALIDATED / DEMONSTRATION ONLY."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import Assessment, Case, Conflict, Fact, utcnow
from . import facts as F
from .rules_engine import ACTIVE_TEMPORAL, BLOCKING_FLAGS

HIGH_RISK_HISTORY = {
    "heart_disease",
    "stroke_history",
    "kidney_disease",
    "diabetes",
    "hypertension",
    "asthma_copd",
    "cancer",
}


@dataclass
class PriorityEvaluation:
    case_id: str
    category: str
    category_rank: int = 1
    internal_score: float = 0.0  # INTERNAL ONLY - never serialised to API responses
    sort_tier: int = 99
    sort_escalation: int = 0
    ranking_explanation: str = ""
    ranking_factors: list[str] = field(default_factory=list)
    scoring_version: str = "demo-score-0.1.0-UNVALIDATED"
    clinically_approved: bool = False


def _parse_days(duration_str: Optional[str]) -> Optional[float]:
    if not duration_str:
        return None
    s = duration_str.lower().strip()
    if "yesterday" in s or "last night" in s or "this morning" in s:
        return 1.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minute|hour|day|week|month|year)", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    mult = {"minute": 1 / 1440.0, "hour": 1 / 24.0, "day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}[unit]
    return val * mult


def _severity_level(sev: Optional[str]) -> Optional[str]:
    if not sev:
        return None
    s = sev.lower().strip()
    m = re.match(r"(\d{1,2})\s*/\s*10", s)
    if m:
        n = int(m.group(1))
        if n >= 7:
            return "severe"
        if n >= 4:
            return "moderate"
        return "mild"
    if any(w in s for w in ("severe", "unbearable", "excruciating", "worst", "intense", "very bad", "bahut tez", "bohot tez", "बहुत तेज")):
        return "severe"
    if any(w in s for w in ("moderate", "tez", "तेज")):
        return "moderate"
    if any(w in s for w in ("mild", "slight", "halka", "halki", "हल्का", "थोड़ा")):
        return "mild"
    return None


def _is_unreliable_vital(f: Fact) -> bool:
    verified = f.verification in ("patient_confirmed", "staff_verified")
    flags = f.flags or []
    return f.category == "vital" and not verified and any(any(fl.startswith(p) for p in BLOCKING_FLAGS) for fl in flags)


def score_case(
    case: Case,
    facts: list[Fact],
    conflicts: list[Conflict],
    assessment: Optional[Assessment],
    ruleset: dict,
    *,
    summary_payload: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PriorityEvaluation:
    """Compute deterministic internal priority score and plain-language explanation for a single case."""
    cfg = ruleset.get("priority_scoring") or {}
    weights = cfg.get("weights") or {}
    scoring_ver = cfg.get("scoring_version", "demo-score-0.1.0-UNVALIDATED")
    approved = bool(cfg.get("clinically_approved", False))
    safety_concepts = set(ruleset.get("safety_critical_concepts") or [])

    category = (case.Effective_queue if hasattr(case, "Effective_queue") else None) or case.confirmed_queue or case.proposed_queue or "NEEDS_REVIEW"
    sort_tier = assessment.sort_tier if assessment and assessment.sort_tier is not None else (1 if category == "URGENT" else 3 if category == "NEEDS_REVIEW" else 5)
    sort_esc = assessment.sort_escalation if assessment and assessment.sort_escalation is not None else 0

    active_facts = [f for f in facts if not F.is_superseded(f) and f.verification != "rejected"]
    present_symptoms = [
        f for f in active_facts
        if f.category == "symptom"
        and f.polarity == "present"
        and f.temporal in ACTIVE_TEMPORAL
        and getattr(f, "subject", "patient") == "patient"
        and getattr(f, "assertion_status", "present") in ("present", "suspected", "possible")
    ]
    present_history = [
        f for f in active_facts
        if f.category in ("history", "context")
        and f.polarity == "present"
        and getattr(f, "subject", "patient") == "patient"
        and getattr(f, "assertion_status", "present") in ("present", "historical", "resolved")
    ]
    family_history = [
        f for f in active_facts
        if getattr(f, "subject", "patient") == "family_member"
        or getattr(f, "assertion_status", "").startswith("family-history-positive")
    ]
    vitals = [f for f in active_facts if f.category == "vital" and f.value_num is not None]
    open_conflicts = [c for c in conflicts if c.status == "unresolved" or (c.status == "clarified" and c.safety_critical)]

    score = 0.0
    factors: list[str] = []

    # 1. Safety Gate & Triggered Rules
    triggered = (assessment.triggered_rules if assessment else None) or []
    if case.emergency_flagged:
        score += float(weights.get("emergency_flag", 500))
        factors.append("Emergency red-flag warning triggered")
    if triggered:
        best_tier = min(int(r.get("tier", 2)) for r in triggered)
        if best_tier == 1:
            score += float(weights.get("tier_1_urgent_rule", 300))
        else:
            score += float(weights.get("tier_2_urgent_rule", 180))
        if len(triggered) > 1:
            score += float(weights.get("per_additional_urgent_rule", 40)) * (len(triggered) - 1)
        rule_names = ", ".join(f"{r.get('rule_id')} ({r.get('name')})" for r in triggered[:2])
        factors.append(f"Matched urgent rule(s): {rule_names}")

    # 2. Symptom risk, severity, onset, progression, duration
    seen_symptom_concepts: set[str] = set()
    has_severe = False
    has_moderate = False
    has_rapid_worsening = False
    has_worsening = False
    has_sudden = False
    has_prolonged = False

    for sf in present_symptoms:
        if sf.concept not in seen_symptom_concepts:
            seen_symptom_concepts.add(sf.concept)
            if sf.concept in safety_concepts:
                score += float(weights.get("safety_critical_symptom", 65))
                factors.append(f"High-risk symptom present: {(sf.label or sf.concept).replace('_', ' ').lower()}")
            else:
                score += float(weights.get("routine_symptom", 15))

        sev = _severity_level(sf.severity)
        if sev == "severe" and not has_severe:
            has_severe = True
            score += float(weights.get("severity_severe", 45))
            factors.append(f"Severe symptom intensity ({sf.severity}) for {(sf.label or sf.concept).replace('_', ' ').lower()}")
        elif sev == "moderate" and not has_moderate and not has_severe:
            has_moderate = True
            score += float(weights.get("severity_moderate", 20))
            factors.append(f"Moderate symptom intensity ({sf.severity}) for {(sf.label or sf.concept).replace('_', ' ').lower()}")
        elif sev == "mild":
            score += float(weights.get("severity_mild", 5))

        if sf.progression == "rapidly_worsening" and not has_rapid_worsening:
            has_rapid_worsening = True
            score += float(weights.get("progression_rapidly_worsening", 60))
            factors.append(f"Rapidly worsening progression ({(sf.label or sf.concept).replace('_', ' ').lower()})")
        elif sf.progression == "worsening" and not has_worsening and not has_rapid_worsening:
            has_worsening = True
            score += float(weights.get("progression_worsening", 35))
            factors.append(f"Worsening symptom progression ({(sf.label or sf.concept).replace('_', ' ').lower()})")

        if sf.onset == "sudden" and not has_sudden:
            has_sudden = True
            score += float(weights.get("onset_sudden", 30))
            factors.append(f"Sudden symptom onset ({(sf.label or sf.concept).replace('_', ' ').lower()})")

        days = _parse_days(sf.duration)
        if days is not None and days >= float(weights.get("prolonged_duration_days_threshold", 7)) and not has_prolonged:
            has_prolonged = True
            score += float(weights.get("prolonged_duration_bonus", 15))
            factors.append(f"Prolonged symptom duration ({sf.duration})")

    # 3. Age as a contextual vulnerability modifier (only when combined with active symptoms/concerns)
    age_fact = next((f for f in active_facts if f.concept == "age" and f.value_num is not None), None)
    if age_fact and age_fact.value_num is not None and present_symptoms:
        age_val = int(age_fact.value_num)
        if age_val >= int(weights.get("age_very_elderly_threshold", 75)):
            score += float(weights.get("age_high_vulnerability_bonus", 40))
            factors.append(f"Higher age-related clinical vulnerability (age {age_val} years)")
        elif age_val >= int(weights.get("age_elderly_threshold", 65)):
            score += float(weights.get("age_vulnerability_bonus", 25))
            factors.append(f"Older adult risk modifier (age {age_val} years)")
        elif age_val <= int(weights.get("age_pediatric_threshold", 5)):
            score += float(weights.get("age_high_vulnerability_bonus", 40))
            factors.append(f"Young paediatric vulnerability modifier (age {age_val} years)")

    # 4. Comorbidities & medical background
    seen_hist: set[str] = set()
    for hf in present_history:
        if hf.concept in seen_hist:
            continue
        seen_hist.add(hf.concept)
        if hf.concept == "pregnancy":
            score += float(weights.get("pregnancy_context", 30))
            factors.append("Pregnancy context reported")
        elif hf.concept in HIGH_RISK_HISTORY:
            score += float(weights.get("high_risk_comorbidity", 22))
            factors.append(f"Relevant comorbidity: {(hf.label or hf.concept).replace('_', ' ')}")

    seen_fam: set[str] = set()
    for ff in family_history:
        if ff.concept in seen_fam:
            continue
        seen_fam.add(ff.concept)
        rel = getattr(ff, "relationship_to_patient", "family member")
        rel_str = rel if rel != "unknown" else "family member"
        factors.append(f"Family history noted: {(ff.label or ff.concept).replace('_', ' ')} ({rel_str})")

    ambig_provenance = [f for f in active_facts if getattr(f, "subject", "patient") == "unknown" or "subject_or_section_uncertain" in (f.flags or [])]
    if ambig_provenance:
        score += float(weights.get("unreliable_measurement", 20))
        factors.append("Ambiguous subject / section provenance requires human verification")

    # 5. Reliable Vitals & Extracted Lab Findings
    for vf in vitals:
        if _is_unreliable_vital(vf):
            score += float(weights.get("unreliable_measurement", 20))
            factors.append(f"Unverified/implausible {vf.concept.replace('_', ' ')} reading requires human verification")
            continue
        val = vf.value_num
        if vf.concept == "spo2" and val is not None:
            if val < 92:
                score += float(weights.get("abnormal_vital_critical", 55))
                factors.append(f"Critically low SpO2 ({val:g}%)")
            elif val < 95:
                score += float(weights.get("abnormal_vital_moderate", 25))
                factors.append(f"Borderline low SpO2 ({val:g}%)")
        elif vf.concept == "temperature" and val is not None and vf.unit in ("C", "F"):
            temp_c = round((val - 32) * 5 / 9, 1) if vf.unit == "F" else val
            if temp_c >= 39.4:
                score += float(weights.get("abnormal_vital_critical", 55))
                factors.append(f"High fever recorded ({val:g} °{vf.unit})")
            elif temp_c >= 38.0:
                score += float(weights.get("abnormal_vital_moderate", 25))
                factors.append(f"Elevated temperature recorded ({val:g} °{vf.unit})")
        elif vf.concept == "heart_rate" and val is not None:
            if val >= 120 or val < 50:
                score += float(weights.get("abnormal_vital_critical", 55))
                factors.append(f"Markedly abnormal heart rate ({val:g} bpm)")
            elif val >= 100:
                score += float(weights.get("abnormal_vital_moderate", 25))
                factors.append(f"Elevated heart rate ({val:g} bpm)")
        elif vf.concept == "blood_pressure" and val is not None:
            dia = vf.value_num2 or 0
            if val >= 180 or val < 90 or dia >= 110:
                score += float(weights.get("abnormal_vital_critical", 55))
                factors.append(f"Markedly abnormal blood pressure ({val:g}/{dia:g} mmHg)")
            elif val >= 140 or dia >= 90:
                score += float(weights.get("abnormal_vital_moderate", 25))
                factors.append(f"Elevated blood pressure ({val:g}/{dia:g} mmHg)")

    # Lab findings from structured summary payload (only when abnormal flag is reliably determined)
    lab_items = ((summary_payload or {}).get("report_findings") or {}).get("important_findings") or []
    for lab in lab_items:
        flag = (lab.get("flag") or "").lower()
        name = lab.get("test") or "Lab finding"
        val_str = f"{lab.get('value', '')} {lab.get('unit') or ''}".strip()
        if flag in ("high", "low", "abnormal", "critical"):
            is_crit = any(k in name.lower() for k in ("troponin", "potassium", "creatinine", "lactate", "inr")) or flag == "critical"
            if is_crit:
                score += float(weights.get("abnormal_lab_critical", 50))
                factors.append(f"Critical/high-priority abnormal lab finding: {name} ({val_str}, {flag})")
            else:
                score += float(weights.get("abnormal_lab_flag", 22))
                factors.append(f"Abnormal lab finding: {name} ({val_str}, {flag})")

    # 6. Contradictions & Missing Information (conservative handling: uncertainty increases review priority)
    safety_conflicts = [c for c in open_conflicts if c.safety_critical]
    other_conflicts = [c for c in open_conflicts if not c.safety_critical]
    if safety_conflicts:
        score += float(weights.get("unresolved_safety_conflict", 70)) * len(safety_conflicts)
        concepts_str = ", ".join(sorted({c.concept.replace("_", " ") for c in safety_conflicts}))
        factors.append(f"Unresolved safety-critical contradiction ({concepts_str})")
    if other_conflicts:
        score += float(weights.get("unresolved_conflict", 28)) * len(other_conflicts)
        concepts_str = ", ".join(sorted({c.concept.replace("_", " ").replace(":", " ") for c in other_conflicts}))
        factors.append(f"Unresolved contradiction requiring clarification ({concepts_str})")

    missing_items = (assessment.missing_information if assessment else None) or []
    blocking_missing = [m for m in missing_items if m.get("blocking")]
    if blocking_missing:
        score += float(weights.get("missing_blocking_fact", 18)) * len(blocking_missing)
        labels = ", ".join(m.get("label", "required item") for m in blocking_missing[:3])
        factors.append(f"Missing essential intake details ({labels})")

    # 7. Waiting-time fairness (capped so routine cases cannot jump safety gates)
    ref_time = case.submitted_at or case.created_at
    if ref_time:
        now_dt = now or utcnow()
        if ref_time.tzinfo is None and now_dt.tzinfo is not None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        elif ref_time.tzinfo is not None and now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        wait_mins = max(0.0, (now_dt - ref_time).total_seconds() / 60.0)
        mins_per_pt = max(1.0, float(weights.get("waiting_minutes_per_point", 15)))
        cap = float(weights.get("waiting_points_cap", 30))
        wait_pts = min(cap, round(wait_mins / mins_per_pt, 1))
        if wait_pts >= 1.0:
            score += wait_pts
            factors.append(f"Waiting time fairness adjustment ({int(wait_mins)} min in queue)")

    if not factors:
        if category == "NORMAL":
            factors.append("Routine presentation with complete baseline details and no open conflicts")
        elif category == "NEEDS_REVIEW":
            factors.append("Routed for human verification due to incomplete or unverified intake details")
        else:
            factors.append("Elevated urgency category requiring prompt clinical review")

    explanation = _build_explanation(category, factors, sort_tier)
    return PriorityEvaluation(
        case_id=case.id,
        category=category,
        internal_score=round(score, 2),
        sort_tier=sort_tier,
        sort_escalation=sort_esc,
        ranking_explanation=explanation,
        ranking_factors=factors,
        scoring_version=scoring_ver,
        clinically_approved=approved,
    )


def _build_explanation(category: str, factors: list[str], sort_tier: int, rank: Optional[int] = None) -> str:
    cat_label = {"URGENT": "Urgent", "NEEDS_REVIEW": "Needs Review", "NORMAL": "Normal"}.get(category, category)
    prefix = f"Ranked #{rank} in {cat_label}" if rank is not None else f"Placed in {cat_label} (tier {sort_tier})"
    top_reasons = "; ".join(factors[:4])
    return f"{prefix}: {top_reasons}."


def rank_cases_by_category(
    evaluations: list[PriorityEvaluation],
    cases_by_id: Optional[dict[str, Case]] = None,
) -> dict[str, PriorityEvaluation]:
    """Assign independent 1-based ranks (#1, #2, #3...) inside each category (URGENT, NEEDS_REVIEW, NORMAL).
    Ordering within a category:
      1. sort_tier (ascending: tier 1 before tier 2)
      2. sort_escalation (descending: rapidly worsening before stable)
      3. internal_score (descending: higher multi-factor clinical urgency first)
      4. submitted_at / created_at (ascending: longest waiting first)
      5. case_id (deterministic tie-breaker)
    """
    grouped: dict[str, list[PriorityEvaluation]] = {"URGENT": [], "NEEDS_REVIEW": [], "NORMAL": []}
    for ev in evaluations:
        grouped.setdefault(ev.category, []).append(ev)

    out: dict[str, PriorityEvaluation] = {}
    for cat, group in grouped.items():
        def _sort_key(e: PriorityEvaluation):
            c = (cases_by_id or {}).get(e.case_id)
            ts = (c.submitted_at or c.created_at) if c else None
            ts_val = ts.timestamp() if ts else float("inf")
            return (e.sort_tier, -e.sort_escalation, -e.internal_score, ts_val, e.case_id)

        group.sort(key=_sort_key)
        for idx, ev in enumerate(group, start=1):
            ev.category_rank = idx
            ev.ranking_explanation = _build_explanation(cat, ev.ranking_factors, ev.sort_tier, rank=idx)
            out[ev.case_id] = ev
    return out
