"""Deterministic, Explainable Urgency Scoring Engine for CareFlow AI.

Independent of all LLM/SLM providers. Evaluates 6 explicit dimensions:
1. Symptom warning signs (with separate Critical-Warning Override pathway)
2. Time & progression
3. Patient-reported severity & functional impact
4. Relevant patient risk factors
5. Available objective measurements (with plausibility checks)
6. Information uncertainty & conflict handling (never lowers score; triggers verification)
"""
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from backend.app.schemas.triage import (
    StructuredCaseExtraction,
    VitalMeasurement,
    UrgencyScoreResult,
    ScoringFactorContribution,
    PriorityBand,
)


@dataclass
class UrgencyRuleConfig:
    rules_version: str = "v2.4.0-demo-unvalidated"
    immediate_escalation_threshold: int = 75
    high_priority_threshold: int = 50
    # Configurable weights (max points per dimension)
    max_warning_sign_pts: int = 35
    max_time_progression_pts: int = 20
    max_severity_function_pts: int = 15
    max_risk_factor_pts: int = 10
    max_objective_vitals_pts: int = 10
    max_uncertainty_pts: int = 10
    # Uncertainty handling parameters
    critical_missing_info_adjustment: int = 6
    conflict_verification_adjustment: int = 5
    low_confidence_ocr_adjustment: int = 3
    critical_red_flag_keywords: List[str] = field(
        default_factory=lambda: [
            "chest pressure",
            "chest heaviness",
            "chest pain",
            "radiating to left arm",
            "shortness of breath",
            "breathless",
            "unresponsive",
            "slurred speech",
            "facial droop",
            "severe bleeding",
            "hemoptysis",
        ]
    )


