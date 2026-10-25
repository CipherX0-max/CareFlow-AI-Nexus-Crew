"""Data lifecycle: deterministic, auditable deletion of case content and attachments.

Deleted clinical text is never copied into the audit log (only counts and the policy outcome)."""
from __future__ import annotations

import shutil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (Assessment, Case, ClinicalNote, Conflict, Decision, ExtractionRun, Fact, FollowUpQuestion, Message, Upload, utcnow)
from . import uploads as U


def retention_hold(db: Session, case: Case) -> str | None:
    """Reason why a submitted case must be retained (restricted), or None when direct deletion is permitted."""
    if db.scalar(select(func.count()).select_from(Decision).where(Decision.case_id == case.id)):
        return "A clinician/staff decision has been recorded for this case and must be retained under the record-keeping policy."
    if db.scalar(select(func.count()).select_from(ClinicalNote).where(ClinicalNote.case_id == case.id, ClinicalNote.status == "approved")):
        return "An approved clinical note exists; retention policy applies."
    return None


def direct_delete_allowed(db: Session, case: Case) -> tuple[bool, str | None]:
    if case.status == "draft":
        return True, None
    if case.status in ("deletion_requested", "deleted"):
        return False, "Deletion is already requested or complete."
    if not get_settings().direct_deletion_enabled:
        return False, "Direct deletion of submitted records is disabled by policy; a deletion request is required."
    hold = retention_hold(db, case)
    return (hold is None), hold


def remove_files(db: Session, case: Case) -> int:
    n = 0
    for up in db.execute(select(Upload).where(Upload.case_id == case.id)).scalars():
        try:
            U.path_for(up).unlink(missing_ok=True)
            n += 1
        except OSError:
            pass
    shutil.rmtree(get_settings().upload_dir / case.id, ignore_errors=True)
    return n


def purge_case_content(db: Session, case: Case) -> dict:
    """Remove all clinical content and files; keep an inert tombstone row so references and the audit chain stay intact."""
    files = remove_files(db, case)
    counts: dict[str, int] = {}
    for model in (Conflict, FollowUpQuestion, Fact, ExtractionRun, Message, Upload, ClinicalNote, Assessment):
        rows = db.execute(select(model).where(model.case_id == case.id)).scalars().all()
        counts[model.__tablename__] = len(rows)
        for row in rows:
            db.delete(row)
    case.chief_complaint, case.proposed_queue, case.confirmed_queue = "", None, None
    case.current_assessment_id = None
    case.emergency_flagged = False
    case.status, case.language, case.updated_at = "deleted", "-", utcnow()
    return {"files_removed": files, "rows_removed": counts}
