"""Append-only, hash-chained audit trail. Details must be minimal: identifiers and decisions, not clinical prose."""
import hashlib
import json
import threading
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, utcnow

_lock = threading.Lock()
_FORBIDDEN_KEYS = {"password", "token", "csrf", "secret", "password_hash"}


def _scrub(details: dict) -> dict:
    return {k: ("[redacted]" if k.lower() in _FORBIDDEN_KEYS else v) for k, v in details.items()}


def _digest(prev: str, ev: AuditEvent) -> str:
    body = json.dumps(
        [prev, ev.ts.isoformat(), ev.actor_id, ev.actor_role, ev.action, ev.entity_type, ev.entity_id, ev.case_id, ev.details],
        sort_keys=True, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def record(db: Session, action: str, *, actor=None, actor_role: Optional[str] = None, entity_type: str = "",
           entity_id: str = "", case_id: Optional[str] = None, details: Optional[dict[str, Any]] = None) -> AuditEvent:
    with _lock:
        prev = db.execute(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)).scalar() or ""
        ev = AuditEvent(
            ts=utcnow(), actor_id=getattr(actor, "id", actor), actor_role=actor_role or getattr(actor, "role", "system"),
            action=action, entity_type=entity_type, entity_id=entity_id, case_id=case_id,
            details=_scrub(details or {}), prev_hash=prev,
        )
        ev.hash = _digest(prev, ev)
        db.add(ev)
        db.flush()
    return ev


def verify_chain(db: Session) -> dict:
    prev, count = "", 0
    for ev in db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        if ev.prev_hash != prev or ev.hash != _digest(prev, ev):
            return {"ok": False, "broken_at": ev.id, "checked": count}
        prev, count = ev.hash, count + 1
    return {"ok": True, "checked": count}
