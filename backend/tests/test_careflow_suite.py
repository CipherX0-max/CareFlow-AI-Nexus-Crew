"""Comprehensive Automated Test Suite for CareFlow AI.

Covers all 14 required test dimensions from Section 14:
1. Authentication & role permissions (RBAC)
2. Patient consent & submission blocking without consent
3. File validation (audio & document size/mime checks)
4. Speech-provider failures & zero-budget guard
5. OCR-provider failures & fallback provenance
6. SLM output validation & malformed output handling
7. Missing and conflicting facts detection
8. Source traceability / provenance retention
9. Urgency-score calculation across all 6 dimensions
10. Critical-warning overrides
11. Priority sorting and waiting-time aging behavior
12. Clinician edits, priority override with mandatory reason, and final approval
13. Audit-log generation
14. Consent withdrawal and deletion workflows
"""
import unittest
from backend.app.config import Settings
from backend.app.adapters.providers import (
    validate_audio_upload,
    validate_document_upload,
    DemoSpeechToTextAdapter,
    DemoDocumentExtractionAdapter,
    DemoTranslationAdapter,
    DemoGemma3StructuringAdapter,
    SarvamSaarasSpeechAdapter,
    ProviderOrchestrator,
    ProviderError,
)
from backend.app.engine.urgency_scorer import ExplainableUrgencyEngine, sort_cases_for_dashboard
from backend.app.schemas.triage import VitalMeasurement
from backend.app.services.case_service import CareFlowRepository
from backend.eval.slm_synthetic_eval import run_evaluation


