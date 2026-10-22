"""ORM models. Facts, conflicts, assessments, decisions and audit events are separate tables so each
is independently traceable. Decisions and audit events are append-only (enforced by DB triggers)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (JSON, Boolean, DDL, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        TypeDecorator, UniqueConstraint, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Always store UTC, always return tz-aware datetimes (SQLite drops tzinfo)."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON, datetime: UTCDateTime}


# Controlled vocabularies (validated in the API layer)
ROLES = ("patient", "staff", "admin")
QUEUES = ("URGENT", "NORMAL", "NEEDS_REVIEW")
CASE_STATUSES = ("draft", "submitted", "in_review", "awaiting_patient", "resolved", "deletion_requested")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="patient")
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    verification_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    profile: Mapped[Optional["PatientProfile"]] = relationship(back_populates="user", uselist=False)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    user: Mapped[User] = relationship()


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class PatientProfile(Base):
    __tablename__ = "patient_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    birth_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # year only: data minimisation
    preferred_language: Mapped[str] = mapped_column(String(16), default="en")
    user: Mapped[User] = relationship(back_populates="profile")


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text_version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(default=utcnow)
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(16), default="en")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    last_patient_activity_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # current proposal (always from the rules engine) and clinician-confirmed decision (separate!)
    proposed_queue: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    sort_tier: Mapped[int] = mapped_column(Integer, default=99)
    sort_escalation: Mapped[int] = mapped_column(Integer, default=0)
    current_assessment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    confirmed_queue: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    reassessment_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_state: Mapped[str] = mapped_column(String(16), default="idle")  # idle|processing|failed
    chief_complaint: Mapped[str] = mapped_column(String(300), default="")
    emergency_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    deletion_requested_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    __table_args__ = (Index("ix_cases_queue_order", "proposed_queue", "sort_tier", "sort_escalation", "submitted_at"),)


class Message(Base):
    """A unit of patient (or staff) input. Original text is never overwritten; corrections are new rows."""
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    author_role: Mapped[str] = mapped_column(String(16), default="patient")
    kind: Mapped[str] = mapped_column(String(24), default="text")  # text|followup_answer|staff_note
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="en")
    language_support: Mapped[str] = mapped_column(String(16), default="supported")
    text_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translation_flags: Mapped[list] = mapped_column(default=list)
    question_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    client_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # idempotency key
    __table_args__ = (UniqueConstraint("case_id", "seq", name="uq_msg_seq"),
                      UniqueConstraint("case_id", "client_token", name="uq_msg_idem"))


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # audio | pdf | image
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(80))  # opaque; path never exposed
    mime: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending|processing|needs_confirmation|confirmed|failed
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    method: Mapped[str] = mapped_column(String(40), default="")  # pymupdf|tesseract|pymupdf+tesseract|faster-whisper
    detected_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    language_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")  # machine output (immutable)
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confirmed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # patient-corrected version
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    segments: Mapped[list] = mapped_column(default=list)  # pages (docs) or timestamped segments (audio)
    flags: Mapped[list] = mapped_column(default=list)  # unreadable_page, low_confidence, language_uncertain...
    mean_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(24))  # lexicon | llm | lexicon+llm
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16))  # ok | degraded | failed
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected: Mapped[list] = mapped_column(default=list)  # facts dropped by validation, with reason
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(20))  # symptom|history|medication|allergy|vital|demographic|report_finding|context
    concept: Mapped[str] = mapped_column(String(80), index=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    polarity: Mapped[str] = mapped_column(String(12), default="present")  # present|denied|uncertain
    temporal: Mapped[str] = mapped_column(String(12), default="current")  # current|past|resolved|unknown
    value_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    value_num: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value_num2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # diastolic
    unit: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    onset: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    duration: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    frequency: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    severity: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    progression: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # improving|stable|worsening|rapidly_worsening
    origin: Mapped[str] = mapped_column(String(12), default="explicit")  # explicit | inferred
    source_type: Mapped[str] = mapped_column(String(24))  # message|audio|document|followup|staff|profile
    source_id: Mapped[str] = mapped_column(String(32))
    locator: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)  # "message #2", "audio 00:12-00:19", "page 1"
    speaker: Mapped[str] = mapped_column(String(16), default="patient")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    stated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    measured_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # extraction certainty, NOT clinical risk
    method: Mapped[str] = mapped_column(String(16), default="lexicon")
    verification: Mapped[str] = mapped_column(String(20), default="unverified")  # unverified|patient_confirmed|staff_verified|rejected
    flags: Mapped[list] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_facts_case_concept", "case_id", "concept"),)


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(160), index=True)  # dedupe key
    kind: Mapped[str] = mapped_column(String(32))  # presence|value|unit|age|allergy|medication|negation_risk
    concept: Mapped[str] = mapped_column(String(80))
    fact_a_id: Mapped[str] = mapped_column(ForeignKey("facts.id"))
    fact_b_id: Mapped[Optional[str]] = mapped_column(ForeignKey("facts.id"), nullable=True)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="unresolved")  # unresolved|clarified|reviewer_resolved
    clarification: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    resolution_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    evidence_considered: Mapped[list] = mapped_column(default=list)
    llm_opinion: Mapped[Optional[dict]] = mapped_column(nullable=True)  # advisory only
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (UniqueConstraint("case_id", "key", name="uq_conflict_key"),)


class FollowUpQuestion(Base):
    __tablename__ = "followup_questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120))  # e.g. duration:chest_pain, conflict:<id>
    text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str] = mapped_column(String(16), default="text")  # text|yesno|choice|number
    options: Mapped[list] = mapped_column(default=list)
    concept: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    conflict_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|answered|skipped
    answer_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_choice: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    asked_at: Mapped[datetime] = mapped_column(default=utcnow)
    answered_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint("case_id", "key", name="uq_question_key"),)


class Ruleset(Base):
    __tablename__ = "rulesets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    clinically_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    definition: Mapped[dict]
    sha256: Mapped[str] = mapped_column(String(64))
    loaded_at: Mapped[datetime] = mapped_column(default=utcnow)


class Assessment(Base):
    """A proposed (never final) urgency routing produced by the deterministic rules engine."""
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    ruleset_version: Mapped[str] = mapped_column(String(64))
    demonstration_only: Mapped[bool] = mapped_column(Boolean, default=True)
    trigger: Mapped[str] = mapped_column(String(32))  # submission|new_information|clarification|conflict_resolved|manual|reprocess
    proposed_queue: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    triggered_rules: Mapped[list] = mapped_column(default=list)
    supporting_fact_ids: Mapped[list] = mapped_column(default=list)
    missing_information: Mapped[list] = mapped_column(default=list)
    open_conflict_ids: Mapped[list] = mapped_column(default=list)
    data_quality: Mapped[list] = mapped_column(default=list)
    sort_tier: Mapped[int] = mapped_column(Integer, default=99)
    sort_escalation: Mapped[int] = mapped_column(Integer, default=0)
    failure: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    inputs_digest: Mapped[str] = mapped_column(String(64), default="")
    changed_from: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Decision(Base):
    """Append-only human decision record. The original proposal is preserved alongside the outcome."""
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    proposed_queue: Mapped[str] = mapped_column(String(16))
    final_queue: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))  # confirm|escalate|downgrade|override
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(default=utcnow)


class ClinicalNote(Base):
    __tablename__ = "clinical_notes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|rejected|superseded
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    content: Mapped[dict]
    change_summary: Mapped[list] = mapped_column(default=list)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    edited_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_note_version"),)


class AuditEvent(Base):
    """Hash-chained, append-only audit log. Never store passwords, tokens or full clinical text here."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(16), default="system")
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity_type: Mapped[str] = mapped_column(String(24), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    case_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


# --- append-only enforcement (SQLite triggers; PostgreSQL equivalent documented in README) ---
for _table in ("audit_events", "decisions"):
    for _op in ("UPDATE", "DELETE"):
        event.listen(
            Base.metadata,
            "after_create",
            DDL(
                f"CREATE TRIGGER IF NOT EXISTS {_table}_no_{_op.lower()} BEFORE {_op} ON {_table} "
                f"BEGIN SELECT RAISE(ABORT, '{_table} is append-only'); END;"
            ).execute_if(dialect="sqlite"),
        )
