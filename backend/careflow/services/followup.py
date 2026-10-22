"""Follow-up question generation and answer handling.
Every question carries a reason. Answered/skipped keys are never asked again. An unanswered or skipped question is
never recorded as a negative finding."""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Case, Conflict, Fact, FollowUpQuestion, Message, User, utcnow
from . import facts as F
from . import lexicon as L
from . import reconcile as R
from .rules_engine import ACTIVE_TEMPORAL

MAX_OPEN = 3
MAX_TOTAL = 14

T = {  # key -> {lang: text}
    "duration": {"en": "How long have you had {sym}, and did it start suddenly or gradually?",
                 "hi": "आपको {sym} कब से है, और यह अचानक शुरू हुआ या धीरे-धीरे?",
                 "hi-Latn": "Aapko {sym} kab se hai, aur yeh achanak shuru hua ya dheere-dheere?"},
    "progression": {"en": "Is your {sym} getting better, staying the same, or getting worse?",
                    "hi": "आपका {sym} बेहतर हो रहा है, वैसा ही है, या बिगड़ रहा है?",
                    "hi-Latn": "Aapka {sym} behtar ho raha hai, waisa hi hai, ya bigad raha hai?"},
    "severity": {"en": "On a scale of 0 (none) to 10 (worst imaginable), how bad is your {sym} right now?",
                 "hi": "0 (कुछ नहीं) से 10 (सबसे ज़्यादा) के पैमाने पर, अभी आपका {sym} कितना तेज़ है?",
                 "hi-Latn": "0 (kuch nahi) se 10 (sabse zyada) ke scale par, abhi aapka {sym} kitna tez hai?"},
    "age": {"en": "How old are you (in years)?", "hi": "आपकी उम्र कितने साल है?", "hi-Latn": "Aapki umar kitne saal hai?"},
    "meds": {"en": "Are you taking any medicines right now (including blood thinners, insulin or over-the-counter)? List them, or choose 'None'.",
             "hi": "क्या आप अभी कोई दवा ले रहे हैं (खून पतला करने वाली, इंसुलिन या बिना पर्ची की दवा सहित)? उनके नाम लिखें, या 'कोई नहीं' चुनें।",
             "hi-Latn": "Kya aap abhi koi dawai le rahe hain (blood thinner, insulin ya bina parchi ki dawai sahit)? Naam likhein, ya 'Koi nahi' chunein."},
    "allergies": {"en": "Do you have any known allergies (medicines, food, other)? List them, or choose 'None'.",
                  "hi": "क्या आपको किसी चीज़ से एलर्जी है (दवा, खाना, अन्य)? लिखें, या 'कोई नहीं' चुनें।",
                  "hi-Latn": "Kya aapko kisi cheez se allergy hai (dawai, khana, anya)? Likhein, ya 'Koi nahi' chunein."},
    "history": {"en": "Do you have any long-term health conditions or past surgeries (for example diabetes, heart, lung, kidney or blood-pressure problems)? List them, or choose 'None'.",
                "hi": "क्या आपको कोई पुरानी बीमारी या पहले की सर्जरी है (जैसे डायबिटीज़, दिल, फेफड़े, किडनी या बीपी की समस्या)? लिखें, या 'कोई नहीं' चुनें।",
                "hi-Latn": "Kya aapko koi purani bimari ya pehle ki surgery hai (jaise diabetes, dil, phephde, kidney ya BP ki samasya)? Likhein, ya 'Koi nahi' chunein."},
    "uncertain": {"en": "You mentioned you might have {sym}. Do you have {sym} now?", "hi": "आपने बताया कि शायद आपको {sym} है। क्या अभी आपको {sym} है?",
                  "hi-Latn": "Aapne bataya ki shayad aapko {sym} hai. Kya abhi aapko {sym} hai?"},
    "vital": {"en": "If you have a recent {vit} reading, please enter it (with units). If you cannot measure it, choose 'Cannot measure'.",
              "hi": "यदि आपके पास हाल की {vit} रीडिंग है तो कृपया (इकाई सहित) लिखें। यदि माप नहीं सकते, तो 'माप नहीं सकता' चुनें।",
              "hi-Latn": "Agar aapke paas haal ki {vit} reading hai to kripya (unit ke saath) likhein. Agar naap nahi sakte, to 'Naap nahi sakta' chunein."},
}
OPT = {
    "presence": [("present_now", "Yes, I have it now"), ("earlier_resolved", "I had it earlier, but it has gone"), ("not_present", "No, I do not have it / I misspoke"), ("unsure", "I am not sure")],
    "yes_now": [("present_now", "Yes, I have it now"), ("earlier_resolved", "I had it earlier, but it has gone"), ("not_present", "No"), ("unsure", "I am not sure")],
    "progression": [("improving", "Getting better"), ("stable", "Staying the same"), ("worsening", "Getting worse"), ("rapidly_worsening", "Getting worse quickly"), ("unsure", "Not sure")],
    "none_text": [("none", "None"), ("unsure", "I am not sure")],
    "vital": [("cannot_measure", "Cannot measure")],
    "ab": [("use_a", "First value"), ("use_b", "Second value"), ("unsure", "I am not sure")],
    "unit": [("unit_F", "Degrees Fahrenheit (°F)"), ("unit_C", "Degrees Celsius (°C)"), ("unsure", "I am not sure")],
}


