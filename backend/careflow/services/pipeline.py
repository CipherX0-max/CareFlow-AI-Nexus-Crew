"""Orchestration: new evidence -> extraction -> reconciliation -> follow-up questions -> rules -> proposed assessment.
Any failure degrades to NEEDS_REVIEW; nothing in here can silently produce a NORMAL proposal."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import session_scope
from ..models import (Assessment, Case, Conflict, ExtractionRun, Fact, FollowUpQuestion, Message, PatientProfile, Ruleset, Upload, utcnow)
from . import extraction, facts as F, followup, reconcile, rules_engine as RE
from . import lexicon as L

log = logging.getLogger("careflow.pipeline")
_locks: dict[str, threading.Lock] = {}
_dirty: set[str] = set()
_guard = threading.Lock()
_ruleset_cache: dict = {}


def active_ruleset() -> dict:
    s = get_settings()
    mtime = s.rules_path.stat().st_mtime
    if _ruleset_cache.get("path") != str(s.rules_path) or _ruleset_cache.get("mtime") != mtime:
        rs, sha = RE.load_ruleset(s.rules_path)
        _ruleset_cache.update(path=str(s.rules_path), mtime=mtime, rs=rs, sha=sha)
    return _ruleset_cache["rs"]


def sync_ruleset(db: Session) -> Ruleset:
    rs = active_ruleset()
    row = db.execute(select(Ruleset).where(Ruleset.version == rs["version"])).scalar_one_or_none()
    if row is None:
        row = Ruleset(version=rs["version"], clinically_approved=bool(rs.get("clinically_approved")), approved_by_note=rs.get("approval_note", "")[:300],
                      definition=rs, sha256=_ruleset_cache["sha"])
        db.add(row)
        db.flush()
    return row


# ---------------------------------------------------------------------------------------------- units
def _page_chunks(text: str, pages: list[dict], edited: bool) -> list[extraction.Chunk]:
    parts = re.split(r"^\[page (\d+)\]\s*$", text, flags=re.M)
    chunks: list[extraction.Chunk] = []
    conf = {p["page"]: p.get("confidence") for p in pages}
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            pg = int(parts[i])
            c = conf.get(pg)
            chunks.append(extraction.Chunk(f"page {pg}", parts[i + 1].strip(), 1.0 if (edited or c is None) else max(0.0, min(1.0, c / 100))))
    else:
        chunks.append(extraction.Chunk("page 1", text.strip(), 1.0))
    return [c for c in chunks if c.text]


def _mmss(x: float) -> str:
    return f"{int(x // 60):02d}:{int(x % 60):02d}"


def _upload_unit(up: Upload) -> extraction.SourceUnit:
    text = up.confirmed_text if up.confirmed_text is not None else up.extracted_text
    edited = up.confirmed_text is not None and up.confirmed_text.strip() != up.extracted_text.strip()
    lang = up.detected_language or "en"
    if up.kind == "audio":
        if edited:
            chunks = [extraction.Chunk("voice recording (patient-corrected transcript)", text, 1.0)]
        else:
            chunks = [extraction.Chunk(f"voice recording {_mmss(s['start'])}-{_mmss(s['end'])}", s["text"], max(0.0, min(1.0, s.get("confidence", 1.0))))
                      for s in up.segments if s.get("text")]
        return extraction.SourceUnit("audio", up.id, chunks, language=lang, translation_en=None if edited else up.translated_text)
    return extraction.SourceUnit("document", up.id, _page_chunks(text, up.segments, edited), speaker="document", language=lang if lang else "en")


# ---------------------------------------------------------------------------------------------- entry
def process_case(case_id: str, trigger: str = "new_information") -> None:
    with _guard:
        lock = _locks.setdefault(case_id, threading.Lock())
    if not lock.acquire(blocking=False):
        _dirty.add(case_id)  # another run is in flight; it will loop once more
        return
    try:
        while True:
            _dirty.discard(case_id)
            _process_once(case_id, trigger)
            if case_id not in _dirty:
                break
            trigger = "new_information"
    finally:
        lock.release()


def _process_once(case_id: str, trigger: str) -> None:
    try:
        with session_scope() as db:
            c = db.get(Case, case_id)
            if c is None:
                return
            c.processing_state = "processing"
        with session_scope() as db:
            case = db.get(Case, case_id)
            run_extraction(db, case)
            ruleset = active_ruleset()
            sync_ruleset(db)
            reconcile.detect(db, case_id, ruleset)
            followup.generate(db, case, ruleset)
            assess(db, case, trigger)
            case.processing_state = "idle"
    except Exception as exc:  # noqa: BLE001 -- fail SAFE: never leave a stale/low-risk proposal behind
        log.exception("pipeline failed for case %s", case_id)
        with session_scope() as db:
            case = db.get(Case, case_id)
            if case:
                record_failure(db, case, f"{type(exc).__name__}", trigger)
                case.processing_state = "failed"


def record_failure(db: Session, case: Case, reason: str, trigger: str) -> Assessment:
    rs_ver = _ruleset_cache.get("rs", {}).get("version", "unknown")
    a = Assessment(case_id=case.id, ruleset_version=rs_ver, demonstration_only=True, trigger=trigger, proposed_queue="NEEDS_REVIEW",
                   summary="Needs human review: automatic processing failed, so no urgency can be proposed from this information.",
                   data_quality=[{"code": "processing_failed", "message": f"Internal processing error ({reason})."}], sort_tier=2, failure=reason,
                   changed_from=case.proposed_queue)
    db.add(a)
    db.flush()
    _apply_to_case(db, case, a, trigger)
    return a


# ---------------------------------------------------------------------------------------------- extraction
def run_extraction(db: Session, case: Case) -> None:
    done = {(r.source_type, r.source_id) for r in db.execute(select(ExtractionRun).where(ExtractionRun.case_id == case.id)).scalars()}
    units: list[tuple[extraction.SourceUnit, Optional[Message], Optional[Upload]]] = []
    for m in db.execute(select(Message).where(Message.case_id == case.id, Message.kind == "text", Message.author_role == "patient").order_by(Message.seq)).scalars():
        if ("message", m.id) not in done:
            units.append((extraction.SourceUnit("message", m.id, [extraction.Chunk(f"message #{m.seq}", m.text)], language=m.language, stated_at=m.created_at), m, None))
    for up in db.execute(select(Upload).where(Upload.case_id == case.id, Upload.status == "confirmed").order_by(Upload.created_at)).scalars():
        if (("audio" if up.kind == "audio" else "document"), up.id) not in done:
            units.append((_upload_unit(up), None, up))
    for unit, msg, up in units:
        res = extraction.extract_source(unit)
        db.add(ExtractionRun(case_id=case.id, source_type=unit.kind, source_id=unit.id, method=res.method, model=res.model, prompt_version=res.prompt_version,
                             status=res.status, error=res.error, raw_output=res.raw_output or None, rejected=res.rejected, latency_ms=res.latency_ms))
        F.persist_drafts(db, case.id, res.drafts, source_type=unit.kind, source_id=unit.id, speaker=unit.speaker, stated_at=unit.stated_at)
        if msg is not None:
            msg.text_en = res.translation_en if msg.language != "en" else None
            msg.translation_flags = res.translation_flags
        if up is not None:
            if res.translation_flags:
                up.flags = sorted(set([*(up.flags or []), *res.translation_flags]))
            if res.translation_en and not up.translated_text and up.kind != "audio":
                up.translated_text = res.translation_en
    # profile-derived age (approximate; year-of-birth only)
    prof = db.get(PatientProfile, case.patient_id)
    if prof and prof.birth_year and not db.execute(select(Fact.id).where(Fact.case_id == case.id, Fact.source_type == "profile")).first():
        age = datetime.now(timezone.utc).year - prof.birth_year
        if 0 < age < 121:
            db.add(Fact(case_id=case.id, category="demographic", concept="age", label="Age", polarity="present", temporal="current", value_num=float(age), unit="years",
                        source_type="profile", source_id=prof.user_id, locator="profile (from birth year)", speaker="patient", excerpt=f"birth year {prof.birth_year}",
                        method="profile", confidence=0.7, flags=["approximate_from_birth_year"]))
    first = db.execute(select(Message).where(Message.case_id == case.id, Message.kind == "text").order_by(Message.seq).limit(1)).scalar_one_or_none()
    if first:
        case.language = first.language
        case.chief_complaint = first.text.strip().replace("\n", " ")[:300]
    db.flush()


# ---------------------------------------------------------------------------------------------- assessment
def build_context(db: Session, case: Case) -> RE.CaseContext:
    facts = [F.to_view(f) for f in F.effective_facts(db, case.id)]
    conflicts = [RE.ConflictView(c.id, c.concept, c.kind, c.safety_critical, c.status) for c in db.execute(select(Conflict).where(Conflict.case_id == case.id)).scalars()]
    runs = list(db.execute(select(ExtractionRun).where(ExtractionRun.case_id == case.id)).scalars())
    msgs = list(db.execute(select(Message).where(Message.case_id == case.id)).scalars())
    ups = list(db.execute(select(Upload).where(Upload.case_id == case.id)).scalars())
    qs = list(db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == case.id)).scalars())
    has_input = bool(facts) or any(m.kind == "text" for m in msgs) or any(u.status == "confirmed" for u in ups)
    text_inputs = [r for r in runs if r.source_type in ("message", "audio", "document")]
    extraction_ok = not (text_inputs and all(r.status == "failed" for r in text_inputs)) and bool(facts or not text_inputs)
    unprocessed = [m for m in msgs if m.kind == "text" and not any(r.source_id == m.id for r in runs)]
    if unprocessed:
        extraction_ok = False
    limited = any(m.language_support == "limited" for m in msgs if m.kind == "text") or any(
        (u.detected_language or "en") not in ("en", "hi") for u in ups if u.status == "confirmed" and u.kind == "audio")
    tflags = sum(1 for m in msgs for fl in (m.translation_flags or []) if not fl.startswith("reviewed:"))
    # uncertain ASR/translation readings block 'normal' unless the patient corrected the transcript or staff acknowledged the flag
    tflags += sum(1 for u in ups if u.status == "confirmed" and (u.confirmed_text or "").strip() == (u.extracted_text or "").strip()
                  for fl in (u.flags or []) if fl.startswith(("negation_count_mismatch", "polarity_changed", "translation_unavailable", "translation_concepts_differ", "asr_")))
    return RE.CaseContext(facts=facts, conflicts=conflicts, extraction_ok=extraction_ok, extraction_degraded=any(r.status == "degraded" for r in text_inputs),
                          failed_uploads=sum(1 for u in ups if u.status == "failed"),
                          unconfirmed_uploads=sum(1 for u in ups if u.status in ("pending", "processing", "needs_confirmation")),
                          limited_language=limited, translation_flag_count=tflags,
                          skipped_question_keys={q.key for q in qs if q.status in ("skipped",)} | {q.key for q in qs if q.answer_choice == "cannot_measure"},
                          has_any_input=has_input)


def inputs_digest(ctx: RE.CaseContext, version: str) -> str:
    blob = json.dumps([version, _ruleset_cache.get("sha", ""), sorted((f.id, f.verification, f.polarity, f.temporal, f.progression or "") for f in ctx.facts),
                       sorted((c.id, c.status) for c in ctx.conflicts), ctx.extraction_ok, ctx.extraction_degraded, ctx.failed_uploads,
                       ctx.unconfirmed_uploads, ctx.limited_language, ctx.translation_flag_count, sorted(ctx.skipped_question_keys)], default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def assess(db: Session, case: Case, trigger: str, *, force: bool = False, actor=None) -> Optional[Assessment]:
    rs = active_ruleset()
    try:
        ctx = build_context(db, case)
        digest = inputs_digest(ctx, rs["version"])
        last = db.get(Assessment, case.current_assessment_id) if case.current_assessment_id else None
        if last and last.inputs_digest == digest and not force and trigger != "manual" and not last.failure:
            return last
        res = RE.evaluate(ctx, rs)
    except Exception as exc:  # noqa: BLE001  rules/DB/config failure must never become "Normal"
        log.exception("assessment failed for case %s", case.id)
        return record_failure(db, case, f"rules_error:{type(exc).__name__}", trigger)
    prev_queue = case.proposed_queue
    a = Assessment(case_id=case.id, ruleset_version=res.ruleset_version, demonstration_only=res.demonstration_only, trigger=trigger, proposed_queue=res.queue,
                   summary=res.summary, triggered_rules=res.triggered_rules, supporting_fact_ids=res.supporting_fact_ids, missing_information=res.missing_information,
                   open_conflict_ids=res.open_conflict_ids, data_quality=res.data_quality, sort_tier=res.sort_tier, sort_escalation=res.sort_escalation,
                   failure=res.failure, inputs_digest=digest, changed_from=prev_queue if prev_queue != res.queue else None)
    db.add(a)
    db.flush()
    _apply_to_case(db, case, a, trigger, actor=actor)
    return a


def _apply_to_case(db: Session, case: Case, a: Assessment, trigger: str, actor=None) -> None:
    prev = case.proposed_queue
    case.proposed_queue, case.sort_tier, case.sort_escalation, case.current_assessment_id = a.proposed_queue, a.sort_tier, a.sort_escalation, a.id
    if case.confirmed_queue and a.proposed_queue != case.confirmed_queue:
        case.reassessment_pending = True
    elif case.confirmed_queue and a.proposed_queue == case.confirmed_queue:
        case.reassessment_pending = False
    if case.status == "resolved" and trigger in ("new_information", "clarification"):
        case.status = "submitted"
        audit.record(db, "case.reopened", actor=actor, actor_role="system", entity_type="case", entity_id=case.id, case_id=case.id, details={"trigger": trigger})
    audit.record(db, "assessment.created", actor=actor, actor_role=getattr(actor, "role", "system"), entity_type="assessment", entity_id=a.id, case_id=case.id,
                 details={"queue": a.proposed_queue, "previous_queue": prev, "ruleset": a.ruleset_version, "rules": [r["rule_id"] for r in a.triggered_rules],
                          "trigger": trigger, "failure": a.failure})
