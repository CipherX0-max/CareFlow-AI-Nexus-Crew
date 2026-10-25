"""Synthetic demonstration / acceptance scenarios (NO real patient data). Used by both the automated acceptance
tests and the demo seeding command, so what is demonstrated is exactly what is tested.

Expected outcomes below are defined by THIS fixture and the configured DEMONSTRATION ruleset - they are not clinical
ground truth."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..tests import media  # synthetic file builders (PDF/images/speech)


@dataclass
class Scenario:
    id: str
    title: str
    steps: list[tuple]
    queue: str                      # expected proposed queue after the steps
    needs: tuple = ()               # "whisper" | "tts"
    facts: list[dict] = field(default_factory=list)       # {"concept","polarity","temporal","source_type"?}
    conflicts: list[str] = field(default_factory=list)    # expected conflict kinds (subset)
    questions: list[str] = field(default_factory=list)    # expected question key prefixes asked at some point
    flags: list[str] = field(default_factory=list)        # substrings expected among message/upload/fact flags
    rules: list[str] = field(default_factory=list)        # expected triggered demo rule ids
    note: str = ""


def _wav(kind: str, tmp: Path) -> bytes:
    """Synthetic speech via macOS `say` + ffmpeg."""
    if kind == "silent":
        return media.silent_wav(2.0)
    texts = {
        "english": ("Rishi", "I am sixty years old. I have chest pain for two days and I feel short of breath."),
        "hindi": ("Lekha", "मुझे दो दिन से सीने में दर्द है और सांस लेने में तकलीफ है।"),
        "tamil": ("Vani", "எனக்கு இரண்டு நாட்களாக காய்ச்சல் இருக்கிறது."),
    }
    if kind == "codeswitch":
        a = media.speech_wav("मुझे दो दिन से बुखार है।", "Lekha", tmp / "cs1.wav")
        b = media.speech_wav("And I also have a bad headache since yesterday.", "Rishi", tmp / "cs2.wav")
        return media.concat_wav([a, b], tmp / "cs.wav").read_bytes()
    voice, text = texts[kind]
    return media.speech_wav(text, voice, tmp / f"{kind}.wav").read_bytes()


BUILDERS: dict[str, Callable[[Path], bytes]] = {
    "pdf_digital": lambda t: media.digital_pdf(),
    "pdf_scanned": lambda t: media.scanned_pdf(),
    "photo": lambda t: media.image_bytes(media.text_image()),
    "photo_unreadable": lambda t: media.unreadable_image(),
    "pdf_conflicting": lambda t: media.digital_pdf(["Discharge summary (synthetic)", "Age: 58 years", "Chest pain present on admission", "Medicines: clopidogrel 75 mg", "Allergic to penicillin"]),
    "wav_english": lambda t: _wav("english", t), "wav_hindi": lambda t: _wav("hindi", t), "wav_tamil": lambda t: _wav("tamil", t),
    "wav_codeswitch": lambda t: _wav("codeswitch", t), "wav_silent": lambda t: _wav("silent", t),
}
MIME = {"pdf": "application/pdf", "photo": "image/png", "wav": "audio/wav"}

# step grammar:
# ("say", text) ("upload", builder, confirm) ("answer", key_prefix, text, choice) ("submit",) ("staff_resolve", concept, outcome, note)
# confirm: "as_is" | "skip" | ("append", "extra text") | ("replace", "text")
S = Scenario
SCENARIOS: list[Scenario] = [
    S("S01-english", "Clear English text intake (urgent demo rule)", [("say", "I am 45 years old. I have chest pain for 2 days, it is getting worse, and I feel short of breath."), ("submit",)],
      "URGENT", facts=[dict(concept="chest_pain", polarity="present", temporal="current", source_type="message"), dict(concept="shortness_of_breath", polarity="present")],
      rules=["DEMO-U1"]),
    S("S02-hindi", "Hindi text intake", [("say", "मुझे 3 दिन से सीने में दर्द है। बुखार नहीं है। मेरी उम्र 52 साल है।"), ("submit",)],
      "URGENT", facts=[dict(concept="chest_pain", polarity="present"), dict(concept="fever", polarity="denied")], rules=["DEMO-U4"]),
    S("S03-hinglish", "Hinglish text intake", [("say", "Mujhe seene mein dard hai 2 din se aur saans nahi aa rahi. Bukhar nahi hai. Meri umar 52 saal hai."), ("submit",)],
      "URGENT", facts=[dict(concept="chest_pain", polarity="present"), dict(concept="shortness_of_breath", polarity="present"), dict(concept="fever", polarity="denied")]),
    S("S04-regional-audio", "Regional-language (Tamil) audio recording", [("upload", "wav_tamil", "as_is"), ("submit",)], "NEEDS_REVIEW", needs=("whisper", "tts"),
      flags=["language_outside_primary_support"], note="Language outside primary support: transcript flagged, doctor must review."),
    S("S05-codeswitch-audio", "Hindi/English code-switching within one recording (patient corrects the transcript)", [("upload", "wav_codeswitch", ("replace", "मुझे दो दिन से बुखार है। And I also have a bad headache since yesterday. I am 33 years old.")),
      ("submit",)], "NORMAL", needs=("whisper", "tts"), facts=[dict(concept="fever", polarity="present", source_type="audio"), dict(concept="headache", polarity="present", source_type="audio")],
      flags=["asr_translation_concepts_differ"]),
    S("S06-digital-pdf", "Clear digital PDF report", [("say", "I am 58 years old. I have chest pain."), ("upload", "pdf_digital", "as_is"), ("submit",)], "URGENT",
      facts=[dict(concept="chest_pain", polarity="present", source_type="document"), dict(concept="medication:clopidogrel", polarity="present", source_type="document")]),
    S("S07-scanned-pdf", "Scanned (image-only) PDF", [("say", "I am 58 years old. I have a headache for 2 days."), ("upload", "pdf_scanned", "as_is"), ("submit",)], "URGENT",
      facts=[dict(concept="chest_pain", polarity="present", source_type="document")]),
    S("S07b-photo", "Photograph of a report", [("say", "I am 58 years old. I have a headache for 2 days."), ("upload", "photo", "as_is"), ("submit",)], "URGENT",
      facts=[dict(concept="chest_pain", polarity="present", source_type="document")]),
    S("S08-unreadable", "Unreadable OCR and failed audio transcription", [("say", "I am 40 years old. I have a sore throat for 3 days."), ("upload", "photo_unreadable", "skip"),
      ("upload", "wav_silent", "skip"), ("submit",)], "NEEDS_REVIEW", flags=["unreadable_upload"]),
    S("S09-present", "A symptom explicitly present (benign, complete)", [("say", "I am 30 years old. I have a sore throat for 3 days."), ("submit",)], "NORMAL", facts=[dict(concept="sore_throat", polarity="present", temporal="current")]),
    S("S10-present-then-denied", "Same symptom explicitly denied later (genuine contradiction)", [("say", "I am 50 years old. I have chest pain."), ("say", "I do not have chest pain."), ("submit",)],
      "URGENT", conflicts=["presence"], questions=["conflict:"], facts=[dict(concept="chest_pain", polarity="present"), dict(concept="chest_pain", polarity="denied")]),
    S("S11-resolved", "Symptom present earlier but resolved (not a contradiction)", [("say", "I am 30 years old. Chest pain was there yesterday but it is gone now. I have had a headache for 2 days."),
      ("submit",)], "NORMAL", facts=[dict(concept="chest_pain", polarity="present", temporal="resolved")]),
    S("S12-text-vs-report", "Conflict between typed text and the uploaded report", [("say", "I am 58 years old. I have a headache for 2 days. I take no regular medicines and have no known allergies."),
      ("upload", "pdf_conflicting", "as_is"), ("submit",)], "URGENT", conflicts=["medication", "allergy"], facts=[dict(concept="medication:clopidogrel", polarity="present", source_type="document")]),
    S("S13-asr-negation", "Transcription error that changes a negation (audio vs text)", [("say", "I am 55 years old. I do not have chest pain. I have a headache for 1 day."),
      ("upload", "wav_english", ("replace", "I have chest pain")), ("submit",)], "URGENT", needs=("whisper", "tts"), conflicts=["presence"]),
    S("S14-missing", "Missing age, duration and measurements", [("say", "I have a fever."), ("submit",)], "NEEDS_REVIEW", questions=["age", "duration:fever"]),
    S("S15-units", "Incorrect unit; conflicting readings with unknown times", [("say", "I am 40 years old. I have had a fever for 2 days. My temperature is 101 C. My oxygen saturation is 97%. Later my SpO2 is 90%."), ("submit",)],
      "URGENT", conflicts=["unit", "value"], rules=["DEMO-V1"], facts=[dict(concept="temperature", polarity="present")],
      note="A conflicting SpO2 reading below the PLACEHOLDER threshold still triggers the demo rule: contradictions never lower urgency."),
    S("S16-urgent-demo-rule", "Potentially urgent case triggering a demonstration rule", [("say", "I am 66 years old. My face is drooping and I have slurred speech since this morning."), ("submit",)],
      "URGENT", rules=["DEMO-U2"], facts=[dict(concept="facial_droop", polarity="present"), dict(concept="slurred_speech", polarity="present")]),
    S("S17-cannot-categorise", "A case that cannot safely be categorised", [("say", "I feel unwell."), ("submit",)], "NEEDS_REVIEW", questions=["age"]),
    S("S18-clarify-and-note", "Clinician clarifies a conflict and updates the final note", [("say", "I am 50 years old. I have chest pain."), ("say", "I do not have chest pain."), ("submit",),
      ("answer", "conflict:", "", "earlier_resolved"), ("staff_resolve", "chest_pain", "earlier_resolved", "Phoned patient: pain was last week only and has fully gone.")],
      "NEEDS_REVIEW", conflicts=["presence"]),
    S("S19-full-multimodal", "Text + audio + report, genuine contradiction, follow-up, reassessment, human-approved note",
      [("say", "I am 58 years old. I do not have chest pain. I have a headache for 2 days. I take no regular medicines and have no known allergies."),
       ("upload", "wav_english", ("replace", "I have chest pain for two days and I feel short of breath.")),
       ("upload", "pdf_conflicting", "as_is"), ("submit",)],
      "URGENT", needs=("whisper", "tts"), conflicts=["presence", "medication", "allergy"], questions=["conflict:"],
      facts=[dict(concept="chest_pain", polarity="denied", source_type="message"), dict(concept="chest_pain", polarity="present", source_type="audio"),
             dict(concept="chest_pain", polarity="present", source_type="document")]),
]


def run_scenario(patient, doctor, scn: Scenario, tmp: Path) -> dict:
    """Drive a scenario through the real HTTP API. `patient`/`doctor` are logged-in test/HTTP clients (Api-like)."""
    case_id = patient.post("/api/cases").json()["id"]
    asked: set[str] = set()

    def collect():
        for q in patient.get(f"/api/cases/{case_id}").json()["questions"]:
            asked.add(q["key"])

    for st in scn.steps:
        op = st[0]
        if op == "say":
            r = patient.post(f"/api/cases/{case_id}/messages", {"text": st[1]})
            assert r.status_code == 201, r.text
        elif op == "upload":
            _, builder, confirm = st
            data = BUILDERS[builder](tmp)
            kind = builder.split("_")[0]
            ext = {"pdf": "report.pdf", "photo": "photo.png", "wav": "voice.wav"}[kind]
            r = patient.upload(f"/api/cases/{case_id}/uploads", ext, data, MIME[kind])
            assert r.status_code == 202, r.text
            up = r.json()["upload"]
            got = patient.get(f"/api/cases/{case_id}/uploads/{up['id']}").json()
            if confirm != "skip" and got["status"] == "needs_confirmation":
                text = got["extracted_text"]
                if isinstance(confirm, tuple):
                    text = confirm[1] if confirm[0] == "replace" else text + "\n" + confirm[1]
                rc = patient.post(f"/api/cases/{case_id}/uploads/{up['id']}/confirm", {"text": text})
                assert rc.status_code == 200, rc.text
        elif op == "answer":
            _, prefix, text, choice = st
            q = next((q for q in patient.get(f"/api/cases/{case_id}").json()["questions"] if q["key"].startswith(prefix) and q["status"] == "open"), None)
            assert q, f"no open question starting with {prefix!r}"
            r = patient.post(f"/api/cases/{case_id}/questions/{q['id']}/answer", {"text": text, "choice": choice})
            assert r.status_code == 200, r.text
        elif op == "submit":
            r = patient.post(f"/api/cases/{case_id}/submit")
            assert r.status_code == 200, r.text
        elif op == "staff_resolve":
            _, concept, outcome, note = st
            d = doctor.get(f"/api/doctor/cases/{case_id}").json()
            c = next(c for c in d["conflicts"] if c["concept"] == concept and c["status"] != "reviewer_resolved")
            r = doctor.post(f"/api/doctor/cases/{case_id}/conflicts/{c['id']}/resolve",
                           {"outcome": outcome, "note": note, "evidence_considered": [e["fact_id"] for e in c["evidence"] if "fact_id" in e]})
            assert r.status_code == 200, r.text
        collect()
    return {"case_id": case_id, "asked": asked, "detail": doctor.get(f"/api/doctor/cases/{case_id}").json()}


def finish_review(doctor, case_id: str, *, reason: str = "Reviewed evidence and conflicts; confirming per rules proposal for the demonstration workflow.") -> dict:
    """Reviewer workflow: decide, draft the note, edit it, approve it. Returns the final detail."""
    d = doctor.get(f"/api/doctor/cases/{case_id}").json()
    a = d["assessment"]
    final = a["proposed_queue"]
    if final == "NEEDS_REVIEW":
        final = "NORMAL"  # reviewer resolves the open items and decides
        reason = "Reviewer resolved the open items by phone (demonstration) and decided routine."
    r = doctor.post(f"/api/doctor/cases/{case_id}/decision", {"assessment_id": a["id"], "final_queue": final, "reason": reason if final != a["proposed_queue"] else ""})
    assert r.status_code == 201, r.text
    n = doctor.post(f"/api/doctor/cases/{case_id}/notes/draft").json()
    e = doctor.put(f"/api/doctor/cases/{case_id}/notes/{n['id']}", {"changes": {"reviewer_comments": "Reviewed with source evidence; see conflict register."}})
    assert e.status_code == 200, e.text
    ap = doctor.post(f"/api/doctor/cases/{case_id}/notes/{n['id']}/approve", {"reason": "Reviewed and approved."})
    assert ap.status_code == 200, ap.text
    return doctor.get(f"/api/doctor/cases/{case_id}").json()
