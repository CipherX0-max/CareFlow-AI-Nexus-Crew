import pytest

from ..services import lexicon as L


def facts(text):
    return {(d.concept, d.polarity, d.temporal): d for d in L.extract_all(text)}


@pytest.mark.parametrize("text,expect", [
    ("I have chest pain", ("chest_pain", "present", "current")),
    ("I do not have chest pain", ("chest_pain", "denied", "current")),
    ("no fever or chest pain", ("chest_pain", "denied", "current")),
    ("I have no chest pain", ("chest_pain", "denied", "current")),
    ("Chest pain was there yesterday but it is gone now", ("chest_pain", "present", "resolved")),
    ("maybe I have chest pain, not sure", ("chest_pain", "uncertain", "unknown")),
    ("मुझे सीने में दर्द है", ("chest_pain", "present", "current")),
    ("मुझे सीने में दर्द नहीं है", ("chest_pain", "denied", "current")),
    ("mujhe seene mein dard hai", ("chest_pain", "present", "current")),
    ("seene mein dard nahi hai", ("chest_pain", "denied", "current")),
    ("saans nahi aa rahi", ("shortness_of_breath", "present", "current")),  # negation word is INSIDE the symptom phrase
    ("bukhar nahi hai", ("fever", "denied", "current")),
    ("बुखार नहीं है", ("fever", "denied", "current")),
])
def test_polarity_and_time(text, expect):
    assert expect in facts(text), facts(text).keys()


def test_duration_and_progression_attach_to_symptom():
    d = next(x for x in L.extract_all("I have chest pain for 2 days and it is getting worse") if x.concept == "chest_pain")
    assert d.duration == "2 days" and d.progression == "worsening"


def test_hindi_number_words_and_since():
    assert L.parse_duration("teen din se") == "3 days"
    assert L.parse_duration("3 दिन से") == "3 days"
    assert L.parse_duration("since yesterday") == "since yesterday"


def test_orphan_modifier_attaches_to_previous_sentence_and_is_flagged():
    d = next(x for x in L.extract_all("I have a headache. It started 3 days ago and is getting worse.") if x.concept == "headache")
    assert d.progression == "worsening"
    assert "modifier_from_adjacent_sentence" in d.flags


def test_vitals_units_and_plausibility():
    v = {d.concept: d for d in L.extract_vitals("BP 150/95 mmHg, SpO2 93%, pulse 110 bpm, temperature 101 F, pain 7/10")}
    assert (v["blood_pressure"].value_num, v["blood_pressure"].value_num2) == (150, 95)
    assert v["spo2"].value_num == 93 and v["spo2"].unit == "%"
    assert v["temperature"].unit == "F" and "implausible_value" not in v["temperature"].flags
    bad = L.extract_vitals("temperature 101 C")[0]
    assert "implausible_value" in bad.flags and "unit_may_be_F" in bad.flags
    nounit = L.extract_vitals("temperature 38.5")[0]
    assert nounit.unit is None and "unit_missing" in nounit.flags


def test_measurement_time_unknown_is_flagged_not_invented():
    d = L.extract_vitals("SpO2 94%")[0]
    assert "measurement_time_unknown" in d.flags


def test_age_extraction_variants_and_no_false_positive_for_duration():
    assert L.extract_age("I am 45 years old")[0].value_num == 45
    assert L.extract_age("meri umar 52 saal hai")[0].value_num == 52
    assert L.extract_age("I am 3 days into this fever") == []


def test_allergy_and_medication_negation():
    f = {d.concept: d for d in L.extract_meds_allergies("No known allergies. I am taking aspirin 75 mg. I am allergic to penicillin.")}
    assert f["allergy_any"].polarity == "denied"
    assert f["allergy:penicillin"].polarity == "present"
    assert f["medication:aspirin"].polarity == "present"


@pytest.mark.parametrize("text,lang,support", [
    ("I have a headache", "en", "supported"),
    ("मुझे सिरदर्द है", "hi", "supported"),
    ("mujhe kal se bukhar hai aur sir dard bhi hai", "hi-Latn", "supported"),
    ("எனக்கு காய்ச்சல்", "ta", "limited"),
    ("আমার জ্বর", "bn", "limited"),
])
def test_language_detection(text, lang, support):
    assert L.detect_language(text) == (lang, support)


def test_negation_count_for_translation_checks():
    assert L.negation_count("I do not have fever") == 1
    assert L.negation_count("mujhe bukhar nahi hai") == 1
    assert L.negation_count("I have fever") == 0


@pytest.mark.parametrize("text,expect,absent", [
    ("seene mein dard hai aur bukhar nahi hai", ("chest_pain", "present", "current"), ("chest_pain", "denied", "current")),
    ("mujhe seene mein dard hai bukhar nahi", ("chest_pain", "present", "current"), ("chest_pain", "denied", "current")),
    ("I have no fever, chest pain is severe", ("chest_pain", "present", "current"), ("chest_pain", "denied", "current")),
    ("मुझे सीने में दर्द है। बुखार नहीं है।", ("chest_pain", "present", "current"), ("chest_pain", "denied", "current")),
    ("no cough and fever", ("fever", "denied", "current"), ("fever", "present", "current")),
    ("headache and no chest pain", ("headache", "present", "current"), ("headache", "denied", "current")),
])
def test_negation_scope_does_not_leak_across_symptoms(text, expect, absent):
    f = facts(text)
    assert expect in f and absent not in f


def test_medication_extraction_has_no_false_positives_from_ordinary_words():
    for t in ("Chest pain present on admission", "I am on leave from work", "taking care of my mother", "I am taking a rest", "currently on holiday"):
        assert not [d for d in L.extract_meds_allergies(t) if d.polarity == "present"], t
    ok = L.extract_meds_allergies("I am taking metformin 500 mg and on admission I was given nothing")
    assert [d.concept for d in ok if d.polarity == "present"] == ["medication:metformin"]
    rep = L.extract_meds_allergies("Medicines: clopidogrel 75 mg, aspirin 75 mg\nAllergic to penicillin")
    assert {d.concept for d in rep} == {"medication:clopidogrel", "medication:aspirin", "allergy:penicillin"}
