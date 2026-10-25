"""Persistent SQLite / SQLAlchemy-compatible Case, Consent, Appointment, and Audit Service."""
import sqlite3
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from backend.app.schemas.triage import StructuredCaseExtraction, UrgencyScoreResult
from backend.app.engine.urgency_scorer import ExplainableUrgencyEngine, sort_cases_for_dashboard


class CareFlowRepository:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS patients (
                patient_id TEXT PRIMARY KEY,
                age INTEGER,
                preferred_language TEXT NOT NULL,
                consent_granted INTEGER NOT NULL DEFAULT 0,
                consent_timestamp TEXT,
                consent_withdrawn_at TEXT,
                deletion_requested_at TEXT
            );

            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                status TEXT NOT NULL,
                priority_band TEXT NOT NULL,
                urgency_score INTEGER NOT NULL,
                critical_warning_override INTEGER NOT NULL DEFAULT 0,
                requires_verification INTEGER NOT NULL DEFAULT 0,
                waiting_minutes INTEGER NOT NULL DEFAULT 0,
                assigned_reviewer TEXT,
                extraction_json TEXT NOT NULL,
                score_json TEXT NOT NULL,
                final_approved_note TEXT,
                approved_by TEXT,
                approved_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT,
                actor_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appointments (
                appointment_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                case_id TEXT,
                care_episode_type TEXT NOT NULL,
                status TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def record_audit(self, actor_id: str, actor_role: str, event_type: str, details: Dict[str, Any], case_id: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO audit_events (case_id, actor_id, actor_role, event_type, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (case_id, actor_id, actor_role, event_type, json.dumps(details), now),
        )
        self.conn.commit()

    def record_consent(self, patient_id: str, age: Optional[int], preferred_language: str, consent_granted: bool) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO patients (patient_id, age, preferred_language, consent_granted, consent_timestamp)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(patient_id) DO UPDATE SET
               consent_granted=excluded.consent_granted,
               consent_timestamp=excluded.consent_timestamp,
               consent_withdrawn_at=NULL""",
            (patient_id, age, preferred_language, 1 if consent_granted else 0, now if consent_granted else None),
        )
        self.conn.commit()
        self.record_audit(patient_id, "patient", "CONSENT_RECORDED", {"consent_granted": consent_granted, "language": preferred_language})
        return {"patient_id": patient_id, "consent_granted": consent_granted, "timestamp": now}

    def withdraw_consent_and_request_deletion(self, patient_id: str, reason: str = "Patient requested withdrawal") -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE patients SET consent_granted=0, consent_withdrawn_at=?, deletion_requested_at=? WHERE patient_id=?",
            (now, now, patient_id),
        )
        self.conn.execute(
            "UPDATE cases SET status='withdrawn_by_patient' WHERE patient_id=?",
            (patient_id,),
        )
        self.conn.commit()
        self.record_audit(patient_id, "patient", "CONSENT_WITHDRAWN_AND_DELETION_REQUESTED", {"reason": reason, "timestamp": now})
        return {"patient_id": patient_id, "consent_granted": False, "deletion_requested_at": now, "status": "scheduled_for_purge"}

    def create_case(
        self,
        case_id: str,
        patient_id: str,
        extraction: StructuredCaseExtraction,
        score: UrgencyScoreResult,
        waiting_minutes: int = 5,
    ) -> Dict[str, Any]:
        row = self.conn.execute("SELECT consent_granted FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
        if not row or not row["consent_granted"]:
            raise PermissionError("Informed patient consent is required before submitting or processing health information.")
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO cases (
                case_id, patient_id, status, priority_band, urgency_score,
                critical_warning_override, requires_verification, waiting_minutes,
                extraction_json, score_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id,
                patient_id,
                "awaiting_review",
                score.priority_band,
                score.internal_prototype_score,
                1 if score.critical_warning_override else 0,
                1 if score.requires_verification else 0,
                waiting_minutes,
                (extraction.model_dump_json() if hasattr(extraction, 'model_dump_json') else extraction.json()),
                (score.model_dump_json() if hasattr(score, 'model_dump_json') else score.json()),
                now,
            ),
        )
        self.conn.commit()
        self.record_audit(
            patient_id,
            "patient",
            "CASE_SUBMITTED",
            {"priority_band": score.priority_band, "urgency_score": score.internal_prototype_score, "rules_version": score.rules_version},
            case_id=case_id,
        )
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not row:
            raise KeyError(f"Case {case_id} not found.")
        return {
            "case_id": row["case_id"],
            "patient_id": row["patient_id"],
            "status": row["status"],
            "priority_band": row["priority_band"],
            "urgency_score": row["urgency_score"],
            "critical_warning_override": bool(row["critical_warning_override"]),
            "requires_verification": bool(row["requires_verification"]),
            "waiting_minutes": row["waiting_minutes"],
            "assigned_reviewer": row["assigned_reviewer"],
            "extraction": json.loads(row["extraction_json"]),
            "score": json.loads(row["score_json"]),
            "final_approved_note": row["final_approved_note"],
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "created_at": row["created_at"],
        }

    def list_prioritized_queue(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM cases WHERE status != 'withdrawn_by_patient'").fetchall()
        items = [self.get_case(r["case_id"]) for r in rows]
        return sort_cases_for_dashboard(items)

    def override_priority(
        self, case_id: str, staff_id: str, staff_role: str, new_priority_band: str, reason: str
    ) -> Dict[str, Any]:
        if staff_role not in ("clinician", "admin"):
            raise PermissionError("Only authorized clinicians or clinical admins may override review priority.")
        if not reason or len(reason.strip()) < 5:
            raise ValueError("A clear clinical reason is required to override priority.")
        current = self.get_case(case_id)
        prev_band = current["priority_band"]
        self.conn.execute(
            "UPDATE cases SET priority_band=? WHERE case_id=?",
            (new_priority_band, case_id),
        )
        self.conn.commit()
        self.record_audit(
            staff_id,
            staff_role,
            "PRIORITY_OVERRIDDEN",
            {
                "previous_priority_band": prev_band,
                "new_priority_band": new_priority_band,
                "previous_urgency_score": current["urgency_score"],
                "reason": reason,
            },
            case_id=case_id,
        )
        return self.get_case(case_id)

    def approve_final_note(
        self, case_id: str, clinician_id: str, clinician_role: str, final_note: str
    ) -> Dict[str, Any]:
        if clinician_role != "clinician":
            raise PermissionError("Only a qualified clinician can approve the final triage or referral note.")
        if not final_note or len(final_note.strip()) < 10:
            raise ValueError("Final triage note cannot be empty.")
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE cases SET status='approved_by_clinician', final_approved_note=?, approved_by=?, approved_at=? WHERE case_id=?",
            (final_note, clinician_id, now, case_id),
        )
        self.conn.commit()
        self.record_audit(
            clinician_id,
            clinician_role,
            "FINAL_NOTE_APPROVED",
            {"approved_at": now, "note_length": len(final_note)},
            case_id=case_id,
        )
        return self.get_case(case_id)

    def create_appointment(
        self, appointment_id: str, patient_id: str, case_id: str, care_episode_type: str, scheduled_for: str
    ) -> Dict[str, Any]:
        # Prevent accidental duplicate active routine bookings for the same care episode
        existing = self.conn.execute(
            """SELECT appointment_id FROM appointments
               WHERE patient_id=? AND care_episode_type=? AND status IN ('confirmed', 'pending')""",
            (patient_id, care_episode_type),
        ).fetchone()
        if existing and care_episode_type != "second_opinion":
            raise ValueError(
                f"Active booking ({existing['appointment_id']}) already exists for care episode '{care_episode_type}'."
            )
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO appointments (appointment_id, patient_id, case_id, care_episode_type, status, scheduled_for, created_at) VALUES (?, ?, ?, ?, 'confirmed', ?, ?)",
            (appointment_id, patient_id, case_id, care_episode_type, scheduled_for, now),
        )
        self.conn.commit()
        return {"appointment_id": appointment_id, "status": "confirmed", "scheduled_for": scheduled_for}

    def get_audit_trail(self, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if case_id:
            rows = self.conn.execute("SELECT * FROM audit_events WHERE case_id=? ORDER BY event_id DESC", (case_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM audit_events ORDER BY event_id DESC").fetchall()
        return [
            {
                "event_id": r["event_id"],
                "case_id": r["case_id"],
                "actor_id": r["actor_id"],
                "actor_role": r["actor_role"],
                "event_type": r["event_type"],
                "details": json.loads(r["details_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
