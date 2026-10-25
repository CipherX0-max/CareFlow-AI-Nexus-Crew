"""20 Mandatory Regression Tests for Source-Aware Extraction and Evidence Validation.
Verifies that:
1. Every extracted medical fact preserves what, who, who/what referent, provenance, assertion status, temporal status, verification.
2. Family history, questions, educational statements, suspected conditions, lab values are NEVER converted into confirmed personal diagnoses.
3. The exact thyroid report Family History scenario is completely resolved.
"""
from __future__ import annotations

import pytest

from ..models import Case, Fact, PatientProfile
from ..services import extraction as E, facts as F, lexicon as L, priority_scorer as PS, rules_engine as RE, summary as SM


@pytest.fixture()
def db_env(env):
    from ..main import init_database
    init_database()
    yield env


# ---------------------------------------------------------------------------
# 1. Exact thyroid report Family History scenario
# ---------------------------------------------------------------------------
def test_01_thyroid_report_family_history_cancer():
    text = "THYROID PROFILE REPORT\n\nFamily History of\nCancer: Yes\n"
    drafts = L.extract_all(text)
    cancer = next(d for d in drafts if d.concept == "cancer")
    assert cancer.subject == "family_member"
    assert cancer.relationship_to_patient == "unknown"
    assert cancer.assertion_status == "family-history-positive"
    assert cancer.section_heading == "Family History of"
    assert cancer.polarity == "present"


# ---------------------------------------------------------------------------
# 2. Free-text message: mother had thyroid cancer
# ---------------------------------------------------------------------------
def test_02_mother_had_thyroid_cancer():
    text = "My mother had thyroid cancer, but I have never had any cancer."
    drafts = L.extract_all(text)
    fam_cancer = next(d for d in drafts if d.concept == "cancer" and d.subject == "family_member")
    assert fam_cancer.relationship_to_patient == "mother"
    assert fam_cancer.assertion_status == "family-history-positive"

    pat_cancer = next((d for d in drafts if d.concept == "cancer" and d.subject == "patient"), None)
    if pat_cancer:
        assert pat_cancer.polarity == "denied"


# ---------------------------------------------------------------------------
# 3. Free-text message: father died of heart attack
# ---------------------------------------------------------------------------
def test_03_father_heart_attack():
    text = "Father died of heart attack at 50."
    drafts = L.extract_all(text)
    heart = next(d for d in drafts if d.concept == "heart_disease")
    assert heart.subject == "family_member"
    assert heart.relationship_to_patient == "father"
    assert heart.assertion_status == "family-history-positive"


# ---------------------------------------------------------------------------
# 4. Free-text message: sister has asthma
# ---------------------------------------------------------------------------
def test_04_sister_has_asthma():
    text = "My sister has asthma."
    drafts = L.extract_all(text)
    asthma = next(d for d in drafts if d.concept == "asthma_copd")
    assert asthma.subject == "family_member"
    assert asthma.relationship_to_patient == "sibling"
    assert asthma.assertion_status == "family-history-positive"


# ---------------------------------------------------------------------------
# 5. Patient personal historical resolved: childhood asthma
# ---------------------------------------------------------------------------
def test_05_childhood_asthma_resolved():
    text = "I had childhood asthma, resolved years ago. Currently no breathing issues."
    drafts = L.extract_all(text)
    asthma = next(d for d in drafts if d.concept == "asthma_copd")
    assert asthma.subject == "patient"
    assert asthma.temporal in ("past", "resolved")
    assert asthma.assertion_status in ("historical", "resolved")


# ---------------------------------------------------------------------------
# 6. Patient personal history in remission
# ---------------------------------------------------------------------------
def test_06_cancer_in_remission():
    text = "I had breast cancer in 2018, in remission."
    drafts = L.extract_all(text)
    cancer = next(d for d in drafts if d.concept == "cancer")
    assert cancer.subject == "patient"
    assert cancer.temporal in ("past", "resolved")
    assert cancer.assertion_status in ("historical", "resolved")
    assert cancer.event_date == "2018"


