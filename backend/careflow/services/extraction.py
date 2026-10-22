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
        d = L.FactDraft(category=cat, concept=concept, label=L.C[concept][1] if concept in L.C else concept.replace("_", " ").replace(":", ": "),
                        polarity=pol, temporal=temp, excerpt=excerpt, method="llm", confidence=0.6)
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
        out.append(d)
    return out, rejected


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
    if res.status == "degraded" and not res.drafts and all_text.strip() and unit.language not in ("en", "hi", "hi-Latn"):
        res.status = "failed"
        res.error = (res.error or "") + " | language outside deterministic coverage"

    en = unit.translation_en or (" ".join(translations) if translations else None)
    if unit.language in ("en",) and not unit.translation_en:
        en = all_text
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
        res.translation_flags.append("translation_unavailable")
    return res


def translate_only(text: str) -> tuple[Optional[str], list[str]]:
    """Used for short texts (e.g. staff-facing display) when extraction ran without the LLM."""
    try:
        data, _ = llm.chat_json(prompts.TRANSLATE_SYSTEM, text, prompts.TRANSLATE_SCHEMA, num_predict=800)
        en = str(data.get("english", "")).strip()
        flags = translation_negation_check(text, en) if en else ["translation_unavailable"]
        if data.get("uncertain"):
            flags.append("translation_self_reported_uncertain")
        return en or None, flags
    except llm.LLMUnavailable:
        return None, ["translation_unavailable"]
