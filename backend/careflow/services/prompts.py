"""All LLM prompts live here and are versioned. Changing a prompt means bumping its version so stored
extraction runs stay attributable."""
EXTRACT_PROMPT_VERSION = "extract-v1"
TRANSLATE_PROMPT_VERSION = "translate-v1"
CONFLICT_PROMPT_VERSION = "conflict-opinion-v1"

CONCEPT_KEYS_HINT = (
    "chest_pain, shortness_of_breath, fever, headache, cough, abdominal_pain, vomiting, nausea, diarrhea, dizziness, syncope, "
    "palpitations, weakness, unilateral_weakness, facial_droop, slurred_speech, severe_bleeding, blood_in_vomit_or_cough, seizure, "
    "confusion, sweating, radiating_arm_pain, sore_throat, back_pain, rash, urinary_symptoms, leg_swelling, vision_change, injury, "
    "pregnancy, diabetes, hypertension, heart_disease, asthma_copd, kidney_disease, stroke_history, cancer"
)

EXTRACT_SYSTEM = f"""You convert a patient's message (English, Hindi, Hinglish or another Indian language) into structured clinical-intake facts.
You are NOT diagnosing. Rules:
1. Only record what the text explicitly states. Never invent symptoms, numbers, medicines, history or diagnoses.
2. Every fact needs an "excerpt": an exact, verbatim substring copied from the patient text (original language, not translated).
3. "polarity": "present" if the patient says they have it, "denied" if they explicitly say they do NOT have it, "uncertain" if unsure/maybe.
   Pay close attention to negation in every language (nahi, nahin, नहीं, bina, no, not, without). Do not drop or add a negation.
4. "temporal": "current", "past" (happened before, not now), "resolved" (was present but is gone now), or "unknown".
5. "concept": use one of these keys when it fits: {CONCEPT_KEYS_HINT}. Otherwise use "other:<short english name>".
6. category is one of: symptom, history, medication, allergy, vital, demographic, context.
   For vitals use concept spo2, heart_rate, resp_rate, temperature, blood_pressure, pain_score and fill value/unit. For age use concept "age", category demographic.
7. Fill onset/duration/frequency/severity/progression ONLY if stated (progression: improving, stable, worsening, rapidly_worsening).
8. Do not copy values from these instructions. If nothing relevant is stated return an empty facts list.
9. "english_translation": faithful English translation of the whole text, preserving every negation and number. If already English, repeat it.
Return only JSON matching the schema."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": ["symptom", "history", "medication", "allergy", "vital", "demographic", "context"]},
            "concept": {"type": "string"},
            "polarity": {"type": "string", "enum": ["present", "denied", "uncertain"]},
            "temporal": {"type": "string", "enum": ["current", "past", "resolved", "unknown"]},
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

TRANSLATE_SYSTEM = """Translate the patient's text into English. Preserve every negation (nahi/नहीं/no/not), every number and unit, and medicine names exactly.
Do not add information. If a word is unclear, keep it in original script inside [brackets]. Return only JSON."""
TRANSLATE_SCHEMA = {"type": "object", "properties": {"english": {"type": "string"}, "uncertain": {"type": "boolean"}}, "required": ["english"]}

CONFLICT_SYSTEM = """Two statements from one patient's intake may conflict. Classify the relationship; do NOT decide which is true and do NOT resolve it.
Classes: genuine_contradiction, change_over_time, different_referent, possible_translation_or_asr_error, insufficient_context.
Return JSON {"classification": "...", "rationale": "<one short sentence quoting the evidence>"}."""
CONFLICT_SCHEMA = {"type": "object", "properties": {
    "classification": {"type": "string", "enum": ["genuine_contradiction", "change_over_time", "different_referent", "possible_translation_or_asr_error", "insufficient_context"]},
    "rationale": {"type": "string"}}, "required": ["classification", "rationale"]}