# ---------------------------------------------------------------------------
# 7. Document Differential Diagnosis: rule out pulmonary embolism
# ---------------------------------------------------------------------------
def test_07_differential_diagnosis_rule_out():
    text = "DIFFERENTIAL DIAGNOSIS:\nRule out pulmonary embolism. Query chest pain."
    drafts = L.extract_all(text)
    pe = next((d for d in drafts if "pulmonary" in d.concept or d.concept == "chest_pain"), None)
    assert pe is not None
    assert pe.subject == "patient"
    assert pe.assertion_status == "suspected"
    assert pe.polarity == "uncertain"


# ---------------------------------------------------------------------------
# 8. Document Impression: possible angina
# ---------------------------------------------------------------------------
def test_08_possible_angina():
    text = "Clinical Impression:\nPossible angina / chest discomfort."
    drafts = L.extract_all(text)
    cp = next(d for d in drafts if d.concept == "chest_pain")
    assert cp.assertion_status == "suspected"
    assert cp.polarity == "uncertain"


# ---------------------------------------------------------------------------
# 9. Educational information / disclaimer excluded from clinical observations
# ---------------------------------------------------------------------------
def test_09_educational_material_excluded():
    text = "Educational Information:\nCancer is a leading cause of weight loss and chronic illness in adults."
    drafts = L.extract_all(text)
    cancer_drafts = [d for d in drafts if d.concept == "cancer"]
    # Educational facts are marked educational and excluded by validate_fact_provenance
    for d in cancer_drafts:
        validated, rej = E.validate_fact_provenance(d, text)
        assert validated is None
        assert rej == "rejected_educational_or_disclaimer_content"


# ---------------------------------------------------------------------------
# 10. Patient diagnostic query: Could this be a heart attack?
# ---------------------------------------------------------------------------
def test_10_question_not_confirmed_diagnosis():
    text = "I have chest pain. Could this be a heart attack?"
    drafts = L.extract_all(text)
    cp = next(d for d in drafts if d.concept == "chest_pain")
    assert cp.polarity == "present"
    assert cp.assertion_status == "present"

    hd = next(d for d in drafts if d.concept == "heart_disease")
    assert hd.assertion_status == "questioned"
    assert hd.polarity == "uncertain"


# ---------------------------------------------------------------------------
# 11. Conditional warning instructions
# ---------------------------------------------------------------------------
def test_11_conditional_instructions():
    text = "Discharge Instructions: Return to clinic if you develop chest pain or severe bleeding."
    drafts = L.extract_all(text)
    for d in drafts:
        if d.concept in ("chest_pain", "severe_bleeding"):
            assert d.assertion_status == "conditional"
            assert d.polarity == "uncertain"


# ---------------------------------------------------------------------------
# 12. Clinical table formatting: Cancer: No, Hypertension: Yes
# ---------------------------------------------------------------------------
def test_12_table_format_personal_history():
    text = "Past Medical History:\nCancer: No\nHypertension: Yes\nDiabetes: No"
    drafts = L.extract_all(text)
    ca = next(d for d in drafts if d.concept == "cancer")
    ht = next(d for d in drafts if d.concept == "hypertension")
    db = next(d for d in drafts if d.concept == "diabetes")

    assert ca.polarity == "denied"
    assert ca.subject == "patient"

    assert ht.polarity == "present"
    assert ht.subject == "patient"

    assert db.polarity == "denied"
    assert db.subject == "patient"


# ---------------------------------------------------------------------------
# 13. Family history table formatting: Cancer: No, Diabetes: Yes
# ---------------------------------------------------------------------------
def test_13_family_history_table():
    text = "Family History of:\nCancer: No\nDiabetes: Yes"
    drafts = L.extract_all(text)
    ca = next(d for d in drafts if d.concept == "cancer")
    db = next(d for d in drafts if d.concept == "diabetes")

    assert ca.subject == "family_member"
    assert ca.assertion_status == "family-history-negative"
    assert ca.polarity == "denied"

    assert db.subject == "family_member"
    assert db.assertion_status == "family-history-positive"
    assert db.polarity == "present"


