"""Immediate emergency-language screen. Runs synchronously on every patient text BEFORE any AI processing, so the safety
message never waits for a model. Conservative: any mention (present or uncertain, current or unknown) of a configured
emergency concept triggers the message; only an explicit denial / resolved status suppresses it."""
from ..config import get_settings
from . import lexicon as L


def screen(text: str, ruleset: dict) -> dict:
    concepts = set(ruleset["emergency"]["concepts"])
    hits = sorted({d.concept for d in L.extract_symptoms(text) if d.concept in concepts and d.polarity in ("present", "uncertain") and d.temporal != "resolved"})
    if not hits:
        return {"triggered": False, "concepts": []}
    em = ruleset["emergency"]
    num = get_settings().emergency_number
    suffix = f" Emergency number: {num}." if num else ""
    return {"triggered": True, "concepts": hits, "language": L.detect_language(text)[0], "message": {"en": em["message_en"] + suffix, "hi": em["message_hi"], "hi-Latn": em["message_hi_Latn"]},
            "clinician_reviewed": bool(em.get("reviewed_by_clinician")),
            "note": "" if em.get("reviewed_by_clinician") else "This warning text has not been reviewed by a clinician (demonstration)."}