class ExplainableUrgencyEngine:
    def __init__(self, config: Optional[UrgencyRuleConfig] = None):
        self.config = config or UrgencyRuleConfig()

    def evaluate_case(
        self,
        extraction: StructuredCaseExtraction,
        patient_age: Optional[int] = None,
        vitals: Optional[List[VitalMeasurement]] = None,
        translation_ambiguities: Optional[List[str]] = None,
    ) -> UrgencyScoreResult:
        contributions: List[ScoringFactorContribution] = []
        critical_reasons: List[str] = []
        requires_verification = False

        # ---------------------------------------------------------------------
        # 1. SYMPTOM WARNING SIGNS & CRITICAL OVERRIDE PATHWAY
        # ---------------------------------------------------------------------
        warning_pts = 0
        combined_text = (
            extraction.primary_concern_verbatim
            + " "
            + " ".join(extraction.extracted_symptoms)
            + " "
            + " ".join(extraction.potential_warning_signs)
        ).lower()

        matched_red_flags = [
            kw for kw in self.config.critical_red_flag_keywords if kw in combined_text
        ]
        if len(extraction.potential_warning_signs) >= 2 or len(matched_red_flags) >= 2:
            warning_pts = self.config.max_warning_sign_pts
            reason = (
                f"Multiple potential red-flag warning signs detected: "
                f"{', '.join(extraction.potential_warning_signs or matched_red_flags)}"
            )
            critical_reasons.append(reason)
            contributions.append(
                ScoringFactorContribution(
                    dimension="symptom_warning_signs",
                    label="Compound Red-Flag Symptom Cluster",
                    points_awarded=warning_pts,
                    max_dimension_points=self.config.max_warning_sign_pts,
                    rule_id="RULE-WARN-001",
                    explanation=reason,
                )
            )
        elif len(extraction.potential_warning_signs) == 1 or len(matched_red_flags) == 1:
            warning_pts = 24
            flag_str = (extraction.potential_warning_signs or matched_red_flags)[0]
            critical_reasons.append(f"Potential critical warning sign flagged for review: {flag_str}")
            contributions.append(
                ScoringFactorContribution(
                    dimension="symptom_warning_signs",
                    label="Single Warning Sign Flagged",
                    points_awarded=warning_pts,
                    max_dimension_points=self.config.max_warning_sign_pts,
                    rule_id="RULE-WARN-002",
                    explanation=f"Reported symptom requires prompt escalation screening: {flag_str}",
                )
            )
        else:
            warning_pts = 5 if extraction.extracted_symptoms else 0
            contributions.append(
                ScoringFactorContribution(
                    dimension="symptom_warning_signs",
                    label="Baseline Reported Symptoms",
                    points_awarded=warning_pts,
                    max_dimension_points=self.config.max_warning_sign_pts,
                    rule_id="RULE-WARN-003",
                    explanation="Symptoms recorded without immediate red-flag cluster.",
                )
            )

        # ---------------------------------------------------------------------
        # 2. TIME AND PROGRESSION
        # ---------------------------------------------------------------------
        time_pts = 0
        time_explanations = []
        if extraction.progression == "getting_worse":
            time_pts += 12
            time_explanations.append("Patient reports symptoms are actively worsening (+12)")
        elif extraction.progression == "fluctuating":
            time_pts += 6
            time_explanations.append("Fluctuating symptom course (+6)")

        if extraction.onset_and_duration and any(
            k in extraction.onset_and_duration.lower() for k in ["acute", "last night", "hour", "today", "morning", "12"]
        ):
            time_pts += 8
            time_explanations.append("Acute onset window (<24h) (+8)")

        time_pts = min(time_pts, self.config.max_time_progression_pts)
        contributions.append(
            ScoringFactorContribution(
                dimension="time_and_progression",
                label="Onset & Symptom Trajectory",
                points_awarded=time_pts,
                max_dimension_points=self.config.max_time_progression_pts,
                rule_id="RULE-TIME-001",
                explanation="; ".join(time_explanations) if time_explanations else "No rapid progression or acute (<24h) onset confirmed.",
            )
        )

        # ---------------------------------------------------------------------
        # 3. PATIENT-REPORTED SEVERITY & FUNCTIONAL IMPACT
        # ---------------------------------------------------------------------
        sev_pts = 0
        sev_notes = []
        if extraction.patient_reported_severity is not None:
            if extraction.patient_reported_severity >= 8:
                sev_pts += 10
                sev_notes.append(f"High self-reported severity ({extraction.patient_reported_severity}/10) (+10)")
            elif extraction.patient_reported_severity >= 5:
                sev_pts += 6
                sev_notes.append(f"Moderate self-reported severity ({extraction.patient_reported_severity}/10) (+6)")
            else:
                sev_pts += 2
                sev_notes.append(f"Mild self-reported severity ({extraction.patient_reported_severity}/10) (+2)")

        if extraction.functional_impact and any(
            w in extraction.functional_impact.lower() for w in ["unable", "cannot", "stairs", "sleep", "walk", "breathless"]
        ):
            sev_pts += 5
            sev_notes.append(f"Functional interference reported ({extraction.functional_impact}) (+5)")

        sev_pts = min(sev_pts, self.config.max_severity_function_pts)
        contributions.append(
            ScoringFactorContribution(
                dimension="severity_and_function",
                label="Patient Severity & Functional Impact",
                points_awarded=sev_pts,
                max_dimension_points=self.config.max_severity_function_pts,
                rule_id="RULE-SEV-001",
                explanation="; ".join(sev_notes) if sev_notes else "No severe functional impairment reported.",
            )
        )

        # ---------------------------------------------------------------------
        # 4. RELEVANT PATIENT RISK FACTORS
        # ---------------------------------------------------------------------
        risk_pts = 0
        risk_notes = []
        if patient_age is not None and (patient_age >= 60 or patient_age <= 5):
            risk_pts += 5
            risk_notes.append(f"Age-related clinical review modifier (Age: {patient_age}) (+5)")
        if extraction.relevant_history:
            risk_pts += 5
            risk_notes.append(f"Reported history ({', '.join(extraction.relevant_history[:3])}) (+5)")

        risk_pts = min(risk_pts, self.config.max_risk_factor_pts)
        contributions.append(
            ScoringFactorContribution(
                dimension="patient_risk_factors",
                label="Reported History & Demographic Modifiers",
                points_awarded=risk_pts,
                max_dimension_points=self.config.max_risk_factor_pts,
                rule_id="RULE-RISK-001",
                explanation="; ".join(risk_notes) if risk_notes else "No additional high-risk history reported.",
            )
        )

        # ---------------------------------------------------------------------
        # 5. OBJECTIVE MEASUREMENTS & VITALS (WITH PLAUSIBILITY CHECK)
        # ---------------------------------------------------------------------
        vital_pts = 0
        vital_notes = []
        for v in vitals or []:
            if v.name.lower() == "spo2":
                if v.value < 30 or v.value > 100:
                    v.plausible = False
                    requires_verification = True
                    vital_notes.append(f"Implausible SpO2 value ({v.value}{v.unit}) flagged for staff verification")
                elif v.value < 94:
                    vital_pts += 8
                    vital_notes.append(f"Low SpO2 ({v.value}%) from {v.source_type} (+8)")
                    critical_reasons.append(f"Objective hypoxia threshold triggered (SpO2 {v.value}%)")
            elif v.name.lower() in ("systolic_bp", "bp_systolic"):
                if v.value < 40 or v.value > 300:
                    v.plausible = False
                    requires_verification = True
                    vital_notes.append(f"Implausible Systolic BP ({v.value} mmHg) flagged for verification")
                elif v.value >= 160 or v.value < 90:
                    vital_pts += 6
                    vital_notes.append(f"Elevated/Abnormal Systolic BP ({v.value} mmHg) (+6)")

        vital_pts = min(vital_pts, self.config.max_objective_vitals_pts)
        contributions.append(
            ScoringFactorContribution(
                dimension="objective_measurements",
                label="Available Objective Vitals & Report Measurements",
                points_awarded=vital_pts,
                max_dimension_points=self.config.max_objective_vitals_pts,
                rule_id="RULE-OBJ-001",
                explanation="; ".join(vital_notes) if vital_notes else "No abnormal verified vitals attached.",
            )
        )

        # ---------------------------------------------------------------------
        # 6. INFORMATION UNCERTAINTY & CONFLICT VERIFICATION
        #    (Never lowers urgency score; applies bounded verification adjustment)
        # ---------------------------------------------------------------------
        unc_pts = 0
        unc_notes = []
        critical_missing = [m for m in extraction.missing_information if m.is_critical_for_warning_sign and not m.answered_value]
        if critical_missing:
            unc_pts += self.config.critical_missing_info_adjustment
            requires_verification = True
            unc_notes.append(
                f"{len(critical_missing)} critical warning-sign question(s) unanswered (+{self.config.critical_missing_info_adjustment} verification adjustment)"
            )

        unresolved_conflicts = [c for c in extraction.conflicting_statements if c.resolution_status == "unresolved"]
        if unresolved_conflicts:
            unc_pts += self.config.conflict_verification_adjustment
            requires_verification = True
            unc_notes.append(
                f"{len(unresolved_conflicts)} unresolved cross-source conflict(s) detected (+{self.config.conflict_verification_adjustment} verification adjustment)"
            )

        if extraction.uncertain_extractions or translation_ambiguities:
            unc_pts += self.config.low_confidence_ocr_adjustment
            requires_verification = True
            unc_notes.append(
                f"Low-confidence OCR or translation ambiguity present (+{self.config.low_confidence_ocr_adjustment})"
            )

        unc_pts = min(unc_pts, self.config.max_uncertainty_pts)
        contributions.append(
            ScoringFactorContribution(
                dimension="information_uncertainty",
                label="Uncertainty, Conflicts & Missing Critical Info",
                points_awarded=unc_pts,
                max_dimension_points=self.config.max_uncertainty_pts,
                rule_id="RULE-UNC-001",
                explanation="; ".join(unc_notes) if unc_notes else "No critical conflicts or missing red-flag answers.",
                is_uncertainty_adjustment=True,
            )
        )

        total_score = min(
            100,
            warning_pts + time_pts + sev_pts + risk_pts + vital_pts + unc_pts,
        )
        critical_override = len(critical_reasons) > 0

        # Priority Band Assignment
        if critical_override or total_score >= self.config.immediate_escalation_threshold:
            band: PriorityBand = "immediate_escalation"
        elif total_score >= self.config.high_priority_threshold:
            band = "high_priority"
        elif requires_verification:
            # Missing critical info or conflicts NEVER silently enter the routine standard queue
            band = "information_required"
        else:
            band = "standard_review"

        return UrgencyScoreResult(
            internal_prototype_score=total_score,
            priority_band=band,
            critical_warning_override=critical_override,
            critical_warning_reasons=critical_reasons,
            requires_verification=requires_verification,
            contributing_factors=contributions,
            rules_version=self.config.rules_version,
            calculated_at=datetime.now(timezone.utc).isoformat(),
        )


def sort_cases_for_dashboard(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sorts cases deterministically by:
    1. Approved critical-warning escalation
    2. Priority band rank & urgency score
    3. Time waiting for review (older submissions prioritized via transparent aging policy)
    """
    band_rank = {
        "immediate_escalation": 4,
        "high_priority": 3,
        "information_required": 2,
        "standard_review": 1,
    }
    return sorted(
        cases,
        key=lambda c: (
            1 if c.get("critical_warning_override") else 0,
            band_rank.get(c.get("priority_band", "standard_review"), 0),
            c.get("urgency_score", 0),
            c.get("waiting_minutes", 0),
        ),
        reverse=True,
    )