# ---------------------------------------------------------------------------
# 14. Contradiction engine: patient denies cancer, family history has cancer -> NO conflict
# ---------------------------------------------------------------------------
def test_14_reconciliation_no_false_conflict_on_family_history(db_env):
    from ..db import session_scope
    from ..models import Case, Fact
    from ..services import reconcile as R
    from .helpers import make_user

    uid = make_user("pat.cf14@demo-clinic.org", "patient")
    with session_scope() as db:
        case = Case(case_number="CF-TEST-014", patient_id=uid, language="en")
        db.add(case)
        db.flush()

        f_patient = Fact(
            case_id=case.id, category="history", concept="cancer", label="Cancer", polarity="denied",
            temporal="current", source_type="message", source_id="msg-1", excerpt="I do not have cancer",
            subject="patient", relationship_to_patient="patient", assertion_status="denied",
        )
        f_family = Fact(
            case_id=case.id, category="history", concept="cancer", label="Cancer", polarity="present",
            temporal="unknown", source_type="document", source_id="doc-1", excerpt="Family History of Cancer: Yes",
            subject="family_member", relationship_to_patient="unknown", assertion_status="family-history-positive",
            section_heading="Family History of",
        )
        db.add_all([f_patient, f_family])
        db.flush()

        ruleset = {"safety_critical_concepts": ["chest_pain", "cancer"]}
        conflicts = R.detect(db, case.id, ruleset)
        assert len(conflicts) == 0, f"Expected 0 conflicts, got: {[c.description for c in conflicts]}"


# ---------------------------------------------------------------------------
# 15. Contradiction engine: true contradiction on patient status IS flagged
# ---------------------------------------------------------------------------
def test_15_reconciliation_detects_true_personal_conflict(db_env):
    from ..db import session_scope
    from ..models import Case, Fact
    from ..services import reconcile as R
    from .helpers import make_user

    uid = make_user("pat.cf15@demo-clinic.org", "patient")
    with session_scope() as db:
        case = Case(case_number="CF-TEST-015", patient_id=uid, language="en")
        db.add(case)
        db.flush()

        f1 = Fact(
            case_id=case.id, category="symptom", concept="chest_pain", label="Chest pain", polarity="present",
            temporal="current", source_type="message", source_id="msg-1", excerpt="I have severe chest pain",
            subject="patient", relationship_to_patient="patient", assertion_status="present",
        )
        f2 = Fact(
            case_id=case.id, category="symptom", concept="chest_pain", label="Chest pain", polarity="denied",
            temporal="current", source_type="message", source_id="msg-2", excerpt="I do not have chest pain",
            subject="patient", relationship_to_patient="patient", assertion_status="denied",
        )
        db.add_all([f1, f2])
        db.flush()

        ruleset = {"safety_critical_concepts": ["chest_pain"]}
        conflicts = R.detect(db, case.id, ruleset)
        assert len(conflicts) == 1
        assert conflicts[0].concept == "chest_pain"


# ---------------------------------------------------------------------------
# 16. Priority scorer: family history of cancer does NOT add comorbidity points
# ---------------------------------------------------------------------------
def test_16_priority_scorer_ignores_family_history_for_personal_comorbidity():
    case = Case(id="case-16", case_number="CF-016", language="en", confirmed_queue=None, proposed_queue="NORMAL")
    fam_fact = Fact(
        id="f-fam", case_id=case.id, category="history", concept="cancer", label="Cancer", polarity="present",
        subject="family_member", relationship_to_patient="unknown", assertion_status="family-history-positive",
    )
    ruleset = {
        "safety_critical_concepts": [],
        "priority_scoring": {"weights": {"high_risk_comorbidity": 22.0}},
    }
    prio = PS.score_case(case, [fam_fact], [], None, ruleset)
    assert not any("Relevant comorbidity: Cancer" in f for f in prio.ranking_factors)
    assert prio.internal_score == 0.0 or "Family history noted: Cancer" in " ".join(prio.ranking_factors)


