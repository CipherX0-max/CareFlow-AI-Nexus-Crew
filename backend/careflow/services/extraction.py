"""Source text -> validated fact drafts.

Pipeline per source: deterministic lexicon (always) + local LLM (when available). The LLM output is
schema-validated and every fact must quote a verbatim excerpt from the source, otherwise it is rejected.
Where the two extractors disagree on polarity the fact becomes `uncertain` (never silently picked)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import lexicon as L
from . import llm, prompts
from ..config import get_settings

log = logging.getLogger("careflow.extraction")

VITAL_ALIASES = {"spo2": "spo2", "oxygen_saturation": "spo2", "oxygen": "spo2", "o2_sat": "spo2", "saturation": "spo2",
                 "heart_rate": "heart_rate", "pulse": "heart_rate", "hr": "heart_rate",
                 "resp_rate": "resp_rate", "respiratory_rate": "resp_rate", "rr": "resp_rate",
                 "temperature": "temperature", "temp": "temperature", "blood_pressure": "blood_pressure", "bp": "blood_pressure",
                 "pain_score": "pain_score"}
MODIFIER_NOISE = {"", "unknown", "none", "null", "n/a", "na", "not stated", "not provided", "unspecified", "not specified"}
PROGRESSION_OK = {"improving", "stable", "worsening", "rapidly_worsening"}


@dataclass
class Chunk:
    locator: str
    text: str
    quality: float = 1.0  # 0..1 reliability of the text itself (OCR / ASR confidence); 1.0 for typed text


@dataclass
class SourceUnit:
    kind: str  # message | audio | document | followup
    id: str
    chunks: list[Chunk]
    speaker: str = "patient"
    stated_at: Optional[datetime] = None
    language: str = "en"
    translation_en: Optional[str] = None  # already available (e.g. Whisper translate); else LLM translates


@dataclass
class ExtractionResult:
    drafts: list[L.FactDraft] = field(default_factory=list)
    status: str = "ok"  # ok | degraded | failed
    error: Optional[str] = None
    method: str = "lexicon"
    model: str = ""
    prompt_version: str = ""
    raw_output: str = ""
    rejected: list[dict] = field(default_factory=list)
    translation_en: Optional[str] = None
    translation_flags: list[str] = field(default_factory=list)
    latency_ms: int = 0


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60] or "unspecified"


def _clean_modifier(v) -> Optional[str]:
    if v is None:
        return None
    v = str(v).strip()
    return None if _norm(v) in MODIFIER_NOISE else v[:80]


def _grounded_modifier(value: Optional[str], source: str) -> bool:
    """A free-text modifier (duration, onset...) is accepted only if its content words appear in the source."""
    if not value:
        return False
    src = _norm(source)
    toks = [t for t in re.findall(r"[\wऀ-ॿ]+", value.lower()) if t not in {"for", "since", "the", "a", "an", "of", "about", "around"}]
    return bool(toks) and any(t in src for t in toks)


def _validate_llm_facts(raw: dict, block: str) -> tuple[list[L.FactDraft], list[dict]]:
    out: list[L.FactDraft] = []
    rejected: list[dict] = []
    block_n = _norm(block)
    facts = raw.get("facts")
    if not isinstance(facts, list):
        return [], [{"reason": "facts_not_a_list"}]
    for f in facts:
        try:
            cat = f["category"]
            concept = _slug(str(f["concept"]))
            pol, temp = f["polarity"], f["temporal"]
            excerpt = str(f["excerpt"]).strip()
        except (KeyError, TypeError):
            rejected.append({"reason": "missing_required_field", "fact": str(f)[:120]})
            continue
        if cat not in ("symptom", "history", "medication", "allergy", "vital", "demographic", "context") or \
           pol not in ("present", "denied", "uncertain") or temp not in ("current", "past", "resolved", "unknown"):
            rejected.append({"reason": "invalid_enum", "fact": str(f)[:120]})
            continue
        if not excerpt or _norm(excerpt) not in block_n:
            rejected.append({"reason": "excerpt_not_in_source", "concept": concept, "excerpt": excerpt[:80]})
            continue
        if cat == "vital":
            concept = VITAL_ALIASES.get(concept, concept)
            if concept not in VITAL_ALIASES.values():
                rejected.append({"reason": "unknown_vital", "concept": concept})
                continue
        elif cat == "demographic":
            if concept != "age":
                rejected.append({"reason": "unsupported_demographic", "concept": concept})
                continue
        elif concept in L.C:
            cat = L.C[concept][0]
        elif cat in ("symptom", "history", "context"):
            concept = f"other:{concept}"
        elif cat in ("medication", "allergy") and ":" not in concept and concept not in ("medication_any", "allergy_any"):
            concept = f"{cat}:{concept}"

        subj = str(f.get("subject") or "patient").strip().lower()
        if subj not in ("patient", "family_member", "another_person", "unknown"):
            subj = "patient"
        rel = str(f.get("relationship_to_patient") or ("patient" if subj == "patient" else "unknown")).strip().lower()
        assertion = str(f.get("assertion_status") or ("present" if pol == "present" else "denied" if pol == "denied" else "suspected")).strip().lower()
        sec_h = f.get("section_heading")
        ev_d = f.get("event_date")

        d = L.FactDraft(
            category=cat, concept=concept, label=L.C[concept][1] if concept in L.C else concept.replace("_", " ").replace(":", ": "),
            polarity=pol, temporal=temp, excerpt=excerpt, method="llm", confidence=0.6,
            subject=subj, relationship_to_patient=rel, assertion_status=assertion,
            section_heading=sec_h, event_date=ev_d,
        )
        val = f.get("value")
        if isinstance(val, (int, float)):
            d.value_num = float(val)
            d.value_num2 = float(f["value2"]) if isinstance(f.get("value2"), (int, float)) else None
            d.unit = _clean_modifier(f.get("unit"))
        if cat == "vital" and d.value_num is None:
            rejected.append({"reason": "vital_without_value", "concept": concept})
            continue
        for fld in ("onset", "duration", "frequency", "severity"):
            v = _clean_modifier(f.get(fld))
            if v and _grounded_modifier(v, block):
                setattr(d, fld, v)
            elif v:
                rejected.append({"reason": f"ungrounded_{fld}", "concept": concept, "value": v})
        prog = _clean_modifier(f.get("progression"))
        if prog in PROGRESSION_OK:
            ctx = block
            if (prog.endswith("worsening") and L.WORSE_RX.search(ctx)) or (prog == "improving" and L.BETTER_RX.search(ctx)) or (prog == "stable" and L.SAME_RX.search(ctx)):
                d.progression = prog
            else:
                rejected.append({"reason": "ungrounded_progression", "concept": concept, "value": prog})
        if d.category == "vital":
            d.flags += L._plausibility(d)

        valid_d, rej_reason = validate_fact_provenance(d, block)
        if not valid_d:
            rejected.append({"reason": rej_reason or "failed_provenance_validation", "concept": concept, "excerpt": excerpt[:80]})
            continue
        out.append(valid_d)
    return out, rejected


def validate_fact_provenance(d: L.FactDraft, source_text: str) -> tuple[Optional[L.FactDraft], Optional[str]]:
    """Strictly enforces provenance rules:
    - Never allow family history or maternal/paternal conditions to be attributed to the patient.
    - If the excerpt or surrounding section is under Family History, subject MUST be 'family_member'.
    - If the excerpt contains negation or form-style ': No', polarity cannot be 'present'.
    - If the statement is under a suspected/differential heading or contains 'rule out'/'suspected', assertion_status cannot be personal confirmed diagnosis.
    - If educational or disclaimer, it cannot be accepted as a patient clinical observation.
    """
    exc_lower = d.excerpt.lower()

    sections = L.parse_document_sections(source_text)
    span_offset = d.span[0] if d.span and d.span[0] > 0 else (source_text.find(d.excerpt) if d.excerpt else 0)
    sec = L._section_at(sections, max(0, span_offset)) if sections else None

    # 1. Educational / Disclaimer filter
    if (sec and sec.section_type == "educational") or L.EDUCATIONAL_INLINE_RX.search(d.excerpt) or L.DISCLAIMER_INLINE_RX.search(d.excerpt):
        return None, "rejected_educational_or_disclaimer_content"

    # 2. Section context & family member check
    is_fam_sec = bool(sec and sec.section_type == "family_history") or "family history" in exc_lower or "runs in family" in exc_lower
    inline_fam = False
    for rname, r_rx in L.FAMILY_MEMBER_TERMS:
        if r_rx.search(d.excerpt) or (sec and sec.section_type == "family_history" and r_rx.search(sec.heading)):
            inline_fam = True
            if d.subject != "family_member":
                d.subject = "family_member"
                d.relationship_to_patient = rname
                d.assertion_status = "family-history-negative" if d.polarity == "denied" else "family-history-positive"
                d.flags.append("provenance_corrected_to_family_member")
            break

    if (is_fam_sec or inline_fam) and d.subject == "patient":
        d.subject = "family_member"
        d.relationship_to_patient = "unknown"
        d.assertion_status = "family-history-negative" if d.polarity == "denied" else "family-history-positive"
        d.flags.append("provenance_corrected_from_section_heading")

    if sec and sec.section_type == "ambiguous":
        d.subject = "unknown"
        d.relationship_to_patient = "unknown"
        d.assertion_status = "unknown"
        d.polarity = "uncertain"
        if "subject_or_section_uncertain" not in d.flags:
            d.flags.append("subject_or_section_uncertain")

    # 3. Negation verification
    if d.polarity == "present":
        if L.FORM_NO_RX.search(d.excerpt) or re.search(r"\b(?:no|not|denies|absent|never|nahi|nahin|नहीं|নেই)\s+" + re.escape(d.label.lower()), exc_lower):
            d.polarity = "denied"
            d.assertion_status = "denied" if d.subject == "patient" else "family-history-negative"
            d.flags.append("negation_enforced_by_provenance_validator")

    # 4. Suspected / Differential / Rule-out
    if any(term in exc_lower for term in ("rule out", "r/o", "suspected", "possible", "differential", "query", "concern for")):
        if d.assertion_status not in ("suspected", "possible"):
            d.assertion_status = "suspected"
            d.polarity = "uncertain"
            d.flags.append("suspected_condition_not_confirmed")

    if L.QUESTION_DIAG_RX.search(d.excerpt):
        d.assertion_status = "questioned"
        d.polarity = "uncertain"
        d.flags.append("diagnostic_question_not_confirmed")

    if not d.section_heading and sec and sec.heading:
        d.section_heading = sec.heading
    if not d.event_date and sec and sec.event_date:
        d.event_date = sec.event_date

    return d, None


def _merge(lex: list[L.FactDraft], llm_facts: list[L.FactDraft]) -> list[L.FactDraft]:
    merged = list(lex)
    for lf in llm_facts:
        match = None
        for d in merged:
            if d.key() == lf.key() and d.method.startswith("lexicon"):
                if _norm(lf.excerpt) in _norm(d.excerpt) or _norm(d.excerpt) in _norm(lf.excerpt) or d.category in ("vital", "demographic"):
                    match = d
                    break
        if match is None:
            merged.append(lf)
            continue
        if match.subject != "patient" and lf.subject == "patient":
            lf.subject = match.subject
            lf.relationship_to_patient = match.relationship_to_patient
            lf.assertion_status = match.assertion_status
        if lf.category in ("vital", "demographic") and lf.value_num is not None and match.value_num is not None and abs(lf.value_num - match.value_num) > 1e-6:
            match.flags.append(f"extractor_value_disagreement:lexicon={match.value_num:g},llm={lf.value_num:g}")
            match.confidence = 0.4
            continue
        if lf.polarity != match.polarity:
            match.flags.append(f"extractor_disagreement:lexicon={match.polarity},llm={lf.polarity}")
            match.polarity, match.temporal, match.confidence = "uncertain", "unknown", 0.4
            continue
        match.method = "lexicon+llm"
        match.confidence = 0.9
        if match.temporal == "unknown" and lf.temporal != "unknown":
            match.temporal = lf.temporal
        for fld in ("onset", "duration", "frequency", "severity", "progression", "value_num", "value_num2", "unit"):
            if getattr(match, fld) is None and getattr(lf, fld) is not None:
                setattr(match, fld, getattr(lf, fld))
    for d in merged:
        if d.confidence is None:
            d.confidence = 0.85
    return merged


def _blocks(chunks: list[Chunk], limit: int = 1800) -> list[list[Chunk]]:
    out, cur, size = [], [], 0
    for c in chunks:
        if cur and size + len(c.text) > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += len(c.text) + 1
    if cur:
        out.append(cur)
    return out


def _locate(excerpt: str, chunks: list[Chunk]) -> Optional[Chunk]:
    e = _norm(excerpt)
    for c in chunks:
        if e and e in _norm(c.text):
            return c
    return None


def reading_flags(original: str, english: str, prefix: str) -> list[str]:
    """Compare two renderings of the same speech/text (native vs English): changed polarity or a symptom present in only one."""
    a = {d.concept: d for d in L.extract_symptoms(original)}
    b = {d.concept: d for d in L.extract_symptoms(english)}
    flags = []
    for c in sorted(set(a) & set(b)):
        if a[c].polarity != b[c].polarity and "uncertain" not in (a[c].polarity, b[c].polarity):
            flags.append(f"{prefix}_polarity_differs:{c}")
    pa = {c for c, d in a.items() if d.polarity != "denied"}
    pb = {c for c, d in b.items() if d.polarity != "denied"}
    if pa != pb:
        flags.append(f"{prefix}_translation_concepts_differ:only_original={','.join(sorted(pa - pb)) or '-'};only_english={','.join(sorted(pb - pa)) or '-'}")
    return flags


def translation_negation_check(original: str, english: str) -> list[str]:
    o, e = L.negation_count(original), L.negation_count(english)
    return [f"negation_count_mismatch:original={o},english={e}"] if (o == 0) != (e == 0) or abs(o - e) > 1 else []


def _norm_alnum(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or ""), flags=re.UNICODE).strip().casefold()


def is_valid_english_translation(original: str, candidate: Optional[str], source_lang: str = "") -> tuple[bool, list[str]]:
    """Validate that a machine translation of non-English source text is genuinely in English.
    Rejects empty strings, source text returned unchanged (or with only punctuation altered), and outputs
    that remain in Bengali/Indic/non-Latin script or Hinglish, while allowing legitimate English containing
    non-English proper nouns or bracketed [unclear] annotations."""
    if candidate is None or not str(candidate).strip():
        return False, ["translation_unavailable", "translation_requires_review"]
    orig_str = (original or "").strip()
    cand_str = str(candidate).strip()
    orig_non_latin = sum(1 for ch in orig_str if ord(ch) > 0x024F and (ch.isalpha() or 0x0900 <= ord(ch) <= 0x0DFF))
    det_lang, _ = L.detect_language(orig_str) if orig_str else ("en", "supported")
    eff_lang = (source_lang or "").strip()
    if not eff_lang or eff_lang == "auto":
        eff_lang = det_lang
    source_is_non_en = bool(eff_lang not in ("", "en") or orig_non_latin > 0 or det_lang != "en")
    if not source_is_non_en:
        return True, []

    orig_norm = _norm_alnum(orig_str)
    cand_norm = _norm_alnum(cand_str)
    if orig_norm and cand_norm == orig_norm:
        return False, [
            "translation_unchanged_from_source",
            "translation_non_english_output",
            "translation_unavailable",
            "translation_requires_review",
        ]

    # Strip bracketed annotations (e.g. [unclear: ...]) allowed by TRANSLATE_SYSTEM before script checks
    cleaned = re.sub(r"\[[^\]]*\]", " ", cand_str)
    latin_letters = sum(1 for ch in cleaned if "a" <= ch.lower() <= "z")
    non_latin_chars = sum(1 for ch in cleaned if ord(ch) > 0x024F and (ch.isalpha() or 0x0900 <= ord(ch) <= 0x0DFF))
    non_latin_words = re.findall(r"[\u0900-\u0DFF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]{2,}", cleaned)

    if latin_letters == 0:
        return False, ["translation_non_english_output", "translation_unavailable", "translation_requires_review"]
    if non_latin_chars > 0 and (
        non_latin_chars / (latin_letters + non_latin_chars) > 0.15
        or len(non_latin_words) >= 2
        or (len(non_latin_words) >= 1 and latin_letters < 10)
    ):
        return False, ["translation_non_english_output", "translation_unavailable", "translation_requires_review"]

    cand_lang, _ = L.detect_language(cleaned)
    if cand_lang != "en":
        return False, ["translation_non_english_output", "translation_unavailable", "translation_requires_review"]

    return True, []


def translation_quality_flags(english: str, original: str = "", source_lang: str = "") -> list[str]:
    """Flag empty, non-English, unchanged, obviously repetitive, or number-altered English renderings for human review."""
    words = (english or "").casefold().split()
    if not words:
        return ["translation_unavailable", "translation_requires_review"]
    flags: list[str] = []
    if original or (source_lang and source_lang not in ("en", "auto")):
        ok, val_flags = is_valid_english_translation(original, english, source_lang=source_lang)
        if not ok:
            flags.extend(val_flags)
    if len(words) >= 6 and len(set(words)) / len(words) < 0.5:
        flags.extend(("translation_repetition", "translation_requires_review"))
    # check for consecutive repeated 2- or 3-word n-grams
    for n in (2, 3):
        if len(words) >= n * 2:
            for i in range(len(words) - n * 2 + 1):
                if words[i:i + n] == words[i + n:i + n * 2]:
                    if "translation_repetition" not in flags:
                        flags.extend(("translation_repetition", "translation_requires_review"))
                    break
    if original:
        orig_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", original))
        en_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", english))
        if orig_nums and orig_nums != en_nums:
            flags.extend(("translation_number_mismatch", "translation_requires_review"))
    return sorted(set(flags))


def extract_source(unit: SourceUnit) -> ExtractionResult:
    """Extract facts from one source unit. Never raises for model problems: degrades and says so."""
    s = get_settings()
    res = ExtractionResult()
    all_text = "\n".join(c.text for c in unit.chunks)
    lex: list[L.FactDraft] = []
    for c in unit.chunks:
        for d in L.extract_all(c.text, c.locator):
            d.confidence = None
            d.flags = list(d.flags)
            if c.quality < 0.7:
                d.flags.append("low_source_quality")
            d.span = (d.span[0], d.span[1])
            d.method = "lexicon"
            d.confidence = round(0.85 * min(1.0, max(0.2, c.quality)), 2)
            lex.append(d)

    llm_facts: list[L.FactDraft] = []
    translations: list[str] = []
    if s.llm_enabled and all_text.strip():
        try:
            import time
            t0 = time.monotonic()
            for block in _blocks(unit.chunks):
                btxt = "\n".join(c.text for c in block)
                data, meta = llm.chat_json(prompts.EXTRACT_SYSTEM, btxt, prompts.EXTRACT_SCHEMA)
                res.model, res.raw_output = meta["model"], (res.raw_output + "\n" + meta["raw"])[-6000:]
                facts, rej = _validate_llm_facts(data, btxt)
                res.rejected += rej
                for f in facts:
                    ch = _locate(f.excerpt, block)
                    f.locator = ch.locator if ch else block[0].locator
                    if ch and ch.quality < 0.7:
                        f.flags.append("low_source_quality")
                        f.confidence = round(0.6 * max(0.2, ch.quality), 2)
                llm_facts += facts
                tr = data.get("english_translation")
                if isinstance(tr, str) and tr.strip():
                    translations.append(tr.strip())
                if data.get("translation_uncertain") is True:
                    res.translation_flags.append("translation_self_reported_uncertain")
            res.latency_ms = int((time.monotonic() - t0) * 1000)
            res.method, res.prompt_version = "lexicon+llm", prompts.EXTRACT_PROMPT_VERSION
        except llm.LLMUnavailable as exc:
            res.status, res.error, res.method = "degraded", str(exc)[:300], "lexicon"
            log.warning("LLM extraction unavailable for %s %s: %s", unit.kind, unit.id, exc)
    else:
        res.status, res.error = "degraded", "LLM disabled or no text"

    res.drafts = _merge(lex, llm_facts)
    valid_merged: list[L.FactDraft] = []
    seen_dedupe = set()
    for d in res.drafts:
        validated, rej = validate_fact_provenance(d, all_text)
        if not validated:
            res.rejected.append({"reason": rej or "provenance_rejected", "concept": d.concept, "excerpt": d.excerpt[:80]})
            continue
        sig = (validated.category, validated.concept, validated.subject, validated.relationship_to_patient, validated.polarity, validated.temporal, validated.value_num, validated.value_text, _norm(validated.excerpt))
        if sig in seen_dedupe:
            continue
        seen_dedupe.add(sig)
        valid_merged.append(validated)
    res.drafts = valid_merged
    if res.status == "degraded" and not res.drafts and all_text.strip() and unit.language not in ("en", "hi", "hi-Latn"):
        res.status = "failed"
        res.error = (res.error or "") + " | language outside deterministic coverage"

    en: Optional[str] = None
    if unit.language in ("en",):
        en = unit.translation_en or all_text
    else:
        for cand in (unit.translation_en, " ".join(translations) if translations else None):
            if cand and cand.strip():
                ok, val_flags = is_valid_english_translation(all_text, cand, source_lang=unit.language)
                if ok:
                    en = cand.strip()
                    break
                res.translation_flags.extend(val_flags)
        if not en and s.llm_enabled and all_text.strip() and res.status != "degraded":
            t_en, t_flags = translate_only(all_text, source_lang=unit.language)
            if t_en:
                en = t_en
                res.translation_flags = [
                    f for f in res.translation_flags
                    if f not in ("translation_unavailable", "translation_non_english_output", "translation_unchanged_from_source", "translation_requires_review")
                ] + t_flags
            else:
                res.translation_flags.extend(t_flags)
    res.translation_en = en
    if en and unit.language != "en":
        res.translation_flags += translation_negation_check(all_text, en)
        # cross-check: run the lexicon on the English rendering; polarity disagreement => uncertain + flag
        en_facts = {(d.category, d.concept): d for d in L.extract_symptoms(en)}
        for d in res.drafts:
            other = en_facts.get((d.category, d.concept))
            if other and other.polarity != d.polarity and d.category == "symptom" and "uncertain" not in (other.polarity, d.polarity):
                d.flags.append(f"translation_disagreement:original={d.polarity},english={other.polarity}")
                d.polarity, d.temporal, d.confidence = "uncertain", "unknown", 0.3
                res.translation_flags.append(f"polarity_changed_in_translation:{d.concept}")
        orig_present = {d.concept for d in res.drafts if d.category == "symptom" and d.polarity in ("present", "uncertain") and not d.concept.startswith("other:")}
        en_present = {c for (cat, c), d in en_facts.items() if cat == "symptom" and d.polarity in ("present", "uncertain")}
        if orig_present != en_present:
            only_o, only_e = sorted(orig_present - en_present), sorted(en_present - orig_present)
            res.translation_flags.append(f"translation_concepts_differ:only_original={','.join(only_o) or '-'};only_english={','.join(only_e) or '-'}")
    elif not en and unit.language not in ("en",) and all_text.strip():
        if "translation_unavailable" not in res.translation_flags:
            res.translation_flags.append("translation_unavailable")
    res.translation_flags = sorted(set(res.translation_flags))
    return res


def translate_only(text: str, source_lang: str = "") -> tuple[Optional[str], list[str]]:
    """Used for short texts (e.g. staff-facing display or non-English audio transcripts) via local Ollama."""
    if not (text or "").strip():
        return None, ["translation_unavailable", "translation_requires_review"]
    try:
        data, _ = llm.chat_json(prompts.TRANSLATE_SYSTEM, text, prompts.TRANSLATE_SCHEMA, num_predict=800)
        en = str(data.get("english", "")).strip()
        ok, val_flags = is_valid_english_translation(text, en, source_lang=source_lang)
        if not ok:
            return None, sorted(set(val_flags))
        flags = list(translation_negation_check(text, en))
        flags += translation_quality_flags(en, text, source_lang=source_lang)
        if data.get("uncertain"):
            flags.append("translation_self_reported_uncertain")
        return en, sorted(set(flags))
    except llm.LLMUnavailable:
        return None, ["translation_unavailable", "translation_requires_review"]
