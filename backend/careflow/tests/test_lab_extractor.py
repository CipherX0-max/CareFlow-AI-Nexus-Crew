"""Pure unit tests for the deterministic lab-result parser (synthetic text only)."""
import pytest

from ..services.lab_extractor import extract_lab_results, relevant_to_symptoms, to_dict

CBC = """COMPLETE BLOOD COUNT
Collected: 12/03/2026
Haemoglobin        10.2   g/dL     13.0 - 17.0   L
WBC Count          12.5   x10^3/uL 4.0 - 11.0    H
Platelet Count     95     x10^3/uL 150 - 400
RBC Count          4.1    x10^6/uL 4.5 - 5.9
Sodium             138    mEq/L    136 - 145
"""


def by(rows, frag):
    return next(r for r in rows if frag in r.test_name.lower())


def test_values_units_ranges_flags_and_location_are_extracted():
    rows = extract_lab_results(CBC, "page 1")
    hb = by(rows, "haemoglobin")
    assert (hb.value_num, hb.unit, hb.ref_low, hb.ref_high, hb.printed_flag, hb.test_id) == (10.2, "g/dL", 13.0, 17.0, "L", "HGB")
    assert hb.locator.startswith("page 1, line") and "Haemoglobin" in hb.source_line and hb.report_date == "12/03/2026"
    assert hb.interpretation == "below_reference" and hb.verification == "unverified"


def test_comparison_uses_the_reports_own_interval_and_flags_unflagged_abnormal_values():
    rows = extract_lab_results(CBC)
    assert by(rows, "wbc").interpretation == "above_reference"
    plt = by(rows, "platelet")
    assert plt.printed_flag is None and plt.computed_flag == "L" and plt.interpretation == "below_reference"
    assert by(rows, "sodium").interpretation == "within_reference"


def test_narrative_text_is_not_parsed_as_results():
    assert extract_lab_results("Patient is 45 years old with chest pain for 2 days. BP 120/80. Page 2 of 3") == []
    assert extract_lab_results("") == []


def test_missing_unit_blocks_comparison_and_requires_review():
    rows = extract_lab_results("Haemoglobin  10.2  13.0 - 17.0 L")
    assert rows and "unit_missing" in rows[0].review_reasons and rows[0].interpretation == "not_assessed" and rows[0].review_needed


def test_missing_reference_interval_is_never_replaced_by_a_universal_range():
    rows = extract_lab_results("Haemoglobin  10.2 g/dL")
    assert rows[0].interpretation == "not_assessed" and "reference_interval_missing" in rows[0].review_reasons and rows[0].computed_flag is None


def test_ambiguous_decimal_and_low_ocr_confidence_block_interpretation():
    amb = extract_lab_results("TSH   1,234  mIU/L  0.4 - 4.0")[0]
    assert "decimal_or_thousands_separator_ambiguous" in amb.review_reasons and amb.interpretation == "not_assessed"
    low = extract_lab_results(CBC, ocr_confidence=0.4)
    assert low and all(r.review_needed and r.interpretation == "not_assessed" and "low_ocr_confidence" in r.review_reasons for r in low)


def test_printed_flag_disagreeing_with_comparison_is_surfaced():
    r = extract_lab_results("Glucose  90  mg/dL  70 - 100  H")[0]
    assert r.interpretation == "within_reference" and "printed_flag_disagrees_with_comparison" in r.review_reasons


def test_garbled_reversed_interval_is_unusable():
    r = extract_lab_results("Glucose  90  mg/dL  100 - 70")[0]
    assert r.ref_low is None and "reference_interval_unreadable" in r.review_reasons and r.interpretation == "not_assessed"


def test_relevance_is_neutral_and_names_no_disease():
    rows = [to_dict(r) for r in extract_lab_results(CBC)]
    hits = relevant_to_symptoms(rows, {"weakness"})
    assert hits and all("not a diagnosis" in h["note"] for h in hits)
    hb = next(h for h in hits if "haemoglobin" in h["test_name"].lower())
    assert hb["may_relate_to_reported"] == ["weakness"]
    text = " ".join(h["note"] for h in hits).lower()
    assert "anaemia" not in text and "anemia" not in text and "infection" not in text
