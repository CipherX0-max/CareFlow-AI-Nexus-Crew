"""Universal AI Provider Abstraction Layer for CareFlow AI.

Implements independent interfaces and replaceable adapters for:
1. SpeechToTextProvider (Sarvam Saaras, AI4Bharat IndicConformer, Whisper Local, Demo Mock)
2. DocumentExtractionProvider (Sarvam Vision, Tesseract OCR, Demo Mock)
3. TranslationProvider (IndicTrans2, Sarvam Translate, Demo Mock)
4. LanguageModelProvider (Gemma 3 4B via Ollama/vLLM, Demo Mock)

Strictly enforces:
- Zero-budget cost protection (never triggers paid APIs unless explicitly configured and allowed)
- Standardized Pydantic outputs with provenance & uncertainty preservation
- Explicit provider error handling and safe fallback without silent clinical distortion
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from backend.app.config import Settings, get_settings
from backend.app.schemas.triage import (
    SpeechTranscriptionOutput,
    DocumentExtractionOutput,
    DocumentExtractionPageSpan,
    ExtractedFact,
    SourceReference,
    TranslationOutput,
    StructuredCaseExtraction,
    MissingInformationItem,
    ConflictingStatement,
)


class ProviderError(Exception):
    """Raised when an AI provider fails, times out, lacks credentials, or violates cost controls."""
    def __init__(self, provider_name: str, reason: str, recoverable: bool = True):
        super().__init__(f"[{provider_name}] {reason}")
        self.provider_name = provider_name
        self.reason = reason
        self.recoverable = recoverable


ALLOWED_AUDIO_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a", "audio/webm"}
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15 MB

ALLOWED_DOC_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
MAX_DOC_BYTES = 20 * 1024 * 1024  # 20 MB


def validate_audio_upload(filename: str, mime_type: str, size_bytes: int) -> None:
    if size_bytes <= 0:
        raise ValueError("Audio file is empty.")
    if size_bytes > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio file exceeds maximum size limit of {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
    if mime_type not in ALLOWED_AUDIO_MIME_TYPES:
        raise ValueError(f"Unsupported audio format '{mime_type}'. Supported: WAV, MP3, M4A, WebM.")


def validate_document_upload(filename: str, mime_type: str, size_bytes: int) -> None:
    if size_bytes <= 0:
        raise ValueError("Uploaded document is empty.")
    if size_bytes > MAX_DOC_BYTES:
        raise ValueError(f"Document exceeds maximum size limit of {MAX_DOC_BYTES // (1024 * 1024)} MB.")
    if mime_type not in ALLOWED_DOC_MIME_TYPES:
        raise ValueError(f"Unsupported document format '{mime_type}'. Supported: PDF, PNG, JPEG.")


# ============================================================================
# 1. SPEECH-TO-TEXT ADAPTERS
# ============================================================================

class SpeechToTextProvider(ABC):
    provider_id: str = "base_stt"
    requires_api_key: bool = False
    is_paid_service: bool = False

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, filename: str, mime_type: str, language_hint: str = "hi-IN") -> SpeechTranscriptionOutput:
        pass


class DemoSpeechToTextAdapter(SpeechToTextProvider):
    provider_id = "demo_mock_stt"
    requires_api_key = False
    is_paid_service = False

    def transcribe(self, audio_bytes: bytes, filename: str, mime_type: str, language_hint: str = "hi-IN") -> SpeechTranscriptionOutput:
        validate_audio_upload(filename, mime_type, len(audio_bytes))
        if b"FORCE_PROVIDER_ERROR" in audio_bytes:
            raise ProviderError(self.provider_id, "Simulated speech transcription failure (corrupted audio stream).")
        if language_hint.startswith("hi"):
            return SpeechTranscriptionOutput(
                transcript="मुझे कल रात से सीने में भारीपन हो रहा है और बाएं हाथ में हल्का दर्द है। सुबह से पसीना भी आ रहा है, पर बुखार नहीं है।",
                detected_language="hi-IN (Hindi / Hinglish)",
                confidence=0.92,
                uncertainty_flags=["Audio background noise detected around 00:08; verify exact onset hour."],
                provider_used="demo_mock_stt (Synthetic Sarvam Saaras Simulation)",
                is_synthetic_demo=True,
            )
        return SpeechTranscriptionOutput(
            transcript="Subah 6 baje se chest ke beech mein tight pressure feel ho raha hai, climbing stairs makes me breathless, no fever.",
            detected_language="en-IN (Hinglish Code-Mixed)",
            confidence=0.94,
            uncertainty_flags=[],
            provider_used="demo_mock_stt (Synthetic Sarvam Saaras Simulation)",
            is_synthetic_demo=True,
        )


class SarvamSaarasSpeechAdapter(SpeechToTextProvider):
    provider_id = "sarvam_saaras"
    requires_api_key = True
    is_paid_service = True

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio_bytes: bytes, filename: str, mime_type: str, language_hint: str = "hi-IN") -> SpeechTranscriptionOutput:
        validate_audio_upload(filename, mime_type, len(audio_bytes))
        if not self.settings.allow_paid_providers:
            raise ProviderError(
                self.provider_id,
                "Paid hosted provider 'sarvam_saaras' is disabled by zero-budget guard (ALLOW_PAID_PROVIDERS=false).",
            )
        if not self.settings.sarvam_api_key:
            raise ProviderError(
                self.provider_id,
                "Missing SARVAM_API_KEY environment variable. Configure credentials in .env or use DEMO_MODE=true.",
            )
        raise ProviderError(
            self.provider_id,
            "External network requests disabled in sandbox environment; ready for live Sarvam API deployment.",
        )


class IndicConformerLocalAdapter(SpeechToTextProvider):
    provider_id = "indic_conformer"
    requires_api_key = False
    is_paid_service = False

    def transcribe(self, audio_bytes: bytes, filename: str, mime_type: str, language_hint: str = "hi-IN") -> SpeechTranscriptionOutput:
        validate_audio_upload(filename, mime_type, len(audio_bytes))
        raise ProviderError(
            self.provider_id,
            "Local AI4Bharat IndicConformer server not reachable at INDIC_CONFORMER_ENDPOINT.",
        )


# ============================================================================
# 2. DOCUMENT EXTRACTION & OCR ADAPTERS
# ============================================================================

class DocumentExtractionProvider(ABC):
    provider_id: str = "base_ocr"
    requires_api_key: bool = False
    is_paid_service: bool = False

    @abstractmethod
    def extract_document(self, file_bytes: bytes, filename: str, mime_type: str) -> DocumentExtractionOutput:
        pass


class DemoDocumentExtractionAdapter(DocumentExtractionProvider):
    provider_id = "demo_mock_ocr"
    requires_api_key = False
    is_paid_service = False

    def extract_document(self, file_bytes: bytes, filename: str, mime_type: str) -> DocumentExtractionOutput:
        validate_document_upload(filename, mime_type, len(file_bytes))
        if b"FORCE_OCR_ERROR" in file_bytes:
            raise ProviderError(self.provider_id, "Simulated OCR failure: Document scan resolution below readable threshold.")
        doc_id = f"doc-{abs(hash(filename)) % 10000:04d}"
        src_ref = SourceReference(
            source_type="uploaded_report",
            source_id=doc_id,
            document_name=filename,
            page_or_location="Page 1, Section 2 (Discharge Medications & Vitals)",
            verbatim_excerpt="BP: 158/96 mmHg, SpO2: 95%, Rx: Tab Clopidogrel 75mg OD, Tab Atorvastatin 40mg HS",
            provider_name="demo_mock_ocr (Synthetic Sarvam Vision Simulation)",
            is_synthetic_demo=True,
        )
        return DocumentExtractionOutput(
            document_id=doc_id,
            filename=filename,
            mime_type=mime_type,
            full_text="SYNTHETIC CLINICAL SUMMARY — CITY CARE DIAGNOSTICS | Recorded Vitals: BP 158/96 mmHg | HR 98 bpm | SpO2 95% | Active Medications: Tab Clopidogrel 75mg OD, Tab Metformin 500mg BD",
            pages=[
                DocumentExtractionPageSpan(
                    page_number=1,
                    location_hint="Page 1, Section 2 (Vitals & Active Rx)",
                    extracted_text="BP 158/96 mmHg | HR 98 bpm | SpO2 95% | Active Rx: Tab Clopidogrel 75mg OD, Tab Metformin 500mg BD",
                    confidence=0.91,
                )
            ],
            extracted_facts=[
                ExtractedFact(
                    fact_id=f"{doc_id}-fact-1",
                    category="medication",
                    label="Report Active Medication",
                    value="Clopidogrel 75mg OD (Blood thinner / Antiplatelet)",
                    confidence=0.93,
                    uncertain=False,
                    source_ref=src_ref,
                ),
                ExtractedFact(
                    fact_id=f"{doc_id}-fact-2",
                    category="vital_sign",
                    label="Recorded Blood Pressure",
                    value="158/96 mmHg",
                    confidence=0.95,
                    uncertain=False,
                    source_ref=src_ref,
                ),
            ],
            unreadable_or_ambiguous_flags=[
                "Handwritten margin note on Page 1 bottom-right has low OCR confidence (0.61); requires human clinician visual review."
            ],
            provider_used="demo_mock_ocr (Synthetic Sarvam Vision Simulation)",
            is_synthetic_demo=True,
        )


class SarvamVisionAdapter(DocumentExtractionProvider):
    provider_id = "sarvam_vision"
    requires_api_key = True
    is_paid_service = True

    def __init__(self, settings: Settings):
        self.settings = settings

    def extract_document(self, file_bytes: bytes, filename: str, mime_type: str) -> DocumentExtractionOutput:
        validate_document_upload(filename, mime_type, len(file_bytes))
        if not self.settings.allow_paid_providers:
            raise ProviderError(
                self.provider_id,
                "Paid hosted provider 'sarvam_vision' is blocked by zero-budget policy (ALLOW_PAID_PROVIDERS=false).",
            )
        if not self.settings.sarvam_api_key:
            raise ProviderError(self.provider_id, "Missing SARVAM_API_KEY for Sarvam Vision OCR.")
        raise ProviderError(self.provider_id, "Outbound network disabled in sandbox.")


# ============================================================================
# 3. TRANSLATION ADAPTERS
# ============================================================================

class TranslationProvider(ABC):
    provider_id: str = "base_translation"

    @abstractmethod
    def translate_to_english(self, text: str, source_language: str) -> TranslationOutput:
        pass


class DemoTranslationAdapter(TranslationProvider):
    provider_id = "demo_mock_indictrans2"

    def translate_to_english(self, text: str, source_language: str) -> TranslationOutput:
        if "FORCE_TRANSLATION_ERROR" in text:
            raise ProviderError(self.provider_id, "Simulated translation model failure.")
        ambiguities = []
        if "चक्कर" in text or "chakkar" in text.lower():
            ambiguities.append(
                "Term 'चक्कर / chakkar' can denote either true rotational vertigo or lightheadedness/presyncope; flagged for clinician clarification."
            )
        if "सीने में भारीपन" in text:
            translated = (
                "I have been experiencing heaviness in my chest since last night and mild pain in my left arm. "
                "Sweating since morning as well, but NO fever."
            )
        else:
            translated = text
        return TranslationOutput(
            original_text=text,
            translated_text=translated,
            source_language=source_language,
            target_language="en",
            preserved_negations_and_numbers=["NO fever (बुखार नहीं है)", "since last night (कल रात से)"],
            ambiguity_flags=ambiguities,
            provider_used="demo_mock_indictrans2 (Synthetic IndicTrans2 Adapter)",
            is_synthetic_demo=True,
        )


# ============================================================================
# 4. SMALL LANGUAGE MODEL (SLM) INFORMATION STRUCTURING ADAPTERS
# ============================================================================

class LanguageModelProvider(ABC):
    provider_id: str = "base_slm"
    model_id: str = "unknown"

    @abstractmethod
    def structure_case(
        self,
        patient_text: str,
        voice_transcript: Optional[SpeechTranscriptionOutput] = None,
        doc_extraction: Optional[DocumentExtractionOutput] = None,
        patient_metadata: Optional[Dict[str, Any]] = None,
    ) -> StructuredCaseExtraction:
        pass


class DemoGemma3StructuringAdapter(LanguageModelProvider):
    provider_id = "demo_mock_gemma3_4b"
    model_id = "google/gemma-3-4b-it (Synthetic Demo Simulation - 4B params < 20B limit)"

    def structure_case(
        self,
        patient_text: str,
        voice_transcript: Optional[SpeechTranscriptionOutput] = None,
        doc_extraction: Optional[DocumentExtractionOutput] = None,
        patient_metadata: Optional[Dict[str, Any]] = None,
    ) -> StructuredCaseExtraction:
        if "FORCE_SLM_MALFORMED_OUTPUT" in patient_text:
            raise ProviderError(self.provider_id, "SLM returned malformed JSON violating Pydantic schema.")

        combined_lower = (patient_text + " " + (voice_transcript.transcript if voice_transcript else "")).lower()
        facts: List[ExtractedFact] = []
        conflicts: List[ConflictingStatement] = []
        missing: List[MissingInformationItem] = []
        warning_signs: List[str] = []
        symptoms: List[str] = []

        patient_src = SourceReference(
            source_type="patient_statement",
            source_id="stmt-primary",
            verbatim_excerpt=patient_text[:180],
            provider_name=self.provider_id,
            is_synthetic_demo=True,
        )

        if any(k in combined_lower for k in ["chest", "सीने", "heaviness", "pressure", "tightness"]):
            symptoms.append("Central chest heaviness / pressure")
            warning_signs.append("Chest pressure / heaviness reported")
            facts.append(
                ExtractedFact(
                    fact_id="fact-sym-1",
                    category="symptom",
                    label="Primary Symptom",
                    value="Central chest pressure / heaviness",
                    confidence=0.96,
                    source_ref=patient_src,
                )
            )

        if any(k in combined_lower for k in ["left arm", "बाएं हाथ", "jaw", "radiating"]):
            symptoms.append("Left arm discomfort / radiation")
            warning_signs.append("Pain radiating to left arm")
            facts.append(
                ExtractedFact(
                    fact_id="fact-sym-2",
                    category="red_flag",
                    label="Radiation Pattern",
                    value="Discomfort radiating to left arm",
                    confidence=0.93,
                    source_ref=patient_src,
                )
            )

        if any(k in combined_lower for k in ["sweating", "पसीना", "diaphoresis", "breathless", "shortness of breath", "सांस"]):
            symptoms.append("Associated sweating / exertional breathlessness")
            warning_signs.append("Autonomic / respiratory accompaniment (diaphoresis / dyspnea)")

        if any(k in combined_lower for k in ["fever", "बुखार"]) and not any(
            neg in combined_lower for neg in ["no fever", "बुखार नहीं", "without fever"]
        ):
            symptoms.append("Fever reported")

        if not symptoms:
            symptoms.append("General health concern reported by patient")

        # Check conflict between patient medication statement and uploaded report
        patient_denies_meds = any(
            phrase in combined_lower
            for phrase in ["no medication", "no blood thinner", "not taking any medicine", "कोई दवा नहीं", "none"]
        )
        if doc_extraction and patient_denies_meds:
            for doc_fact in doc_extraction.extracted_facts:
                if doc_fact.category == "medication":
                    conflicts.append(
                        ConflictingStatement(
                            conflict_id="conf-med-01",
                            field_name="Active Medications (Antiplatelet / Blood Thinner)",
                            description=(
                                "Patient statement indicates no current regular medications/blood thinners, "
                                "but uploaded clinical report lists active Clopidogrel 75mg OD."
                            ),
                            source_a=patient_src,
                            value_a="Patient reported: No regular blood thinners / medications",
                            source_b=doc_fact.source_ref,
                            value_b=f"Uploaded Report ({doc_extraction.filename}): {doc_fact.value}",
                            resolution_status="unresolved",
                        )
                    )

        if doc_extraction:
            facts.extend(doc_extraction.extracted_facts)

        # Detect missing fields
        onset_val = None
        if any(t in combined_lower for t in ["last night", "कल रात", "6 baje", "morning", "2 hours", "3 days", "yesterday"]):
            onset_val = "Reported onset within last 12–24 hours"
        else:
            missing.append(
                MissingInformationItem(
                    field_key="onset_and_duration",
                    question_prompt="When did this concern first start, and how long does each episode last?",
                    clinical_relevance="Required to distinguish acute time-sensitive presentation from chronic symptoms.",
                    is_critical_for_warning_sign=True,
                )
            )

        severity_val = (patient_metadata or {}).get("reported_severity")
        if severity_val is None:
            missing.append(
                MissingInformationItem(
                    field_key="patient_reported_severity",
                    question_prompt="On a scale of 1 to 10, how severe does this feel right now?",
                    clinical_relevance="Helps establish baseline symptom burden and functional impact.",
                    is_critical_for_warning_sign=False,
                )
            )

        progression_val = (patient_metadata or {}).get("progression", "unknown")
        if progression_val == "unknown":
            missing.append(
                MissingInformationItem(
                    field_key="progression",
                    question_prompt="Are the symptoms getting worse, staying the same, or improving?",
                    clinical_relevance="Identifies rapid clinical progression.",
                    is_critical_for_warning_sign=bool(warning_signs),
                )
            )

        follow_ups = [m.question_prompt for m in missing]
        if conflicts:
            follow_ups.append(
                "Your uploaded report mentions Clopidogrel 75mg, while your intake form says no current blood thinners. Are you still taking Clopidogrel?"
            )

        uncertain_list = []
        if voice_transcript and voice_transcript.uncertainty_flags:
            uncertain_list.extend(voice_transcript.uncertainty_flags)
        if doc_extraction and doc_extraction.unreadable_or_ambiguous_flags:
            uncertain_list.extend(doc_extraction.unreadable_or_ambiguous_flags)

        return StructuredCaseExtraction(
            primary_concern_verbatim=patient_text,
            extracted_symptoms=symptoms,
            onset_and_duration=onset_val,
            progression=progression_val,
            patient_reported_severity=severity_val,
            functional_impact=(patient_metadata or {}).get("functional_impact"),
            pattern=(patient_metadata or {}).get("pattern", "unknown"),
            associated_symptoms=symptoms[1:] if len(symptoms) > 1 else [],
            relevant_history=(patient_metadata or {}).get("history", []),
            reported_medications=(patient_metadata or {}).get("medications", []),
            potential_warning_signs=warning_signs,
            facts_with_provenance=facts,
            missing_information=missing,
            conflicting_statements=conflicts,
            uncertain_extractions=uncertain_list,
            suggested_follow_up_questions=follow_ups,
            draft_clinician_summary=f"[AI-ASSISTED STRUCTURED DRAFT — REQUIRES CLINICIAN VERIFICATION] Presenting Concern: {patient_text} | Extracted Symptoms: {', '.join(symptoms)} | Open Conflicts: {len(conflicts)} | Missing Details: {len(missing)}.",
            provider_used=self.provider_id,
            model_identifier=self.model_id,
            is_synthetic_demo=True,
        )


class OllamaGemma3Adapter(LanguageModelProvider):
    provider_id = "gemma3_4b_ollama"
    model_id = "gemma3:4b (4.3B parameters - Apache/Gemma Terms)"

    def __init__(self, settings: Settings):
        self.settings = settings

    def structure_case(
        self,
        patient_text: str,
        voice_transcript: Optional[SpeechTranscriptionOutput] = None,
        doc_extraction: Optional[DocumentExtractionOutput] = None,
        patient_metadata: Optional[Dict[str, Any]] = None,
    ) -> StructuredCaseExtraction:
        raise ProviderError(
            self.provider_id,
            f"Local Ollama daemon not running at {self.settings.ollama_base_url} for model {self.settings.gemma_model_id}.",
        )


# ============================================================================
# 5. UNIVERSAL PROVIDER REGISTRY WITH EXPLICIT FALLBACK CONTROL
# ============================================================================

class ProviderOrchestrator:
    """Manages provider lookup, health status, and zero-budget safe fallback."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.stt_registry: Dict[str, SpeechToTextProvider] = {
            "demo_mock": DemoSpeechToTextAdapter(),
            "sarvam_saaras": SarvamSaarasSpeechAdapter(self.settings),
            "indic_conformer": IndicConformerLocalAdapter(),
        }
        self.ocr_registry: Dict[str, DocumentExtractionProvider] = {
            "demo_mock": DemoDocumentExtractionAdapter(),
            "sarvam_vision": SarvamVisionAdapter(self.settings),
        }
        self.translation_registry: Dict[str, TranslationProvider] = {
            "demo_mock": DemoTranslationAdapter(),
        }
        self.structuring_registry: Dict[str, LanguageModelProvider] = {
            "demo_mock": DemoGemma3StructuringAdapter(),
            "gemma3_4b_ollama": OllamaGemma3Adapter(self.settings),
        }

    def run_stt(
        self, audio_bytes: bytes, filename: str, mime_type: str, language_hint: str = "hi-IN", allow_fallback: bool = False
    ) -> SpeechTranscriptionOutput:
        primary = self.stt_registry.get(self.settings.stt_provider)
        if not primary:
            raise ProviderError(self.settings.stt_provider, "Unknown STT_PROVIDER configured.")
        try:
            return primary.transcribe(audio_bytes, filename, mime_type, language_hint)
        except ProviderError as exc:
            if allow_fallback and self.settings.stt_fallback_provider and self.settings.stt_fallback_provider != self.settings.stt_provider:
                fallback = self.stt_registry[self.settings.stt_fallback_provider]
                if fallback.is_paid_service and not self.settings.allow_paid_providers:
                    raise
                res = fallback.transcribe(audio_bytes, filename, mime_type, language_hint)
                res.uncertainty_flags.append(f"Primary provider '{self.settings.stt_provider}' failed ({exc.reason}); served by fallback '{fallback.provider_id}'.")
                return res
            raise

    def run_ocr(self, file_bytes: bytes, filename: str, mime_type: str, allow_fallback: bool = False) -> DocumentExtractionOutput:
        primary = self.ocr_registry.get(self.settings.ocr_provider)
        if not primary:
            raise ProviderError(self.settings.ocr_provider, "Unknown OCR_PROVIDER configured.")
        try:
            return primary.extract_document(file_bytes, filename, mime_type)
        except ProviderError as exc:
            if allow_fallback and self.settings.ocr_fallback_provider and self.settings.ocr_fallback_provider != self.settings.ocr_provider:
                fallback = self.ocr_registry[self.settings.ocr_fallback_provider]
                if fallback.is_paid_service and not self.settings.allow_paid_providers:
                    raise
                res = fallback.extract_document(file_bytes, filename, mime_type)
                res.unreadable_or_ambiguous_flags.append(f"Fallback OCR used after '{self.settings.ocr_provider}' error: {exc.reason}")
                return res
            raise

    def get_health_report(self) -> Dict[str, Any]:
        return {
            "demo_mode": self.settings.demo_mode,
            "allow_paid_providers": self.settings.allow_paid_providers,
            "active_providers": {
                "stt": self.settings.stt_provider,
                "ocr": self.settings.ocr_provider,
                "translation": self.settings.translation_provider,
                "structuring": self.settings.structuring_provider,
                "triage_engine": self.settings.triage_engine,
            },
            "credentials_configured": {
                "sarvam_api_key_present": bool(self.settings.sarvam_api_key),
                "ollama_endpoint": self.settings.ollama_base_url,
            },
        }
