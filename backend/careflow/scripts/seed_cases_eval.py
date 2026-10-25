"""Run the supplied 100-row synthetic dataset through the local lexicon and deterministic demo rules.

This is a regression exercise, not clinical validation. The input's triage labels, scores and
clinical_audit claims are deliberately ignored; none are treated as ground truth or approval.
Run: python -m backend.careflow.scripts.seed_cases_eval [path/to/seed_cases_100.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..config import ROOT, get_settings
from ..services import lexicon
from ..services import rules_engine as RE
from ..services.rules_engine import CaseContext, FactView

DEFAULT_PATH = ROOT / "seed_cases_100.json"
VITAL_FIELDS = {
    "heart_rate": ("heart_rate", "bpm"), "blood_pressure_systolic": ("blood_pressure", "mmHg"),
    "respiratory_rate": ("resp_rate", "breaths/min"), "spo2": ("spo2", "%"),
    "temperature_celsius": ("temperature", "C"),
}


def load_records(path: Path = DEFAULT_PATH) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Dataset root must be a JSON array.")
    seen: set[str] = set()
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"Record {i} must be an object.")
        case_id, text = row.get("case_id"), row.get("raw_symptom_text")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"Record {i} has a missing or duplicate case_id.")
        if not isinstance(text, str) or not text.strip() or len(text) > 60000:
            raise ValueError(f"{case_id}: raw_symptom_text must be nonempty text up to 60,000 characters.")
        if not isinstance(row.get("patient_age"), int) or not 1 <= row["patient_age"] <= 120:
            raise ValueError(f"{case_id}: patient_age is outside the supported data range.")
        if not isinstance(row.get("vitals"), dict):
            raise ValueError(f"{case_id}: vitals must be an object.")
        seen.add(case_id)
    return raw


def evaluate_record(row: dict, ruleset: dict) -> dict:
    """Extract only from raw symptom text plus explicit age/vital fields; ignore labels/audit metadata."""
    facts: list[FactView] = []
    for i, draft in enumerate(lexicon.extract_all(row["raw_symptom_text"], locator="synthetic dataset text")):
        facts.append(FactView(id=f"{row['case_id']}-text-{i}", category=draft.category, concept=draft.concept, polarity=draft.polarity,
            temporal=draft.temporal, value_num=draft.value_num, value_num2=draft.value_num2, unit=draft.unit, duration=draft.duration,
            onset=draft.onset, progression=draft.progression, verification="unverified", flags=list(draft.flags), source_type="dataset"))
    facts.append(FactView(id=f"{row['case_id']}-age", category="demographic", concept="age", polarity="present", temporal="current",
        value_num=float(row["patient_age"]), unit="years", source_type="dataset", verification="unverified"))
    vitals = row["vitals"]
    for field, (concept, unit) in VITAL_FIELDS.items():
        value = vitals.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts.append(FactView(id=f"{row['case_id']}-{field}", category="vital", concept=concept, polarity="present", temporal="unknown",
                value_num=float(value), value_num2=float(vitals["blood_pressure_diastolic"]) if field == "blood_pressure_systolic" and isinstance(vitals.get("blood_pressure_diastolic"), (int, float)) else None,
                unit=unit, source_type="dataset", verification="unverified"))
    result = RE.evaluate(CaseContext(facts=facts, conflicts=[], extraction_degraded=True, has_any_input=True), ruleset)
    return {"case_id": row["case_id"], "synthetic": True, "proposal": result.queue, "summary": result.summary,
        "ruleset_version": result.ruleset_version, "demonstration_only": result.demonstration_only, "triggered_rules": [r["rule_id"] for r in result.triggered_rules],
        "fact_count": len(facts), "source": "raw symptom text and supplied fields only"}


def run(path: Path = DEFAULT_PATH) -> dict:
    rows = load_records(path)
    ruleset, _ = RE.load_ruleset(get_settings().rules_path)
    results = [evaluate_record(row, ruleset) for row in rows]
    counts = {q: sum(1 for r in results if r["proposal"] == q) for q in ("URGENT", "NEEDS_REVIEW", "NORMAL")}
    return {"dataset": path.name, "records": len(results), "synthetic_only": True, "clinical_validation": "NOT PERFORMED",
        "label_policy": "triage_result scores/labels and clinical_audit claims ignored", "proposal_counts": counts, "cases": results}


if __name__ == "__main__":
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    report = run(input_path)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
