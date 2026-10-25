"""Escalation of possible emergencies / urgent cases.

Raises only: this module can set a case to 'urgent' or 'emergency' but never lowers one. Notification status is recorded
truthfully: no outbound SMS/phone integration ships with the project, so the recorded status is 'not_configured' and the
staff/doctor queues (which always surface escalated cases first) are the only notification channel claimed."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from .. import audit
from ..models import Case, utcnow

RANK = {None: 0, "urgent": 1, "emergency": 2}


def notify(case: Case) -> str:
    """Hook for a real integration (SMS/pager). Returns 'sent' | 'failed' | 'not_configured'. Nothing configured -> never claims 'sent'."""
    return "not_configured"


def escalate(db: Session, case: Case, level: str, *, actor=None, source: str = "", reason: str = "") -> bool:
    """Returns True if the escalation level was raised (and an audit event written)."""
    if level not in ("urgent", "emergency"):
        raise ValueError("level must be urgent or emergency")
    if RANK[level] <= RANK.get(case.escalation_level, 0):
        return False
    previous = case.escalation_level
    case.escalation_level = level
    case.escalated_at = utcnow()
    case.escalated_by = getattr(actor, "id", None)
    if level == "emergency":
        case.emergency_flagged = True
    status = notify(case)
    case.escalation_notification = status
    audit.record(db, f"case.escalated_{level}", actor=actor, actor_role=None if actor else "system", entity_type="case", entity_id=case.id, case_id=case.id,
                 details={"previous_level": previous, "new_level": level, "source": source, "reason": reason[:300], "notification_status": status,
                          "staff_task": "case promoted to the top of the staff and doctor queues"})
    return True