def _t(key: str, lang: str, **kw) -> str:
    d = T[key]
    return d.get(lang, d["en"]).format(**kw)


def _sym(concept: str) -> str:
    return concept.replace("_", " ")


def _qkey(db: Session, case_id: str) -> dict[str, FollowUpQuestion]:
    return {q.key: q for q in db.execute(select(FollowUpQuestion).where(FollowUpQuestion.case_id == case_id)).scalars()}


def generate(db: Session, case: Case, ruleset: dict) -> list[FollowUpQuestion]:
    """Create new questions for what is missing/unresolved; withdraw questions that became moot."""
    lang = case.language if case.language in ("en", "hi", "hi-Latn") else "en"
    facts = F.effective_facts(db, case.id)
    safety = set(ruleset["safety_critical_concepts"])
    existing = _qkey(db, case.id)
    cands: list[dict] = []

    conflicts = list(db.execute(select(Conflict).where(Conflict.case_id == case.id)).scalars())
    for c in conflicts:
        key = f"conflict:{c.id}"
        if c.status != "unresolved":
            q = existing.get(key)
            if q and q.status == "open":
                q.status, q.answered_at = "withdrawn", utcnow()
            continue
        if c.kind in ("presence", "negation_risk"):
            sym = _sym(c.concept)
            text = {"en": f"We received different information about {sym}. Do you have {sym} right now?",
                    "hi": f"{sym} के बारे में हमें अलग-अलग जानकारी मिली। क्या अभी आपको {sym} है?",
                    "hi-Latn": f"{sym} ke baare mein humein alag-alag jaankari mili. Kya abhi aapko {sym} hai?"}[lang]
            cands.append(dict(key=key, priority=10 if c.safety_critical else 20, text=text, reason=c.description, answer_type="choice",
                              options=[{"value": v, "label": l} for v, l in OPT["presence"]], concept=c.concept, conflict_id=c.id))
        elif c.kind in ("value", "age"):
            ev = [e for e in c.evidence_considered if "fact_id" in e][:2]
            vals = []
            for e in ev:
                vals.append(F_value(db, e["fact_id"]))
            if len(vals) == 2:
                cands.append(dict(key=key, priority=20, text=f"We have two different values for {_sym(c.concept)}: {vals[0]} and {vals[1]}. Which is correct?",
                                  reason=c.description, answer_type="choice",
                                  options=[{"value": "use_a", "label": vals[0]}, {"value": "use_b", "label": vals[1]}, {"value": "unsure", "label": "I am not sure"}],
                                  concept=c.concept, conflict_id=c.id))
        elif c.kind == "unit":
            f = db.get(Fact, c.fact_a_id)
            if f and c.concept == "temperature":
                cands.append(dict(key=key, priority=25, text=f"Your temperature was recorded as {f.value_num:g}. Was that in Fahrenheit or Celsius?", reason=c.description,
                                  answer_type="choice", options=[{"value": v, "label": l} for v, l in OPT["unit"]], concept=c.concept, conflict_id=c.id))
        elif c.kind in ("allergy", "medication"):
            cands.append(dict(key=key, priority=22, text=f"We have conflicting information about your {c.kind}. Do you have/take: {c.concept.split(':', 1)[-1]}?",
                              reason=c.description, answer_type="choice",
                              options=[{"value": "present_now", "label": "Yes"}, {"value": "not_present", "label": "No"}, {"value": "unsure", "label": "I am not sure"}],
                              concept=c.concept, conflict_id=c.id))

    # uncertain safety-critical statements
    for f in facts:
        if f.polarity == "uncertain" and f.category == "symptom" and \
           not any(c.status == "unresolved" and f.id in {e.get("fact_id") for e in c.evidence_considered} for c in conflicts):
            cands.append(dict(key=f"uncertain:{f.concept}", priority=30 if f.concept in safety else 45,
                              text=_t("uncertain", lang, sym=_sym(f.concept)),
                              reason=f"You indicated you were unsure about {_sym(f.concept)}; an unclear answer is not treated as 'no'.",
                              answer_type="choice", options=[{"value": v, "label": l} for v, l in OPT["yes_now"]], concept=f.concept))

    present = [f for f in facts if f.polarity == "present" and f.temporal in ACTIVE_TEMPORAL and f.category == "symptom"]
    seen_c: list[str] = []
    for f in sorted(present, key=lambda f: (f.concept not in safety, f.created_at)):
        if f.concept in seen_c:
            continue
        seen_c.append(f.concept)
        if len(seen_c) > 2:
            break
        group = [g for g in facts if g.concept == f.concept]
        sym = _sym(f.concept)
        pri = 40 if f.concept in safety else 48
        if not any(g.duration or g.onset for g in group):
            cands.append(dict(key=f"duration:{f.concept}", priority=pri, text=_t("duration", lang, sym=sym), answer_type="text", concept=f.concept,
                              reason=f"Timing of {sym} (when it started and how) helps staff judge how quickly it needs attention."))
        if not any(g.progression for g in group):
            cands.append(dict(key=f"progression:{f.concept}", priority=pri + 2, text=_t("progression", lang, sym=sym), answer_type="choice", concept=f.concept,
                              options=[{"value": v, "label": l} for v, l in OPT["progression"]],
                              reason=f"Whether {sym} is improving or worsening matters for prioritisation."))
        if f.concept.endswith("pain") or f.concept in ("headache", "sore_throat"):
            if not any(g.severity for g in group):
                cands.append(dict(key=f"severity:{f.concept}", priority=pri + 4, text=_t("severity", lang, sym=sym), answer_type="number", concept=f.concept,
                                  reason=f"How severe the {sym} is (as you feel it) helps staff understand your situation."))

    if not any(f.concept == "age" and f.value_num is not None for f in facts):
        cands.append(dict(key="age", priority=50, text=_t("age", lang), answer_type="number", concept="age",
                          reason="Age changes how symptoms are assessed; no age was provided."))
    if not any(f.category == "medication" for f in facts):
        cands.append(dict(key="meds", priority=60, text=_t("meds", lang), answer_type="text", options=[{"value": v, "label": l} for v, l in OPT["none_text"]],
                          reason="Current medicines (especially blood thinners) affect assessment; none were mentioned."))
    if not any(f.category == "allergy" for f in facts):
        cands.append(dict(key="allergies", priority=62, text=_t("allergies", lang), answer_type="text", options=[{"value": v, "label": l} for v, l in OPT["none_text"]],
                          reason="Allergies were not mentioned and are important for safe care."))
    if not any(f.category == "history" for f in facts):
        cands.append(dict(key="history", priority=64, text=_t("history", lang), answer_type="text", options=[{"value": v, "label": l} for v, l in OPT["none_text"]],
                          reason="Relevant medical history was not mentioned."))
    for ve in ruleset.get("vitals_expected", []):
        if {f.concept for f in facts if f.polarity == "present"} & set(ve["when_concepts"]):
            for v in ve["vitals"]:
                if not any(f.category == "vital" and f.concept == v for f in facts):
                    cands.append(dict(key=f"vital:{v}", priority=70, text=_t("vital", lang, vit=v.replace("_", " ")), answer_type="text",
                                      options=[{"value": "cannot_measure", "label": "Cannot measure"}], concept=v,
                                      reason=f"No {v.replace('_', ' ')} reading is available. A missing reading is never assumed to be normal."))

    # withdraw open questions whose information has since arrived
    for key, q in existing.items():
        if q.status in ("open", "deferred") and not key.startswith(("conflict:", "staff:")) and key not in {c["key"] for c in cands}:
            q.status, q.answered_at = "withdrawn", utcnow()

    open_qs = [q for q in existing.values() if q.status == "open"]
    created = []
    for c in sorted(cands, key=lambda c: (c["priority"], c["key"])):
        prior = existing.get(c["key"])
        if prior is not None and prior.status != "deferred":
            continue  # answered, skipped, withdrawn or already open: never re-ask
        if len(open_qs) >= MAX_OPEN:
            weakest = max(open_qs, key=lambda q: (q.priority, q.key))
            if c["priority"] < 30 and weakest.priority > c["priority"] and not weakest.key.startswith("staff:"):
                weakest.status = "deferred"  # urgent clarification preempts routine questions (they come back later)
                open_qs.remove(weakest)
            else:
                continue
        if len(existing) + len(created) >= MAX_TOTAL and prior is None:
            continue
        if prior is not None:
            prior.status = "open"
            open_qs.append(prior)
            continue
        q = FollowUpQuestion(case_id=case.id, key=c["key"], text=c["text"], reason=c["reason"], answer_type=c["answer_type"], options=c.get("options", []),
                             concept=c.get("concept"), conflict_id=c.get("conflict_id"), priority=c["priority"])
        db.add(q)
        created.append(q)
        open_qs.append(q)
    db.flush()
    return created


