"""Helpers over the facts table: effective view (excludes rejected/superseded), draft persistence, serialisation."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Fact, utcnow
from . import lexicon as L
from .rules_engine import FactView


def is_superseded(f: Fact) -> bool:
    return any(x == "superseded" or x.startswith("superseded_by:") for x in (f.flags or []))


def effective_facts(db: Session, case_id: str) -> list[Fact]:
    rows = db.execute(select(Fact).where(Fact.case_id == case_id).order_by(Fact.created_at, Fact.id)).scalars().all()
    return [f for f in rows if f.verification != "rejected" and not is_superseded(f)]


def all_facts(db: Session, case_id: str) -> list[Fact]:
    return list(db.execute(select(Fact).where(Fact.case_id == case_id).order_by(Fact.created_at, Fact.id)).scalars())


def to_view(f: Fact) -> FactView:
    return FactView(
        id=f.id, category=f.category, concept=f.concept, polarity=f.polarity, temporal=f.temporal, value_num=f.value_num,
        value_num2=f.value_num2, unit=f.unit, duration=f.duration, onset=f.onset, progression=f.progression,
        verification=f.verification, flags=list(f.flags or []), source_type=f.source_type,
        subject=getattr(f, "subject", "patient") or "patient",
        relationship_to_patient=getattr(f, "relationship_to_patient", "patient") or "patient",
        assertion_status=getattr(f, "assertion_status", "present") or "present",
        section_heading=getattr(f, "section_heading", None),
    )


def mark_superseded(f: Fact, by: str) -> None:
    f.flags = [*(f.flags or []), f"superseded_by:{by}"]


def persist_drafts(db: Session, case_id: str, drafts: Iterable[L.FactDraft], *, source_type: str, source_id: str, speaker: str = "patient",
                   stated_at: Optional[datetime] = None, verification: str = "unverified") -> list[Fact]:
    out = []
    for d in drafts:
        f = Fact(
            case_id=case_id, category=d.category, concept=d.concept, label=d.label or d.concept, polarity=d.polarity, temporal=d.temporal,
            value_text=d.value_text, value_num=d.value_num, value_num2=d.value_num2, unit=d.unit, onset=d.onset, duration=d.duration,
            frequency=d.frequency, severity=d.severity, progression=d.progression, origin=d.origin, source_type=source_type,
            source_id=source_id, locator=d.locator or d.location_in_source, speaker=speaker, excerpt=d.excerpt, stated_at=stated_at or utcnow(),
            confidence=d.confidence, method=d.method, verification=verification, flags=list(d.flags),
            subject=getattr(d, "subject", "patient") or "patient",
            relationship_to_patient=getattr(d, "relationship_to_patient", "patient") or "patient",
            assertion_status=getattr(d, "assertion_status", "present") or "present",
            section_heading=getattr(d, "section_heading", None),
            page_number=getattr(d, "page_number", None),
            location_in_source=getattr(d, "location_in_source", None),
            event_date=getattr(d, "event_date", None),
            evidence_refs=getattr(d, "evidence_refs", []) or [],
        )
        db.add(f)
        out.append(f)
    db.flush()
    return out


def fact_dict(f: Fact, conflicts_by_fact: Optional[dict] = None) -> dict:
    return {
        "id": f.id, "category": f.category, "concept": f.concept, "label": f.label, "polarity": f.polarity, "temporal": f.temporal,
        "value": f.value_num, "value2": f.value_num2, "value_text": f.value_text, "unit": f.unit, "onset": f.onset, "duration": f.duration,
        "frequency": f.frequency, "severity": f.severity, "progression": f.progression, "origin": f.origin,
        "source": {"type": f.source_type, "id": f.source_id, "locator": f.locator, "speaker": f.speaker},
        "excerpt": f.excerpt, "stated_at": f.stated_at, "measured_at": f.measured_at, "confidence": f.confidence, "method": f.method,
        "verification": f.verification, "flags": f.flags or [], "superseded": is_superseded(f),
        "conflict_ids": (conflicts_by_fact or {}).get(f.id, []),
        "subject": getattr(f, "subject", "patient") or "patient",
        "relationship_to_patient": getattr(f, "relationship_to_patient", "patient") or "patient",
        "assertion_status": getattr(f, "assertion_status", "present") or "present",
        "section_heading": getattr(f, "section_heading", None),
        "page_number": getattr(f, "page_number", None),
        "location_in_source": getattr(f, "location_in_source", None),
        "event_date": getattr(f, "event_date", None),
        "evidence_refs": getattr(f, "evidence_refs", []) or [],
    }