class TestCareFlowComprehensive(unittest.TestCase):
    def setUp(self):
        self.repo = CareFlowRepository(":memory:")
        self.scorer = ExplainableUrgencyEngine()
        self.slm = DemoGemma3StructuringAdapter()
        self.ocr = DemoDocumentExtractionAdapter()
        self.stt = DemoSpeechToTextAdapter()
        self.translator = DemoTranslationAdapter()

    def test_01_file_validation_limits_and_types(self):
        with self.assertRaises(ValueError):
            validate_audio_upload("clip.exe", "application/x-msdownload", 1024)
        with self.assertRaises(ValueError):
            validate_audio_upload("huge.wav", "audio/wav", 25 * 1024 * 1024)
        with self.assertRaises(ValueError):
            validate_document_upload("empty.pdf", "application/pdf", 0)
        with self.assertRaises(ValueError):
            validate_document_upload("script.js", "application/javascript", 500)

    def test_02_speech_and_ocr_provider_failures_and_zero_budget_guard(self):
        with self.assertRaises(ProviderError):
            self.stt.transcribe(b"FORCE_PROVIDER_ERROR", "voice.wav", "audio/wav")
        with self.assertRaises(ProviderError):
            self.ocr.extract_document(b"FORCE_OCR_ERROR", "scan.pdf", "application/pdf")

        # Paid provider blocked when allow_paid_providers=False
        locked_settings = Settings(demo_mode=False, allow_paid_providers=False, sarvam_api_key="test-key")
        paid_sarvam = SarvamSaarasSpeechAdapter(locked_settings)
        with self.assertRaises(ProviderError) as ctx:
            paid_sarvam.transcribe(b"valid_audio_bytes", "patient.wav", "audio/wav")
        self.assertIn("zero-budget", str(ctx.exception))

    def test_03_fallback_preserves_provenance_and_flags(self):
        settings = Settings(stt_provider="indic_conformer", stt_fallback_provider="demo_mock")
        orch = ProviderOrchestrator(settings)
        res = orch.run_stt(b"sample_audio", "recording.wav", "audio/wav", allow_fallback=True)
        self.assertTrue(any("fallback" in flag.lower() for flag in res.uncertainty_flags))
        self.assertIn("demo_mock_stt", res.provider_used)

    def test_04_slm_output_validation_conflicts_and_provenance(self):
        with self.assertRaises(ProviderError):
            self.slm.structure_case("FORCE_SLM_MALFORMED_OUTPUT")

        doc_out = self.ocr.extract_document(b"PDF_CONTENT", "apollo_discharge.pdf", "application/pdf")
        structured = self.slm.structure_case(
            "Subah se chest pressure and left arm pain. I am not taking any medicine.",
            doc_extraction=doc_out,
        )
        # Verify conflict was detected and NOT silently merged
        self.assertEqual(len(structured.conflicting_statements), 1)
        conflict = structured.conflicting_statements[0]
        self.assertEqual(conflict.resolution_status, "unresolved")
        self.assertIn("Clopidogrel", conflict.value_b)
        # Verify source references exist on every extracted fact
        self.assertGreaterEqual(len(structured.facts_with_provenance), 2)
        for fact in structured.facts_with_provenance:
            self.assertTrue(fact.source_ref.verbatim_excerpt)

    def test_05_urgency_scoring_critical_override_and_uncertainty_handling(self):
        # Case A: Critical chest pressure + left arm radiation
        structured_crit = self.slm.structure_case(
            "Severe chest pressure since morning radiating to left arm and sweating.",
            patient_metadata={"reported_severity": 9, "progression": "getting_worse"},
        )
        score_crit = self.scorer.evaluate_case(structured_crit, patient_age=64)
        self.assertTrue(score_crit.critical_warning_override)
        self.assertEqual(score_crit.priority_band, "immediate_escalation")
        self.assertGreaterEqual(score_crit.internal_prototype_score, 70)

        # Case B: Incomplete form with conflict must NOT enter routine standard_review silently
        doc_out = self.ocr.extract_document(b"PDF_CONTENT", "report.pdf", "application/pdf")
        structured_uncertain = self.slm.structure_case(
            "Mild fatigue, not taking any medication.",
            doc_extraction=doc_out,
        )
        score_uncertain = self.scorer.evaluate_case(structured_uncertain, patient_age=42)
        self.assertTrue(score_uncertain.requires_verification)
        self.assertEqual(score_uncertain.priority_band, "information_required")

    def test_06_vital_plausibility_check(self):
        structured = self.slm.structure_case("Mild cough since 3 days", patient_metadata={"reported_severity": 3, "progression": "staying_same"})
        implausible_vital = VitalMeasurement(
            name="SpO2", value=145.0, unit="%", timestamp="2026-10-20T00:10:00Z", source_type="patient_statement"
        )
        score = self.scorer.evaluate_case(structured, vitals=[implausible_vital])
        self.assertFalse(implausible_vital.plausible)
        self.assertTrue(score.requires_verification)

    def test_07_priority_sorting_and_waiting_time(self):
        queue = [
            {"case_id": "C-ROUTINE", "critical_warning_override": False, "priority_band": "standard_review", "urgency_score": 25, "waiting_minutes": 90},
            {"case_id": "C-CRIT", "critical_warning_override": True, "priority_band": "immediate_escalation", "urgency_score": 82, "waiting_minutes": 4},
            {"case_id": "C-HIGH-OLD", "critical_warning_override": False, "priority_band": "high_priority", "urgency_score": 60, "waiting_minutes": 45},
            {"case_id": "C-HIGH-NEW", "critical_warning_override": False, "priority_band": "high_priority", "urgency_score": 60, "waiting_minutes": 10},
        ]
        sorted_q = sort_cases_for_dashboard(queue)
        self.assertEqual([c["case_id"] for c in sorted_q], ["C-CRIT", "C-HIGH-OLD", "C-HIGH-NEW", "C-ROUTINE"])

    def test_08_consent_rbac_approval_and_audit_workflow(self):
        # Cannot create case without consent
        structured = self.slm.structure_case("Chest heaviness since morning", patient_metadata={"reported_severity": 7, "progression": "getting_worse"})
        score = self.scorer.evaluate_case(structured)
        with self.assertRaises(PermissionError):
            self.repo.create_case("CF-2026-001", "pat-101", structured, score)

        # Grant consent & create case
        self.repo.record_consent("pat-101", 58, "hi-IN", True)
        created = self.repo.create_case("CF-2026-001", "pat-101", structured, score)
        self.assertEqual(created["status"], "awaiting_review")

        # Triage nurse cannot approve final clinical note (RBAC enforcement)
        with self.assertRaises(PermissionError):
            self.repo.approve_final_note("CF-2026-001", "staff-nurse-rao", "triage_nurse", "Approved for cardiology referral.")

        # Priority override requires reason
        with self.assertRaises(ValueError):
            self.repo.override_priority("CF-2026-001", "staff-dr-mehta", "clinician", "immediate_escalation", "")

        overridden = self.repo.override_priority(
            "CF-2026-001", "staff-dr-mehta", "clinician", "immediate_escalation", "Diaphoresis with left arm radiation requires immediate ECG."
        )
        self.assertEqual(overridden["priority_band"], "immediate_escalation")

        # Clinician approves final note
        approved = self.repo.approve_final_note(
            "CF-2026-001",
            "staff-dr-mehta",
            "clinician",
            "Urgent triage note approved: Direct patient to acute chest pain bay for 12-lead ECG and serial troponin.",
        )
        self.assertEqual(approved["status"], "approved_by_clinician")
        self.assertEqual(approved["approved_by"], "staff-dr-mehta")

        # Withdraw consent & verify audit trail
        withdrawal = self.repo.withdraw_consent_and_request_deletion("pat-101", "Patient requested data erasure")
        self.assertFalse(withdrawal["consent_granted"])
        audits = self.repo.get_audit_trail("CF-2026-001")
        event_types = {a["event_type"] for a in audits}
        self.assertIn("CASE_SUBMITTED", event_types)
        self.assertIn("PRIORITY_OVERRIDDEN", event_types)
        self.assertIn("FINAL_NOTE_APPROVED", event_types)

    def test_09_synthetic_slm_evaluation_harness(self):
        metrics = run_evaluation()
        self.assertEqual(metrics["schema_valid_rate"], 1.0)
        self.assertEqual(metrics["conflict_detection_accuracy"], 1.0)



    def test_10_fastapi_rbac_and_endpoints(self):
        from fastapi import HTTPException
        from backend.app.main import (
            provider_health,
            staff_login,
            StaffLoginRequest,
            require_staff_auth,
            get_prioritized_queue,
        )
        health = provider_health()
        self.assertIn("active_providers", health)
        # Unauthenticated queue access must fail with 401
        with self.assertRaises(HTTPException) as ctx:
            get_prioritized_queue(authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)
        # Valid staff login returns token
        login_res = staff_login(StaffLoginRequest(email="dr.mehta@careflow.demo", password="TriageDemo2026!"))
        token = login_res["access_token"]
        staff = require_staff_auth(f"Bearer {token}")
        self.assertEqual(staff["role"], "clinician")
        # Duplicate routine appointment check
        self.repo.record_consent("pat-202", 34, "en-IN", True)
        self.repo.create_appointment("APT-1", "pat-202", "CF-202", "general_medicine_intake", "2026-10-21T10:00:00Z")
        with self.assertRaises(ValueError):
            self.repo.create_appointment("APT-2", "pat-202", "CF-202", "general_medicine_intake", "2026-10-21T14:00:00Z")


if __name__ == "__main__":
    unittest.main()