# ---------------------------------------------------------------------------
# 17. Priority scorer: personal confirmed cancer DOES receive comorbidity points
# ---------------------------------------------------------------------------
def test_17_priority_scorer_includes_personal_comorbidity():
    case = Case(id="case-17", case_number="CF-017", language="en", confirmed_queue=None, proposed_queue="NORMAL")
    personal_fact = Fact(
        id="f-pat", case_id=case.id, category="history", concept="cancer", label="Cancer", polarity="present",
        subject="patient", relationship_to_patient="patient", assertion_status="present",
    )
    ruleset = {
        "safety_critical_concepts": [],
        "priority_scoring": {"weights": {"high_risk_comorbidity": 22.0}},
    }
    prio = PS.score_case(case, [personal_fact], [], None, ruleset)
    assert any("Relevant comorbidity: Cancer" in f for f in prio.ranking_factors)


# ---------------------------------------------------------------------------
# 18. Patient case view / Understood: family history shown safely, not "you have Cancer"
# ---------------------------------------------------------------------------
def test_18_patient_case_view_understood_safe_framing(db_env):
    from ..db import session_scope
    from ..models import Case, Fact
    from ..routers import patient as P
    from .helpers import make_user

    uid = make_user("pat.cf18@demo-clinic.org", "patient")
    with session_scope() as db:
        case = Case(case_number="CF-TEST-018", patient_id=uid, language="en", status="draft")
        db.add(case)
        db.flush()

        fam_fact = Fact(
            case_id=case.id, category="history", concept="cancer", label="Cancer", polarity="present",
            subject="family_member", relationship_to_patient="mother", assertion_status="family-history-positive",
            section_heading="Family History of", excerpt="Mother had cancer", source_type="document", source_id="doc-1",
        )
        db.add(fam_fact)
        db.flush()

        view = P.patient_case_view(db, case)
        und = view["understood"]
        assert len(und) == 1
        item = und[0]
        assert "Cancer (mother)" in item["label"]
        assert item["polarity"] == "family_history_positive"
        assert item["subject"] == "family_member"


# ---------------------------------------------------------------------------
# 19. Ambiguous section heading routes case to NEEDS_REVIEW
# ---------------------------------------------------------------------------
def test_19_ambiguous_section_routes_to_needs_review():
    text = "??? History of\nCancer: Yes\n"
    drafts = L.extract_all(text)
    cancer = next(d for d in drafts if d.concept == "cancer")
    assert cancer.subject == "unknown"
    assert "subject_or_section_uncertain" in cancer.flags

    view = RE.FactView(
        id="f-amb", category="history", concept="cancer", polarity="uncertain", temporal="unknown",
        subject="unknown", relationship_to_patient="unknown", assertion_status="unknown",
        flags=["subject_or_section_uncertain"],
    )
    ctx = RE.CaseContext(facts=[view], conflicts=[], extraction_ok=True, has_any_input=True)
    ruleset = {
        "version": "1.0",
        "urgent_rules": [],
        "safety_critical_concepts": [],
        "normal_requires": {},
        "clinically_approved": False,
    }
    result = RE.evaluate(ctx, ruleset)
    assert result.queue == "NEEDS_REVIEW"
    assert any("ambiguous subject" in d["message"].lower() for d in result.data_quality)


# ---------------------------------------------------------------------------
# 20. MedGemma Claim-to-Evidence Validator catches personal attribution of family condition
# ---------------------------------------------------------------------------
def test_20_claim_to_evidence_validator_rejects_hallucinated_personal_diagnosis():
    # Narrative claims the patient has cancer, but cancer was only in family history
    narrative = "The patient has a history of cancer and presents with persistent fatigue."
    allowed_words = {"patient", "history", "cancer", "fatigue", "presents"}
    numbers = set()
    family_only = {"cancer"}
    denied = set()

    is_valid = SM.validate_summary_claims(narrative, allowed_words, numbers, family_only, denied)
    assert is_valid is False, "Claim validator must reject attributing family-only cancer to patient as personal history"

    safe_narrative = "Patient presents with fatigue. Family history of cancer is noted."
    is_safe_valid = SM.validate_summary_claims(safe_narrative, allowed_words | {"family"}, numbers, family_only, denied)
    assert is_safe_valid is True
