"""Deterministic, config-driven urgency rules. No LLM, no weighted scores. Every proposal lists the rule ids,
supporting facts, missing information and open conflicts that produced it.

Precedence (documented, tested):
  1. extraction failure           -> NEEDS_REVIEW
  2. configured URGENT rules      -> URGENT   (not affected by missing vitals, low scores or unresolved conflicts)
  3. safety-critical conflicts / uncertain red flags -> NEEDS_REVIEW (tier-1 ordering)
  4. other conflicts, data-quality problems, insufficient information -> NEEDS_REVIEW
  5. otherwise                    -> NORMAL
Nothing here can output NORMAL unless every guard passes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ACTIVE_TEMPORAL = ("current", "unknown")  # unknown timing is treated as potentially current (conservative)
BLOCKING_FLAGS = ("implausible_value", "unit_missing", "unit_may_be_F", "unit_may_be_C", "extractor_disagreement",
                  "extractor_value_disagreement", "translation_disagreement")


@dataclass
class FactView:
    id: str
    category: str
    concept: str
    polarity: str
    temporal: str
    value_num: Optional[float] = None
    value_num2: Optional[float] = None
    unit: Optional[str] = None
    duration: Optional[str] = None
    onset: Optional[str] = None
    progression: Optional[str] = None
    verification: str = "unverified"
    flags: list = field(default_factory=list)
    source_type: str = ""
    subject: str = "patient"
    relationship_to_patient: str = "patient"
    assertion_status: str = "present"
    section_heading: Optional[str] = None

    @property
    def verified(self) -> bool:
        return self.verification in ("patient_confirmed", "staff_verified")

    def has_flag(self, prefix: str) -> bool:
        return any(f.startswith(prefix) for f in self.flags)

    @property
    def unreliable_measurement(self) -> bool:
        return self.category == "vital" and not self.verified and any(self.has_flag(p) for p in BLOCKING_FLAGS)


@dataclass
class ConflictView:
    id: str
    concept: str
    kind: str
    safety_critical: bool
    status: str  # unresolved | clarified | reviewer_resolved


@dataclass
class CaseContext:
    facts: list[FactView]
    conflicts: list[ConflictView]
    extraction_ok: bool = True  # False: pipeline error / nothing could be extracted
    extraction_degraded: bool = False  # LLM unavailable: lexicon-only
    failed_uploads: int = 0
    unconfirmed_uploads: int = 0
    limited_language: bool = False
    translation_flag_count: int = 0
    skipped_question_keys: set = field(default_factory=set)
    has_any_input: bool = True


@dataclass
class RuleResult:
    queue: str
    summary: str
    triggered_rules: list[dict]
    supporting_fact_ids: list[str]
    missing_information: list[dict]
    open_conflict_ids: list[str]
    data_quality: list[dict]
    sort_tier: int
    sort_escalation: int
    ruleset_version: str
    demonstration_only: bool
    failure: Optional[str] = None


def load_ruleset(path: Path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    rs = json.loads(raw)
    validate_ruleset(rs)
    return rs, hashlib.sha256(raw).hexdigest()


def validate_ruleset(rs: dict) -> None:
    for k in ("version", "urgent_rules", "safety_critical_concepts", "normal_requires", "clinically_approved"):
        if k not in rs:
            raise ValueError(f"ruleset missing '{k}'")
    ids = [r["id"] for r in rs["urgent_rules"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule ids")
    protocol = rs.get("protocol_metadata")
    if protocol:
        _validate_clinical_metadata(protocol, "protocol")
    for r in rs["urgent_rules"]:
        if not isinstance(r.get("tier"), int) or r["tier"] < 1:
            raise ValueError(f"rule {r['id']}: tier must be a positive integer")
        _validate_cond(r["when"], r["id"])
        if r.get("clinical_metadata"):
            _validate_clinical_metadata(r["clinical_metadata"], f"rule {r['id']}")


def _validate_clinical_metadata(meta: dict, label: str) -> None:
    if meta.get("clinically_approved"):
        for key in ("source_organisation", "source_reference", "effective_date"):
            if not meta.get(key):
                raise ValueError(f"{label}: approved clinical logic needs {key}")
        if meta.get("review_status") != "approved":
            raise ValueError(f"{label}: approval flag and review_status disagree")
    elif meta.get("review_status") == "approved":
        raise ValueError(f"{label}: review_status cannot be approved when clinically_approved is false")


def _validate_cond(c: dict, rid: str) -> None:
    keys = set(c)
    if keys <= {"all", "any"} and len(keys) == 1:
        for sub in c[next(iter(keys))]:
            _validate_cond(sub, rid)
    elif keys == {"fact"} or keys == {"vital"} or keys == {"age"}:
        pass
    else:
        raise ValueError(f"rule {rid}: bad condition {c}")


def _is_active(f: FactView) -> bool:
    return (
        f.polarity == "present"
        and f.temporal in ACTIVE_TEMPORAL
        and f.verification != "rejected"
        and getattr(f, "subject", "patient") == "patient"
        and getattr(f, "assertion_status", "present") in ("present", "suspected", "possible")
    )


def _cmp(op: str, a: float, b: float) -> bool:
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b}[op]


def _match(cond: dict, facts: list[FactView], hits: list[str]) -> bool:
    if "all" in cond:
        res = [_match(c, facts, hits) for c in cond["all"]]
        return all(res)
    if "any" in cond:
        res = [_match(c, facts, hits) for c in cond["any"]]
        return any(res)
    if "fact" in cond:
        spec = cond["fact"]
        found = [f for f in facts if f.concept == spec["concept"] and _is_active(f)]
        if "polarity" in spec:
            found = [
                f for f in facts
                if f.concept == spec["concept"]
                and f.polarity == spec["polarity"]
                and f.verification != "rejected"
                and getattr(f, "subject", "patient") == "patient"
                and getattr(f, "assertion_status", "present") in ("present", "suspected", "possible", "denied", "historical", "resolved")
            ]
        hits += [f.id for f in found]
        return bool(found)
    if "vital" in cond:
        spec = cond["vital"]
        found = [f for f in facts if f.category == "vital" and f.concept == spec["name"] and f.value_num is not None and f.verification != "rejected"
                 and not f.unreliable_measurement and _cmp(spec["op"], f.value_num, spec["value"])]
        hits += [f.id for f in found]
        return bool(found)
    if "age" in cond:
        spec = cond["age"]
        ages = [f for f in facts if f.concept == "age" and f.value_num is not None and f.verification != "rejected"]
        ok = [f for f in ages if _cmp(spec["op"], f.value_num, spec["value"])]
        hits += [f.id for f in ok]
        return bool(ok)
    return False


def evaluate(ctx: CaseContext, rs: dict) -> RuleResult:
    ver, demo = rs["version"], not rs.get("clinically_approved", False)
    facts = [f for f in ctx.facts if f.verification != "rejected"]
    safety = set(rs["safety_critical_concepts"])
    missing = _missing_information(ctx, facts, rs)
    open_conflicts = [c for c in ctx.conflicts if _blocks(c, rs)]
    dq: list[dict] = []

    def base(queue, summary, triggered=(), support=(), tier=99, esc=0, failure=None):
        return RuleResult(queue, summary, list(triggered), sorted(set(support)), missing, [c.id for c in open_conflicts], dq,
                          tier, esc, ver, demo, failure)

    # 1. nothing usable
    if not ctx.extraction_ok or not ctx.has_any_input:
        dq.append({"code": "extraction_failed", "message": "The information could not be reliably processed."})
        return base("NEEDS_REVIEW", "Needs human review: the submission could not be reliably processed, so no urgency can be proposed from it.", tier=2,
                    failure="extraction_failed")

    # 2. configured urgent rules
    triggered, support = [], []
    for r in sorted(rs["urgent_rules"], key=lambda r: (r["tier"], r["id"])):
        hits: list[str] = []
        if _match(r["when"], facts, hits):
            triggered.append({"rule_id": r["id"], "name": r["name"], "tier": r["tier"], "rationale": r["rationale"],
                              "placeholder_threshold": bool(r.get("placeholder")), "supporting_fact_ids": sorted(set(hits)),
                              "ruleset_version": ver})
            support += hits
    if ctx.extraction_degraded:
        dq.append({"code": "ai_extraction_degraded", "message": "The local AI model was unavailable; only keyword extraction was used, which can miss information."})
    if triggered:
        tier = min(t["tier"] for t in triggered)
        esc = _escalation(facts, safety, rs)
        names = "; ".join(f"{t['rule_id']} ({t['name']})" for t in triggered[:3])
        extra = []
        if open_conflicts:
            extra.append(f"{len(open_conflicts)} unresolved conflict(s) remain")
        if missing:
            extra.append(f"{len(missing)} information item(s) still missing (this does not lower the proposal)")
        summary = f"Proposed URGENT by configured {'DEMONSTRATION ' if demo else ''}rule(s): {names}."
        if extra:
            summary += " " + "; ".join(extra) + "."
        return base("URGENT", summary, triggered, support, tier, esc)

    # 3-4. reasons the case cannot safely be called NORMAL
    reasons: list[str] = []
    tier = 99
    sc = [c for c in open_conflicts if c.safety_critical]
    if sc:
        tier = min(tier, 1)
        reasons.append("Unresolved conflict on safety-critical information (" + ", ".join(sorted({c.concept for c in sc})) + ")")
        dq.append({"code": "safety_critical_conflict", "message": reasons[-1]})
    unc = sorted({f.concept for f in facts if f.polarity == "uncertain" and f.concept in safety and not f.verified})
    if unc:
        tier = min(tier, 2)
        reasons.append("Possible warning symptom with uncertain status: " + ", ".join(unc))
        dq.append({"code": "uncertain_red_flag", "message": reasons[-1]})
    other = [c for c in open_conflicts if not c.safety_critical]
    if other:
        tier = min(tier, 3)
        reasons.append(f"{len(other)} unresolved conflict(s) in the information")
        dq.append({"code": "unresolved_conflict", "message": reasons[-1]})
    if ctx.extraction_degraded:
        tier = min(tier, 3)
        reasons.append("AI extraction was unavailable (keyword extraction only)")
    if ctx.failed_uploads:
        tier = min(tier, 2)
        reasons.append(f"{ctx.failed_uploads} uploaded file(s) could not be read; their content is unknown")
        dq.append({"code": "unreadable_upload", "message": reasons[-1]})
    if ctx.unconfirmed_uploads:
        tier = min(tier, 3)
        reasons.append(f"{ctx.unconfirmed_uploads} uploaded file(s) awaiting patient confirmation of the extracted text")
        dq.append({"code": "unconfirmed_upload", "message": reasons[-1]})
    if ctx.limited_language:
        tier = min(tier, 3)
        reasons.append("Language has limited automatic support; staff should read the original text")
        dq.append({"code": "limited_language", "message": reasons[-1]})
    if ctx.translation_flag_count:
        tier = min(tier, 3)
        reasons.append("Translation/transcription uncertainty (possible changed negation) not yet cleared")
        dq.append({"code": "translation_uncertain", "message": reasons[-1]})
    bad_vitals = [f for f in facts if f.unreliable_measurement]
    if bad_vitals:
        tier = min(tier, 3)
        reasons.append("Measurement(s) with implausible value or unclear unit: " + ", ".join(sorted({f.concept for f in bad_vitals})))
        dq.append({"code": "unreliable_measurement", "message": reasons[-1]})
    ambig_subject = [
        f for f in facts
        if getattr(f, "subject", "patient") == "unknown"
        or "subject_or_section_uncertain" in f.flags
        or "section_heading_ambiguous" in f.flags
    ]
    if ambig_subject:
        tier = min(tier, 2)
        reasons.append(f"{len(ambig_subject)} fact(s) have ambiguous subject or section provenance and need clinician review")
        dq.append({"code": "ambiguous_subject_provenance", "message": reasons[-1]})
    blocking_missing = [m for m in missing if m.get("blocking")]
    if blocking_missing:
        tier = min(tier, 3)
        reasons.append("Information needed for categorisation is missing: " + ", ".join(m["label"] for m in blocking_missing))
        dq.append({"code": "insufficient_information", "message": reasons[-1]})

    if reasons:
        esc = _escalation(facts, safety, rs)
        return base("NEEDS_REVIEW", "Needs human review: " + "; ".join(reasons) + ". No urgent rule matched, but the case is not safe to call routine.",
                    tier=tier, esc=esc, support=[f.id for f in facts if f.concept in safety and f.polarity != "denied"])
    return base("NORMAL", "Proposed NORMAL: no configured urgent rule matched, required information is present, and no conflicts or data-quality problems are open. "
                "This is a proposal for staff to confirm, not a clinical clearance.", tier=5)


def _blocks(c: ConflictView, rs: dict) -> bool:
    if c.status == "reviewer_resolved":
        return False
    if c.status == "clarified":
        return bool(c.safety_critical and rs.get("safety_conflict", {}).get("requires_reviewer_resolution", True))
    return True


def _escalation(facts: list[FactView], safety: set, rs: dict) -> int:
    prog = rs.get("escalation_indicators", {}).get("progression", [])
    best = 0
    for f in facts:
        if _is_active(f) and f.concept in safety and f.progression in prog:
            best = max(best, 2 if f.progression == "rapidly_worsening" else 1)
    return best


def _missing_information(ctx: CaseContext, facts: list[FactView], rs: dict) -> list[dict]:
    req = rs["normal_requires"]
    out: list[dict] = []
    skipped = ctx.skipped_question_keys
    symptoms = [f for f in facts if f.category == "symptom" and f.polarity == "present" and f.temporal in ACTIVE_TEMPORAL]
    if req.get("symptom") and not symptoms:
        out.append({"key": "symptom", "label": "a current presenting symptom", "blocking": True})
    if req.get("age") and not any(f.concept == "age" and f.value_num is not None for f in facts):
        out.append({"key": "age", "label": "patient age", "blocking": True})
    if req.get("duration_or_onset") and symptoms:
        for f in symptoms:
            if not (f.duration or f.onset) and not any(g.concept == f.concept and (g.duration or g.onset) for g in facts):
                out.append({"key": f"duration:{f.concept}", "label": f"onset/duration of {f.concept.replace('_', ' ')}", "concept": f.concept, "blocking": True})
    present = {f.concept for f in facts if f.polarity == "present"}
    for ve in rs.get("vitals_expected", []):
        if present & set(ve["when_concepts"]):
            for v in ve["vitals"]:
                have = any(f.category == "vital" and f.concept == v and not f.unreliable_measurement for f in facts)
                if not have and f"vital:{v}" not in skipped:
                    out.append({"key": f"vital:{v}", "label": f"{v.replace('_', ' ')} measurement", "blocking": bool(ve.get("blocking"))})
    return out
