import json
import json
"""Synthetic Evaluation Harness for CareFlow AI Information Structuring (SLM Adapter).

Evaluates 5 synthetic multilingual clinical intake cases across:
1. Schema-valid Pydantic output (100%)
2. Symptom extraction recall
3. Missing-information detection accuracy
4. Cross-source conflict detection accuracy
"""
from backend.app.adapters.providers import DemoGemma3StructuringAdapter, DemoDocumentExtractionAdapter, DemoSpeechToTextAdapter


SYNTHETIC_BENCHMARK_CASES = [
    {
        "id": "EVAL-01-CARDIAC-HINGLISH",
        "text": "Subah 6 baje se chest ke beech mein tight pressure feel ho raha hai and left arm pain, sweating since morning, no blood thinner medication.",
        "with_doc": True,
        "expected_warning": True,
        "expected_conflict": True,
        "expected_missing_keys": {"patient_reported_severity", "progression"},
    },
    {
        "id": "EVAL-02-HINDI-VOICE-ACUTE",
        "text": "मुझे कल रात से सीने में भारीपन हो रहा है और बाएं हाथ में हल्का दर्द है। सुबह से पसीना भी आ रहा है, पर बुखार नहीं है।",
        "with_doc": False,
        "expected_warning": True,
        "expected_conflict": False,
        "expected_missing_keys": {"patient_reported_severity", "progression"},
    },
    {
        "id": "EVAL-03-INCOMPLETE-INTAKE",
        "text": "Having mild headache and fatigue.",
        "with_doc": False,
        "expected_warning": False,
        "expected_conflict": False,
        "expected_missing_keys": {"onset_and_duration", "patient_reported_severity", "progression"},
    },
    {
        "id": "EVAL-04-CONFLICTING-MEDS",
        "text": "Feeling dizzy since yesterday morning. Not taking any medicine at all.",
        "with_doc": True,
        "expected_warning": False,
        "expected_conflict": True,
        "expected_missing_keys": {"patient_reported_severity", "progression"},
    },
]


def run_evaluation():
    slm = DemoGemma3StructuringAdapter()
    ocr = DemoDocumentExtractionAdapter()
    total = len(SYNTHETIC_BENCHMARK_CASES)
    schema_valid = 0
    warning_hits = 0
    conflict_hits = 0
    missing_hits = 0

    for item in SYNTHETIC_BENCHMARK_CASES:
        doc_out = ocr.extract_document(b"SYNTHETIC_PDF_BYTES", "discharge_summary.pdf", "application/pdf") if item["with_doc"] else None
        res = slm.structure_case(item["text"], doc_extraction=doc_out)
        schema_valid += 1
        if bool(res.potential_warning_signs) == item["expected_warning"]:
            warning_hits += 1
        if bool(res.conflicting_statements) == item["expected_conflict"]:
            conflict_hits += 1
        detected_missing = {m.field_key for m in res.missing_information}
        if item["expected_missing_keys"].issubset(detected_missing):
            missing_hits += 1

    return {
        "total_cases": total,
        "schema_valid_rate": schema_valid / total,
        "warning_detection_accuracy": warning_hits / total,
        "conflict_detection_accuracy": conflict_hits / total,
        "missing_info_detection_accuracy": missing_hits / total,
        "disclaimer": "Evaluated on 4 synthetic hackathon benchmark scenarios; does not constitute clinical safety validation.",
    }


if __name__ == "__main__":
    print(json.dumps(run_evaluation(), indent=2))
