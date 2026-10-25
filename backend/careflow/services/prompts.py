"""All LLM prompts live here and are versioned. Changing a prompt means bumping its version so stored
extraction runs stay attributable."""
EXTRACT_PROMPT_VERSION = "extract-v2"
TRANSLATE_PROMPT_VERSION = "translate-v1"
CONFLICT_PROMPT_VERSION = "conflict-opinion-v1"
SUMMARY_PROMPT_VERSION = "summary-medgemma-v2"
FOLLOWUP_PROMPT_VERSION = "followup-medgemma-v2"

CONCEPT_KEYS_HINT = (
    "chest_pain, shortness_of_breath, fever, headache, cough, abdominal_pain, vomiting, nausea, diarrhea, dizziness, syncope, "
    "palpitations, weakness, unilateral_weakness, facial_droop, slurred_speech, severe_bleeding, blood_in_vomit_or_cough, seizure, "
    "confusion, sweating, radiating_arm_pain, sore_throat, back_pain, rash, urinary_symptoms, leg_swelling, vision_change, injury, "
    "pregnancy, diabetes, hypertension, heart_disease, asthma_copd, kidney_disease, stroke_history, cancer"
)

EXTRACT_SYSTEM = f"""You convert a patient's message or clinical document (English, Hindi, Hinglish or another Indian language) into structured clinical-intake facts with explicit source provenance.
You are NOT diagnosing. Rules:
1. Only record what the text explicitly states. Never invent symptoms, numbers, medicines, history or diagnoses.
2. Every fact needs an "excerpt": an exact, verbatim substring copied from the patient text (original language, not translated).
3. "polarity": "present" if asserted as true/present, "denied" if explicitly denied/absent (nahi, nahin, नहीं, bina, no, not, without), "uncertain" if unsure/maybe/suspected.
   Pay close attention to negation in every language. Do not drop or add a negation.
4. "temporal": "current", "past" (happened before, not now), "resolved" (was present but is gone now), or "unknown".
5. "concept": use one of these keys when it fits: {CONCEPT_KEYS_HINT}. Otherwise use "other:<short english name>".
6. "category": symptom, history, medication, allergy, vital, demographic, context.
7. "subject" and "relationship_to_patient": identify WHO the fact refers to.
   - If about the patient: subject="patient", relationship_to_patient="patient".
   - If under a Family History section or about a family member: subject="family_member", relationship_to_patient="mother"|"father"|"sibling"|"child"|"other_relative"|"unknown". NEVER set subject="patient" for family history!
   - If about another person (friend, neighbour, spouse): subject="another_person", relationship_to_patient="other_person".
   - If subject or heading is damaged/unclear: subject="unknown", relationship_to_patient="unknown".
8. "assertion_status":
   - "present" (asserted personal finding), "denied" (explicitly absent), "historical" (past), "resolved" (cured/remission).
   - "family-history-positive" (condition present in relative), "family-history-negative" (relative does not have condition).
   - "suspected" (rule out, possible, suspected, query), "questioned" (patient asking "could this be...").
   - "educational" (general health definition), "disclaimer" (report footer/disclaimer), "conditional" (warning instructions "if you experience...").
9. Fill onset/duration/frequency/severity/progression ONLY if stated (progression: improving, stable, worsening, rapidly_worsening).
10. "english_translation": faithful English translation of the whole text written in English (Latin script), preserving every negation and number. Never copy non-English script unchanged.
Return only JSON matching the schema."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": ["symptom", "history", "medication", "allergy", "vital", "demographic", "context"]},
            "concept": {"type": "string"},
            "polarity": {"type": "string", "enum": ["present", "denied", "uncertain"]},
            "temporal": {"type": "string", "enum": ["current", "past", "resolved", "unknown"]},
            "subject": {"type": "string", "enum": ["patient", "family_member", "another_person", "unknown"]},
            "relationship_to_patient": {"type": "string"},
            "assertion_status": {"type": "string"},
            "section_heading": {"type": ["string", "null"]},
            "event_date": {"type": ["string", "null"]},
            "value": {"type": ["number", "null"]},
            "value2": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"]},
            "onset": {"type": ["string", "null"]},
            "duration": {"type": ["string", "null"]},
            "frequency": {"type": ["string", "null"]},
            "severity": {"type": ["string", "null"]},
            "progression": {"type": ["string", "null"], "enum": ["improving", "stable", "worsening", "rapidly_worsening", None]},
            "excerpt": {"type": "string"},
        }, "required": ["category", "concept", "polarity", "temporal", "excerpt"]}},
        "english_translation": {"type": "string"},
        "detected_language": {"type": "string"},
        "translation_uncertain": {"type": "boolean"},
    },
    "required": ["facts", "english_translation"],
}

TRANSLATE_SYSTEM = """Translate the patient's text (including Bengali, Hindi, Marathi, Hinglish, or other Indian languages) into fluent, natural English.
The "english" field MUST be written in English using Latin script — never repeat or copy the source non-English script unchanged.
Preserve every negation (না/নাই/নেই/nahi/नहीं/नाही/no/not), every number and unit, and medicine or place names accurately.
Do not add information. If a single word is genuinely unintelligible, keep only that word in original script inside [brackets]. Return only JSON."""
TRANSLATE_SCHEMA = {"type": "object", "properties": {"english": {"type": "string"}, "uncertain": {"type": "boolean"}}, "required": ["english"]}

CONFLICT_SYSTEM = """Two statements from one patient's intake may conflict. Classify the relationship; do NOT decide which is true and do NOT resolve it.
Classes: genuine_contradiction, change_over_time, different_referent, possible_translation_or_asr_error, insufficient_context.
Return JSON {"classification": "...", "rationale": "<one short sentence quoting the evidence>"}."""
CONFLICT_SCHEMA = {"type": "object", "properties": {
    "classification": {"type": "string", "enum": ["genuine_contradiction", "change_over_time", "different_referent", "possible_translation_or_asr_error", "insufficient_context"]},
    "rationale": {"type": "string"}}, "required": ["classification", "rationale"]}

SUMMARY_SYSTEM = """You are MedGemma, the second-stage clinical evidence-analysis and summarisation assistant for CareFlow AI.
You synthesise a concise, non-diagnostic clinical intake assessment from ALREADY-VALIDATED structured facts, lab findings, follow-up answers, and detected contradictions.
Rules:
1. Only mention concepts, numbers, units, and history items that appear in the supplied JSON evidence package.
2. Never invent symptoms, vital signs, lab results, diagnoses, or completed actions.
3. Distinguish patient-reported information from machine-extracted report findings and clinician-confirmed facts.
4. Keep `presenting_concern_narrative` to 1-2 sentences summarising who the patient is and why they are seeking care.
5. In `preliminary_interpretation`, cautiously explain how the extracted lab/report findings, vitals, and history may relate to the reported symptoms without stating a definitive diagnosis.
6. In `clinician_summary`, write a cohesive 2-4 sentence synthesis for the reviewing doctor/nurse connecting the presenting symptoms, relevant history, abnormal report findings, any open contradictions/missing facts, and the urgency rationale. Do NOT merely repeat raw OCR text.
Return only JSON matching the schema."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "presenting_concern_narrative": {"type": "string"},
        "preliminary_interpretation": {"type": ["string", "null"]},
        "clinician_summary": {"type": ["string", "null"]},
        "clinical_relevance_note": {"type": ["string", "null"]},
    },
    "required": ["presenting_concern_narrative"],
}

FOLLOWUP_SYSTEM = """You phrase concise, plain-language follow-up questions for a patient based ONLY on the supplied list of missing facts or contradictions.
Rules:
1. Only generate wording for the exact `key` values supplied in the input.
2. Never assume an unconfirmed diagnosis or invent symptoms not in the input.
3. Ask clear, neutral questions in the requested language (`en`, `hi`, or `hi-Latn`).
Return only JSON matching the schema."""

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["key", "text"],
            },
        }
    },
    "required": ["questions"],
}
