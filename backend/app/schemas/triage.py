"""Shared, strictly validated Pydantic schemas for CareFlow AI capabilities."""
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


ProvenanceSource = Literal[
    "patient_statement",
    "voice_transcript",
    "uploaded_report",
    "staff_entered",
    "model_interpretation",
]

PriorityBand = Literal[
    "immediate_escalation",
    "high_priority",
    "information_required",
    "standard_review",
]

ReviewStatus = Literal[
    "draft",
    "awaiting_review",
    "in_review",
    "needs_follow_up",
    "approved_by_clinician",
    "withdrawn_by_patient",
]


class SourceReference(BaseModel):
    source_type: ProvenanceSource
    source_id: str
    document_name: Optional[str] = None
    page_or_location: Optional[str] = None
    verbatim_excerpt: str
    provider_name: str
    is_synthetic_demo: bool = True


class ExtractedFact(BaseModel):
    fact_id: str
    category: Literal["symptom", "onset", "progression", "severity", "history", "medication", "vital_sign", "red_flag"]
    label: str
    value: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    uncertain: bool = False
    verified_by_staff: bool = False
    source_ref: SourceReference


class ConflictingStatement(BaseModel):
    conflict_id: str
    field_name: str
    description: str
    source_a: SourceReference
    value_a: str
    source_b: SourceReference
    value_b: str
    resolution_status: Literal["unresolved", "verified_a", "verified_b", "staff_custom"] = "unresolved"
    resolved_value: Optional[str] = None
    resolved_by: Optional[str] = None


class MissingInformationItem(BaseModel):
    field_key: str
    question_prompt: str
    clinical_relevance: str
    is_critical_for_warning_sign: bool = False
    answered_value: Optional[str] = None


class VitalMeasurement(BaseModel):
    name: str
    value: float
    unit: str
    timestamp: str
    source_type: ProvenanceSource
    plausible: bool = True
    plausibility_note: Optional[str] = None


class SpeechTranscriptionOutput(BaseModel):
    transcript: str
    detected_language: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    uncertainty_flags: List[str] = Field(default_factory=list)
    provider_used: str
    is_synthetic_demo: bool = True
    patient_edited_transcript: Optional[str] = None


class DocumentExtractionPageSpan(BaseModel):
    page_number: int
    location_hint: str
    extracted_text: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class DocumentExtractionOutput(BaseModel):
    document_id: str
    filename: str
    mime_type: str
    full_text: str
    pages: List[DocumentExtractionPageSpan] = Field(default_factory=list)
    extracted_facts: List[ExtractedFact] = Field(default_factory=list)
    unreadable_or_ambiguous_flags: List[str] = Field(default_factory=list)
    provider_used: str
    is_synthetic_demo: bool = True


class TranslationOutput(BaseModel):
    original_text: str
    translated_text: str
    source_language: str
    target_language: str
    preserved_negations_and_numbers: List[str] = Field(default_factory=list)
    ambiguity_flags: List[str] = Field(default_factory=list)
    provider_used: str
    is_synthetic_demo: bool = True


class StructuredCaseExtraction(BaseModel):
    primary_concern_verbatim: str
    extracted_symptoms: List[str]
    onset_and_duration: Optional[str] = None
    progression: Optional[Literal["getting_worse", "staying_same", "getting_better", "fluctuating", "unknown"]] = "unknown"
    patient_reported_severity: Optional[int] = Field(default=None, ge=1, le=10)
    functional_impact: Optional[str] = None
    pattern: Optional[Literal["constant", "intermittent", "unknown"]] = "unknown"
    associated_symptoms: List[str] = Field(default_factory=list)
    relevant_history: List[str] = Field(default_factory=list)
    reported_medications: List[str] = Field(default_factory=list)
    potential_warning_signs: List[str] = Field(default_factory=list)
    facts_with_provenance: List[ExtractedFact] = Field(default_factory=list)
    missing_information: List[MissingInformationItem] = Field(default_factory=list)
    conflicting_statements: List[ConflictingStatement] = Field(default_factory=list)
    uncertain_extractions: List[str] = Field(default_factory=list)
    suggested_follow_up_questions: List[str] = Field(default_factory=list)
    draft_clinician_summary: str
    provider_used: str
    model_identifier: str
    is_synthetic_demo: bool = True


class ScoringFactorContribution(BaseModel):
    dimension: Literal[
        "symptom_warning_signs",
        "time_and_progression",
        "severity_and_function",
        "patient_risk_factors",
        "objective_measurements",
        "information_uncertainty",
    ]
    label: str
    points_awarded: int
    max_dimension_points: int
    rule_id: str
    explanation: str
    is_uncertainty_adjustment: bool = False


class UrgencyScoreResult(BaseModel):
    internal_prototype_score: int = Field(ge=0, le=100)
    priority_band: PriorityBand
    critical_warning_override: bool = False
    critical_warning_reasons: List[str] = Field(default_factory=list)
    requires_verification: bool = False
    contributing_factors: List[ScoringFactorContribution] = Field(default_factory=list)
    rules_version: str = "v2.4.0-demo-unvalidated"
    calculated_at: str
    disclaimer: str = (
        "UNVALIDATED DEMO CONFIGURATION: This prototype urgency score is a workflow "
        "prioritization aid tested only on synthetic cases, not a clinical diagnosis or medical probability."
    )