def F_value(db: Session, fact_id: str) -> str:
    f = db.get(Fact, fact_id)
    if not f:
        return "?"
    if f.concept == "blood_pressure":
        return f"{f.value_num:g}/{(f.value_num2 or 0):g} {f.unit or ''}".strip()
    return f"{f.value_num:g} {f.unit or ''}".strip() if f.value_num is not None else (f.value_text or "?")


# ------------------------------------------------------------------ answering -------------------------------
def _new_fact(case: Case, q: FollowUpQuestion, *, category, concept, label=None, polarity="present", temporal="current", text="", **kw) -> Fact:
    return Fact(case_id=case.id, category=category, concept=concept, label=label or concept.replace("_", " "), polarity=polarity, temporal=temporal,
                origin="explicit", source_type="followup", source_id=q.id, locator=f"follow-up answer ({q.key})", speaker="patient", excerpt=text[:300],
                stated_at=utcnow(), confidence=0.95, method="patient_answer", verification="patient_confirmed", flags=kw.pop("flags", []), **kw)


def answer(db: Session, case: Case, q: FollowUpQuestion, *, text: str = "", choice: Optional[str] = None, user: User) -> dict:
    if q.status != "open":
        raise ValueError("This question has already been answered or closed.")
    text = (text or "").strip()
    valid = {o["value"] for o in (q.options or [])}
    if choice and valid and choice not in valid:
        raise ValueError("Invalid choice for this question.")
    if q.answer_type == "choice" and not choice:
        raise ValueError("Please choose one of the options.")
    if not text and not choice:
        raise ValueError("An answer is required (or choose 'I am not sure' to skip).")
    seq = (db.execute(select(Message.seq).where(Message.case_id == case.id).order_by(Message.seq.desc()).limit(1)).scalar() or 0) + 1
    lang, support = L.detect_language(text) if text else (case.language, "supported")
    db.add(Message(case_id=case.id, seq=seq, author_role="patient", kind="followup_answer", text=text or f"[chose: {choice}]", language=lang,
                   language_support=support, question_id=q.id))
    q.answer_text, q.answer_choice, q.answered_at = text or None, choice, utcnow()
    key, kind = q.key, q.key.split(":")[0]
    created: list[Fact] = []
    skipped = choice in ("unsure", "cannot_measure")

    if kind == "conflict":
        c = db.get(Conflict, q.conflict_id)
        if choice == "unsure":
            q.status = "skipped"
        else:
            new = R.apply_resolution(db, c, choice, by_user=user, by_patient=True, text=text or q.text)
            q.status = "answered"
            if new:
                created.append(new)
    elif skipped:
        q.status = "skipped"
    else:
        q.status = "answered"
        concept = q.concept or ""
        if kind == "uncertain":
            pol, temp = R.PRESENCE_OUTCOMES.get(choice, ("present", "current"))
            for f in F.effective_facts(db, case.id):
                if f.concept == concept and f.polarity == "uncertain":
                    F.mark_superseded(f, q.id)
            created.append(_new_fact(case, q, category="symptom", concept=concept, polarity=pol, temporal=temp, text=text or choice or "", flags=["clarification"]))
        elif kind == "duration":
            dur = L.parse_duration(text) or text[:80]
            onset = "sudden" if L.SUDDEN_RX.search(text) else "gradual" if L.GRADUAL_RX.search(text) else None
            created.append(_new_fact(case, q, category="symptom", concept=concept, text=text, duration=dur, onset=onset))
        elif kind == "progression":
            created.append(_new_fact(case, q, category="symptom", concept=concept, text=choice or "", progression=choice))
        elif kind == "severity":
            m = re.search(r"\d+", text)
            if m and 0 <= int(m.group()) <= 10:
                created.append(_new_fact(case, q, category="symptom", concept=concept, text=text, severity=f"{int(m.group())}/10"))
            else:
                raise ValueError("Please enter a number from 0 to 10.")
        elif kind == "age":
            m = re.search(r"\d{1,3}", text)
            if not m or not 0 < int(m.group()) < 121:
                raise ValueError("Please enter your age in years (1-120).")
            created.append(_new_fact(case, q, category="demographic", concept="age", label="Age", text=text, value_num=float(m.group()), unit="years"))
        elif kind in ("meds", "allergies", "history"):
            if choice == "none":
                cat, con = {"meds": ("medication", "medication_any"), "allergies": ("allergy", "allergy_any"), "history": ("history", "history_any")}[kind]
                created.append(_new_fact(case, q, category=cat, concept=con, polarity="denied", temporal="unknown", text="None reported"))
            else:
                items = [p.strip() for p in re.split(r"[,;\n]| and | aur |\band\b", text) if p.strip()]
                cat = {"meds": "medication", "allergies": "allergy", "history": "history"}[kind]
                for it in items[:12]:
                    slug = re.sub(r"[^a-z0-9]+", "_", it.lower()).strip("_")[:40] or "item"
                    concept = f"{ {'meds': 'medication', 'allergies': 'allergy'}.get(kind, 'history')}:{slug}"
                    created.append(_new_fact(case, q, category=cat, concept=concept, label=it, text=it, temporal="unknown" if kind != "meds" else "current", value_text=it))
        elif kind == "vital":
            nums = L.extract_vitals(f"{concept.replace('_', ' ')} {text}")
            if not nums:
                m = re.search(r"\d+(?:\.\d+)?", text)
                if not m:
                    raise ValueError("Please enter the reading as a number (with units if possible).")
                nums = [L.FactDraft(category="vital", concept=concept, value_num=float(m.group()), unit=None, flags=["unit_missing"])]
            for d in nums:
                if d.concept != concept:
                    continue
                created.append(_new_fact(case, q, category="vital", concept=concept, text=text, value_num=d.value_num, value_num2=d.value_num2, unit=d.unit,
                                         flags=[*d.flags, "measurement_time_unknown"]))
    # extra facts in free text (e.g. "since 3 days and I also have fever")
    if text and kind not in ("age", "severity", "vital", "meds", "allergies", "history", "conflict"):
        known = {(f.category, f.concept, f.polarity) for f in created}
        for d in L.extract_all(text, locator=f"follow-up answer ({q.key})"):
            if (d.category, d.concept, d.polarity) in known or d.category == "demographic" and kind == "age":
                continue
            created.append(_new_fact(case, q, category=d.category, concept=d.concept, label=d.label, polarity=d.polarity, temporal=d.temporal, text=d.excerpt,
                                     duration=d.duration, onset=d.onset, severity=d.severity, progression=d.progression, value_num=d.value_num,
                                     value_num2=d.value_num2, unit=d.unit, flags=list(d.flags)))
    for f in created:
        db.add(f)
    db.flush()
    return {"status": q.status, "new_fact_ids": [f.id for f in created]}
