"""The supplied seed file is a synthetic software-regression fixture, never clinical ground truth."""
import copy

from ..scripts import seed_cases_eval as E
from ..config import get_settings
from ..services import rules_engine as RE


def test_seed_dataset_schema_and_ids_are_valid():
    rows = E.load_records()
    assert len(rows) == 100
    assert len({r["case_id"] for r in rows}) == 100
    assert all(r["raw_symptom_text"].strip() for r in rows)


def test_dataset_regression_uses_symptom_text_not_embedded_priority_labels():
    rows = E.load_records()
    row = next(r for r in rows if r["case_id"] == "CF-2026-001")
    rules, _ = RE.load_ruleset(get_settings().rules_path)
    baseline = E.evaluate_record(row, rules)
    altered = copy.deepcopy(row)
    altered["triage_result"] = {"urgency_score": 0, "triage_level": "GREEN", "is_urgent": False}
    altered["clinical_audit"] = {"status": "APPROVED", "reviewed_by": "Untrusted fixture field"}
    assert E.evaluate_record(altered, rules) == baseline
    assert baseline["proposal"] == "URGENT"
    assert baseline["demonstration_only"] is True and baseline["synthetic"] is True


def test_dataset_file_has_no_duplicate_ids_and_reports_unknown_clinical_validation():
    report = E.run()
    assert report["records"] == 100 and report["clinical_validation"] == "NOT PERFORMED"
    assert "ignored" in report["label_policy"]
    assert sum(report["proposal_counts"].values()) == 100
