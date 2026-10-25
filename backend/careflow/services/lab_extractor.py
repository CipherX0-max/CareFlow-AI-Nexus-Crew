"""Deterministic lab-result parser for OCR/PDF text of blood reports.

For each result row it extracts: test name (+ normalised id when recognised), printed value, unit, the laboratory's printed
reference interval, any explicit H/L flag, page/line location and review reasons.

Safety rules:
* A line only counts as a result when it has a numeric value AND (a unit OR a printed reference interval). Narrative text is ignored.
* A value is compared with a reference interval only when value, unit and interval were all read, the interval is well-formed and
  the OCR text is not flagged unreliable. Otherwise `interpretation` is "not_assessed" with the reasons.
* An out-of-range value is "outside the report's stated reference interval". Nothing here names a disease or a diagnosis.
* A printed flag that disagrees with the computed comparison is surfaced for human review, never silently resolved.
* Nothing is invented: missing fields stay None.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

# normalised identifiers (lower-case aliases -> id). Identification only; no reference ranges are assumed.
TEST_IDS = {
    "haemoglobin": "HGB", "hemoglobin": "HGB", "hb": "HGB", "hgb": "HGB",
    "rbc": "RBC", "rbc count": "RBC", "red blood cell count": "RBC", "total rbc count": "RBC",
    "wbc": "WBC", "wbc count": "WBC", "total wbc count": "WBC", "white blood cell count": "WBC", "total leucocyte count": "WBC", "tlc": "WBC",
    "platelet count": "PLT", "platelets": "PLT", "plt": "PLT",
    "haematocrit": "HCT", "hematocrit": "HCT", "hct": "HCT", "pcv": "HCT",
    "mcv": "MCV", "mch": "MCH", "mchc": "MCHC", "rdw": "RDW",
    "glucose": "GLU", "blood glucose": "GLU", "fasting glucose": "GLU", "creatinine": "CREAT", "urea": "UREA", "sodium": "NA", "potassium": "K",
    "neutrophils": "NEUT", "lymphocytes": "LYMPH", "eosinophils": "EOS", "monocytes": "MONO", "basophils": "BASO",
    "bilirubin total": "BILI_T", "total bilirubin": "BILI_T", "sgot": "AST", "ast": "AST", "sgpt": "ALT", "alt": "ALT", "tsh": "TSH", "hba1c": "HBA1C",
}

_NUM = r"\d{1,6}(?:[.,]\d{1,4})?"
_UNIT = (r"(?:g/dl|g/l|mg/dl|mmol/l|[µu]mol/l|meq/l|iu/l|u/l|ng/ml|pg/ml|pmol/l|nmol/l|fl|pg|%|"
         r"(?:x|×)?\s?10\^?\d+\s?/\s?[µu]?l|(?:x|×)?\s?10\^?\d+\s?/\s?mm\^?3|lakhs?/c?mm|millions?/c?mm|cells/[µu]l|/[µu]l|/mm\^?3|/cumm|thou/[µu]l|mill/[µu]l|"
         r"miu/l|miu/ml|mu/l|mmhg|sec|s)")
_REF = rf"(?:{_NUM}\s*[-–—]\s*{_NUM}|[<>≤≥]\s*{_NUM})"
_FLAG = r"(?:HIGH|LOW|CRITICAL|H|L)"

_ROW = re.compile(
    rf"^\s*(?P<name>[A-Za-z][A-Za-z0-9 /().,'\-]{{1,48}}?)\s*[:\s]\s*"
    rf"(?P<value>[<>]?\s?{_NUM})\s*(?P<unit>{_UNIT})?\s*"
    rf"(?:\(?(?:ref(?:erence)?(?:\s+range|\s+interval)?\s*[:\-]?\s*)?(?P<ref>{_REF})\)?)?\s*(?:(?P<unit2>{_UNIT})\s*)?(?P<flag>{_FLAG})?\s*$",
    re.IGNORECASE,
)
_NAME_BAD = re.compile(r"\b(page|date|age|phone|mobile|tel|id|no\.?|number|sample|reg|dr|collected|reported|years?|yrs?)\b", re.I)
_DATE = re.compile(r"(?:collected|reported|report date|sample date|date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.I)
_DEC_AMBIG = re.compile(r"^\d{1,3},\d{3}$")


@dataclass
class LabResult:
    test_name: str
    test_id: Optional[str]
    raw_value: str
    value_num: Optional[float]
    unit: Optional[str]
    ref_text: Optional[str]
    ref_low: Optional[float]
    ref_high: Optional[float]
    printed_flag: Optional[str]          # H | L | CRITICAL as printed on the report
    computed_flag: Optional[str]         # H | L | None -- only when comparison was reliable
    interpretation: str                  # above_reference | below_reference | within_reference | not_assessed
    locator: str                         # "page 1, line 7"
    source_line: str                     # verbatim OCR line (supports the finding)
    report_date: Optional[str]
    ocr_confidence: Optional[float]      # 0..1 page-level, None when unknown
    review_needed: bool
    review_reasons: list[str] = field(default_factory=list)
    verification: str = "unverified"     # unverified until a clinician confirms


def _num(s: str) -> Optional[float]:
    try:
        return float(s.strip().lstrip("<>").strip().replace(",", "."))
    except ValueError:
        return None


def _parse_ref(ref: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    if not ref:
        return None, None
    m = re.match(rf"({_NUM})\s*[-–—]\s*({_NUM})", ref)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        return (lo, hi) if lo is not None and hi is not None and lo < hi else (None, None)  # reversed/garbled interval -> unusable
    m = re.match(rf"[<≤]\s*({_NUM})", ref)
    if m:
        return None, _num(m.group(1))
    m = re.match(rf"[>≥]\s*({_NUM})", ref)
    return (_num(m.group(1)), None) if m else (None, None)


def _norm_flag(f: Optional[str]) -> Optional[str]:
    if not f:
        return None
    f = f.upper()
    return {"H": "H", "HIGH": "H", "L": "L", "LOW": "L", "CRITICAL": "CRITICAL"}.get(f)


def _norm_name(n: str) -> str:
    return re.sub(r"\s+", " ", n).strip(" :.-").strip()


def extract_lab_results(text: str, locator_prefix: str = "page 1", ocr_confidence: Optional[float] = None, low_conf_threshold: float = 0.7) -> list[LabResult]:
    """Never raises; returns [] when nothing looks like a result row. `ocr_confidence` is 0..1."""
    out: list[LabResult] = []
    if not text:
        return out
    dm = _DATE.search(text)
    report_date = dm.group(1) if dm else None
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.rstrip()
        if len(line) < 4 or len(line) > 160:
            continue
        m = _ROW.match(line)
        if not m:
            continue
        name = _norm_name(m.group("name"))
        unit = (m.group("unit") or m.group("unit2") or "").strip() or None
        ref = (m.group("ref") or "").strip() or None
        if not name or _NAME_BAD.search(name) or (unit is None and ref is None):
            continue  # not a lab row: needs a unit or a printed reference interval
        raw = m.group("value").strip()
        value = _num(raw)
        low, high = _parse_ref(ref)
        printed = _norm_flag(m.group("flag"))
        reasons: list[str] = []
        if unit is None:
            reasons.append("unit_missing")
        if ref is None:
            reasons.append("reference_interval_missing")
        elif low is None and high is None:
            reasons.append("reference_interval_unreadable")
        if _DEC_AMBIG.match(raw):
            reasons.append("decimal_or_thousands_separator_ambiguous")
        if raw.startswith(("<", ">")):
            reasons.append("value_is_inequality")
        if ocr_confidence is not None and ocr_confidence < low_conf_threshold:
            reasons.append("low_ocr_confidence")
        comparable = value is not None and unit is not None and (low is not None or high is not None) and not any(
            r in reasons for r in ("decimal_or_thousands_separator_ambiguous", "value_is_inequality", "low_ocr_confidence"))
        computed, interp = None, "not_assessed"
        if comparable:
            if low is not None and value < low:
                computed, interp = "L", "below_reference"
            elif high is not None and value > high:
                computed, interp = "H", "above_reference"
            else:
                interp = "within_reference"
        if printed in ("H", "L") and comparable and computed != printed:
            reasons.append("printed_flag_disagrees_with_comparison")
        if printed in ("H", "L") and interp == "within_reference":
            reasons.append("printed_flag_but_value_within_printed_interval")
        out.append(LabResult(
            test_name=name, test_id=TEST_IDS.get(name.lower()), raw_value=raw, value_num=value, unit=unit, ref_text=ref, ref_low=low, ref_high=high,
            printed_flag=printed, computed_flag=computed, interpretation=interp, locator=f"{locator_prefix}, line {i}", source_line=line.strip(),
            report_date=report_date, ocr_confidence=ocr_confidence, review_needed=bool(reasons) or interp == "not_assessed", review_reasons=reasons))
    return out


def to_dict(r: LabResult) -> dict:
    return asdict(r)


# Symptom <-> test relevance is a *prompt for review*, never a diagnosis or a disease association.
RELEVANCE = {
    "HGB": {"weakness", "dizziness", "shortness_of_breath", "palpitations", "syncope"},
    "RBC": {"weakness", "dizziness", "shortness_of_breath"},
    "WBC": {"fever", "cough"},
    "PLT": set(),
    "GLU": {"dizziness", "weakness", "vomiting", "nausea"},
}


def relevant_to_symptoms(results: list[dict], symptom_concepts: set[str]) -> list[dict]:
    """Abnormal-vs-stated-interval results that share a symptom area with the patient's reported symptoms. Wording is deliberately neutral."""
    hits = []
    for r in results:
        if r.get("interpretation") not in ("above_reference", "below_reference"):
            continue
        overlap = sorted(RELEVANCE.get(r.get("test_id") or "", set()) & symptom_concepts)
        hits.append({"test_name": r["test_name"], "interpretation": r["interpretation"], "locator": r["locator"],
                     "may_relate_to_reported": overlap,
                     "note": "Outside the report's own stated interval; not a diagnosis. A clinician must interpret it." + (
                         " The patient also reported: " + ", ".join(overlap) + "." if overlap else "")})
    return hits
