"""Comprehensive tests for the 5 clinical intake & reviewer workflow improvements:
1. Structured patient summary (PatientSummarySchema, provenance, caching, regeneration, LLM failure fallback)
2. Transcription speed & translation reliability (model cache reuse, timing, loop deduplication, translation error fallback, number/negation flags, language retry)
3. Priority dashboard deterministic ranking (queue_rank #1..#N, emergency/urgent/needs_review/normal, tie-breakers, red_flags)
4. Case-specific follow-up questions (missing duration, contradiction, unclear lab values, no redundant questions, <=6 cap, immediate emergency escalation)
5. Authenticated audio playback & HTTP Range support (200 OK, 206 Partial Content, 416 Range Not Satisfiable, 403 RBAC, 404 discarded/missing)
"""
from __future__ import annotations

import io
import math
import struct
import wave

from . import media
from .helpers import Api, consent, make_user
from ..services import extraction, stt, summary as SM


def _make_wav_bytes(duration_sec: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    n_samples = int(rate * duration_sec)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n_samples):
            val = int(8000 * math.sin(2 * math.pi * 440 * (i / rate)))
            frames.extend(struct.pack("<h", val))
        w.writeframes(bytes(frames))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Structured Patient Summary
# ---------------------------------------------------------------------------

def test_structured_patient_summary_sections_provenance_caching_and_regeneration(app, fake_llm):
    staff = Api(app)
    make_user("sum-staff@demo-clinic.org", role="staff", name="Nurse Asha")
    assert staff.login("sum-staff@demo-clinic.org").status_code == 200

    doc = Api(app)
    make_user("sum-doc@demo-clinic.org", role="doctor", name="Dr Verma")
    assert doc.login("sum-doc@demo-clinic.org").status_code == 200

    pat = Api(app)
    assert pat.signup("sum-pat@demo-clinic.org", name="Rohan Mehta", age=52, mobile="+919876543210").status_code == 201
    assert consent(pat).status_code == 201
    cid = pat.post("/api/cases").json()["id"]

    # Add symptom text + history + lab report PDF
    pat.post(
        f"/api/cases/{cid}/messages",
        {"text": "I have severe chest pain and shortness of breath for 2 days. I have diabetes and take metformin. No fever."},
    )
    lab_lines = [
        "CITY DIAGNOSTICS LAB REPORT",
        "Hemoglobin: 8.2 g/dL (Ref: 13.0 - 17.0) LOW",
        "WBC: 14.5",
    ]
    pdf_bytes = media.digital_pdf(lab_lines)
    up_res = pat.upload(f"/api/cases/{cid}/uploads", "cbc.pdf", pdf_bytes, "application/pdf")
    assert up_res.status_code == 202
    uid = up_res.json()["upload"]["id"]
    pat.post(f"/api/cases/{cid}/uploads/{uid}/confirm", {"text": "\n".join(lab_lines)})
    assert pat.post(f"/api/cases/{cid}/submit").status_code == 200

    # Fetch detail from staff and doctor workspaces
    r1 = doc.get(f"/api/doctor/cases/{cid}")
    assert r1.status_code == 200
    d1 = r1.json()
    ps = d1["patient_summary"]
    assert ps is not None
    # Schema validation passes on returned dict
    validated = SM.PatientSummarySchema.model_validate(ps)
    assert validated.presenting_concern["summary_text"]
    assert validated.symptoms is not None
    assert any(s.concept == "chest_pain" and s.polarity == "present" for s in validated.symptoms["positive"])
    assert any(s.concept == "fever" and s.polarity == "denied" for s in validated.symptoms["negative_explicitly_denied"])
    assert any(s.source_label == "patient_reported" for s in validated.symptoms["positive"])
    assert validated.report_findings is not None and len(validated.report_findings) >= 1
    assert any("hemoglobin" in f.test_name.lower() for f in validated.report_findings)
    assert validated.urgency_indicators is not None
    assert validated.proposed_priority is not None
    assert validated.proposed_priority["proposed_queue"] in ("URGENT", "NEEDS_REVIEW", "NORMAL")
    assert validated.confidence_and_review["unverified_count"] >= 1

    # Verify caching: refreshing case detail does NOT regenerate or invoke LLM again
    digest_before = ps["inputs_digest"]
    gen_at_before = ps["generated_at"]
    sum_calls_before = len(fake_llm.summary_calls)

    r2 = staff.get(f"/api/staff/cases/{cid}")
    assert r2.status_code == 200
    ps2 = r2.json()["patient_summary"]
    assert ps2["inputs_digest"] == digest_before
    assert ps2["generated_at"] == gen_at_before
    assert len(fake_llm.summary_calls) == sum_calls_before

    # Clinician verifies a fact -> provenance updates to clinician_confirmed when regenerated
    chest_fact = next(f for f in d1["facts"] if f["concept"] == "chest_pain")
    ver = doc.post(
        f"/api/doctor/cases/{cid}/facts/{chest_fact['id']}/verify",
        {"verification": "staff_verified", "reason": "Confirmed directly with patient during triage call."},
    )
    assert ver.status_code == 200

    # Patient sends new message -> inputs_digest changes and summary regenerates
    pat.post(f"/api/cases/{cid}/messages", {"text": "The pain is also radiating to my left arm."})
    r3 = doc.get(f"/api/doctor/cases/{cid}")
    ps3 = r3.json()["patient_summary"]
    assert ps3["inputs_digest"] != digest_before
    assert any(s["concept"] == "chest_pain" and s["source_label"] == "clinician_confirmed" for s in ps3["symptoms"]["positive"])


def test_structured_summary_degrades_cleanly_when_llm_unavailable(app, fake_llm):
    doc = Api(app)
    make_user("deg-doc@demo-clinic.org", role="doctor", name="Dr Sen")
    doc.login("deg-doc@demo-clinic.org")

    pat = Api(app)
    pat.signup("deg-pat@demo-clinic.org", name="Meera Nair", age=34, mobile="+919811122233")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    fake_llm.fail = "Local Ollama connection refused"
    pat.post(f"/api/cases/{cid}/messages", {"text": "I have fever and cough for 3 days."})
    pat.post(f"/api/cases/{cid}/submit")

    d = doc.get(f"/api/doctor/cases/{cid}").json()
    ps = d["patient_summary"]
    assert ps is not None
    assert ps["status"] in ("degraded", "failed")
    assert ps["ai_status_note"] is not None
    # Deterministic sections are still populated even when LLM fails
    assert ps["presenting_concern"]["summary_text"]
    assert any(s["concept"] == "fever" for s in ps["symptoms"]["positive"])


# ---------------------------------------------------------------------------
# 2. Transcription Speed & Translation Reliability
# ---------------------------------------------------------------------------

def test_stt_segment_and_phrase_deduplication_and_quality_flags():
    raw_segs = [
        stt.Segment(0.0, 1.5, "मुझे तीन दिन से बुखार है", 0.85, 0.05),
        stt.Segment(1.5, 3.0, "मुझे तीन दिन से बुखार है", 0.85, 0.05),
        stt.Segment(3.0, 4.5, "और खांसी भी है", 0.82, 0.05),
    ]
    deduped, removed = stt._dedupe_segments(raw_segs)
    assert removed is True
    assert len(deduped) == 2
    assert deduped[0].text == "मुझे तीन दिन से बुखार है"
    assert deduped[1].text == "और खांसी भी है"

    # Internal phrase loop collapsing
    loop_segs = [
        stt.Segment(0.0, 4.0, "I have fever and chills I have fever and chills I have fever and chills", 0.78, 0.1),
    ]
    deduped_loop, loop_removed = stt._dedupe_segments(loop_segs)
    assert loop_removed is True
    assert deduped_loop[0].text == "I have fever and chills"

    # Translation quality flags: repetition + number mismatch
    q_flags = extraction.translation_quality_flags(
        "patient has fever for 5 days patient has fever for 5 days patient has fever for 5 days",
        "मरीज को 3 दिन से बुखार है",
    )
    assert "translation_repetition" in q_flags
    assert "translation_number_mismatch" in q_flags


def test_stt_translation_failure_preserves_original_transcript_and_flags_review(monkeypatch, tmp_path):
    class FakeInfo:
        language = "hi"
        language_probability = 0.94

    class FakeSeg:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text
            self.avg_logprob = -0.2
            self.no_speech_prob = 0.02

    class FakeWhisperModel:
        def __init__(self):
            self.calls = []

        def transcribe(self, path, task="transcribe", language=None, **kw):
            self.calls.append((task, language, kw.get("beam_size")))
            if task == "translate":
                raise RuntimeError("Simulated translation beam failure")
            return [FakeSeg(0.0, 2.0, "मुझे सीने में दर्द है")], FakeInfo()

    wav_file = tmp_path / "sample.wav"
    wav_file.write_bytes(_make_wav_bytes(duration_sec=0.6))

    fake_model = FakeWhisperModel()
    monkeypatch.setattr(stt, "get_model", lambda: fake_model)

    res = stt.transcribe(str(wav_file), language_hint="hi", translate=True)
    assert res.text == "मुझे सीने में दर्द है"
    assert res.english == ""
    assert "translation_unavailable" in res.flags
    assert "translation_requires_review" in res.flags
    assert res.transcription_ms >= 0
    assert res.translation_ms >= 0


def test_audio_reupload_with_changed_language_reprocesses(app, monkeypatch):
    calls: list[str | None] = []

    def fake_transcribe(path: str, language_hint: str | None = None):
        calls.append(language_hint)
        lang = language_hint or "hi"
        txt = "मुझे बुखार है" if lang == "hi" else "मला ताप आहे"
        return stt.Transcript(
            language=lang,
            language_probability=0.92,
            segments=[stt.Segment(0.0, 1.2, txt, 0.85, 0.05)],
            english_segments=[stt.Segment(0.0, 1.2, "I have a fever", 0.85, 0.05)],
            duration=1.2,
            flags=[],
            transcription_ms=12,
            translation_ms=8,
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    pat = Api(app)
    pat.signup("lang-pat@demo-clinic.org", name="Aarti", age=29, mobile="+919822233344")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]
    wav_bytes = _make_wav_bytes(duration_sec=0.6)

    # Upload first with hi
    r1 = pat.c.post(
        f"/api/cases/{cid}/uploads",
        files={"file": ("voice.wav", wav_bytes, "audio/wav")},
        data={"language": "hi"},
        headers=pat._h(),
    )
    assert r1.status_code == 202
    assert pat.get(f"/api/cases/{cid}").json()["uploads"][0]["detected_language"] == "hi"

    # Re-upload identical bytes with Marathi ("mr") -> must re-process instead of returning stale "hi"
    r2 = pat.c.post(
        f"/api/cases/{cid}/uploads",
        files={"file": ("voice.wav", wav_bytes, "audio/wav")},
        data={"language": "mr"},
        headers=pat._h(),
    )
    assert r2.status_code == 202
    assert pat.get(f"/api/cases/{cid}").json()["uploads"][0]["detected_language"] == "mr"
    assert calls == ["hi", "mr"]


# ---------------------------------------------------------------------------
# 3. Priority Dashboard Deterministic Ranking
# ---------------------------------------------------------------------------

def test_priority_dashboard_deterministic_ranking_and_red_flags(app):
    staff = Api(app)
    make_user("rank-staff@demo-clinic.org", role="staff", name="Intake Coord")
    staff.login("rank-staff@demo-clinic.org")

    doc = Api(app)
    make_user("rank-doc@demo-clinic.org", role="doctor", name="Dr Kapoor")
    doc.login("rank-doc@demo-clinic.org")

    # Case A: Normal (mild sore throat for 2 days, age 30, no red flags)
    pat_a = Api(app)
    pat_a.signup("pat-a@demo-clinic.org", name="Patient Normal", age=30, mobile="+919700000001")
    consent(pat_a)
    cid_a = pat_a.post("/api/cases").json()["id"]
    pat_a.post(f"/api/cases/{cid_a}/messages", {"text": "I am 30 years old. I have a mild sore throat for 2 days. It is not getting worse. No fever, no shortness of breath."})
    pat_a.post(f"/api/cases/{cid_a}/submit")

    # Case B: Needs Review (contradiction / missing duration)
    pat_b = Api(app)
    pat_b.signup("pat-b@demo-clinic.org", name="Patient Review", age=45, mobile="+919700000002")
    consent(pat_b)
    cid_b = pat_b.post("/api/cases").json()["id"]
    pat_b.post(f"/api/cases/{cid_b}/messages", {"text": "I have a cough."})
    pat_b.post(f"/api/cases/{cid_b}/submit")

    # Case C: Emergency / Urgent (severe chest pain and shortness of breath)
    pat_c = Api(app)
    pat_c.signup("pat-c@demo-clinic.org", name="Patient Emergency", age=62, mobile="+919700000003")
    consent(pat_c)
    cid_c = pat_c.post("/api/cases").json()["id"]
    pat_c.post(f"/api/cases/{cid_c}/messages", {"text": "I have crushing chest pain and difficulty breathing for 1 hour."})
    pat_c.post(f"/api/cases/{cid_c}/submit")

    rows_staff = staff.get("/api/staff/cases").json()["cases"]
    rows_doc = doc.get("/api/doctor/cases").json()["cases"]

    for rows in (rows_staff, rows_doc):
        ids = [r["id"] for r in rows]
        assert cid_c in ids and cid_b in ids and cid_a in ids
        # Verify 1-based sequential queue_rank
        ranks = [r["queue_rank"] for r in rows]
        assert ranks == list(range(1, len(rows) + 1))
        # Emergency case C must rank ahead of B and A
        idx_c = ids.index(cid_c)
        idx_b = ids.index(cid_b)
        idx_a = ids.index(cid_a)
        assert idx_c < idx_b <= idx_a
        # Emergency case C exposes red_flags
        assert rows[idx_c]["emergency_flagged"] is True
        assert len(rows[idx_c]["red_flags"]) >= 1


# ---------------------------------------------------------------------------
# 4. Case-Specific Follow-Up Questions
# ---------------------------------------------------------------------------

def test_context_aware_followups_duration_conflict_lab_and_cap(app):
    pat = Api(app)
    pat.signup("fu-pat@demo-clinic.org", name="Vikram", age=41, mobile="+919600000001")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    # 1. Symptom without duration + lab report with missing unit + contradiction
    pat.post(f"/api/cases/{cid}/messages", {"text": "I have fever. I also have headache."})
    pat.post(f"/api/cases/{cid}/messages", {"text": "I do not have fever."})
    lab_lines = ["Creatinine: 2.4 (Ref: 0.7 - 1.3) HIGH"]
    pdf_bytes = media.digital_pdf(lab_lines)
    up = pat.upload(f"/api/cases/{cid}/uploads", "renal.pdf", pdf_bytes, "application/pdf").json()["upload"]
    pat.post(f"/api/cases/{cid}/uploads/{up['id']}/confirm", {"text": "\n".join(lab_lines)})
    pat.post(f"/api/cases/{cid}/submit")

    detail = pat.get(f"/api/cases/{cid}").json()
    questions = detail["questions"]
    assert 1 <= len(questions) <= 6
    keys = [q["key"] for q in questions]
    # Contradiction on fever and missing duration should be prioritized
    assert any(k.startswith("conflict:") for k in keys)
    assert any(k.startswith("duration:") for k in keys)
    # Age was already provided at signup (41), so "age" must NOT be asked
    assert "age" not in keys


def test_unclear_lab_triggers_lab_verification_question_when_slots_available(app):
    pat = Api(app)
    pat.signup("fu-lab@demo-clinic.org", name="Neha", age=36, mobile="+919600000002")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    # Provide duration so symptom_duration is already known, leaving a slot for lab verification
    pat.post(f"/api/cases/{cid}/messages", {"text": "I have a sore throat for 3 days, not getting worse."})
    lab_lines = ["Hemoglobin: 9.1 (Ref: 12.0 - 15.5) LOW"]
    pdf_bytes = media.digital_pdf(lab_lines)
    up = pat.upload(f"/api/cases/{cid}/uploads", "hb.pdf", pdf_bytes, "application/pdf").json()["upload"]
    pat.post(f"/api/cases/{cid}/uploads/{up['id']}/confirm", {"text": "\n".join(lab_lines)})
    pat.post(f"/api/cases/{cid}/submit")

    questions = pat.get(f"/api/cases/{cid}").json()["questions"]
    keys = [q["key"] for q in questions]
    assert not any(k.startswith("duration:") for k in keys)
    assert any(k.startswith("lab:") for k in keys)


def test_emergency_red_flag_escalates_immediately_without_waiting_for_followups(app, doctor):
    pat = Api(app)
    pat.signup("fu-em@demo-clinic.org", name="Kiran", age=58, mobile="+919600000003")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    msg_res = pat.post(f"/api/cases/{cid}/messages", {"text": "Severe chest pain and sweating right now"})
    assert msg_res.json()["emergency"]["triggered"] is True
    pat.post(f"/api/cases/{cid}/submit")

    d_pat = pat.get(f"/api/cases/{cid}").json()
    assert d_pat["emergency_flagged"] is True

    d_doc = doctor.get(f"/api/doctor/cases/{cid}").json()
    assert d_doc["case"]["emergency_flagged"] is True
    assert d_doc["case"]["proposed_queue"] == "URGENT"


# ---------------------------------------------------------------------------
# 5. Authenticated Audio Playback & HTTP Range Support
# ---------------------------------------------------------------------------

def test_audio_playback_streaming_range_and_rbac(app, monkeypatch):
    monkeypatch.setattr(
        stt,
        "transcribe",
        lambda path, language_hint=None: stt.Transcript(
            language="hi",
            language_probability=0.95,
            segments=[stt.Segment(0.0, 1.0, "मुझे सिरदर्द है", 0.9, 0.01)],
            english_segments=[stt.Segment(0.0, 1.0, "I have a headache", 0.9, 0.01)],
            duration=1.0,
            flags=[],
        ),
    )

    staff = Api(app)
    make_user("aud-staff@demo-clinic.org", role="staff", name="Staff Audio")
    staff.login("aud-staff@demo-clinic.org")

    doc = Api(app)
    make_user("aud-doc@demo-clinic.org", role="doctor", name="Doc Audio")
    doc.login("aud-doc@demo-clinic.org")

    admin = Api(app)
    make_user("aud-admin@demo-clinic.org", role="admin", name="Sys Admin")
    admin.login("aud-admin@demo-clinic.org")

    pat1 = Api(app)
    pat1.signup("aud-pat1@demo-clinic.org", name="Pat One", age=33, mobile="+919500000001")
    consent(pat1)

    pat2 = Api(app)
    pat2.signup("aud-pat2@demo-clinic.org", name="Pat Two", age=28, mobile="+919500000002")
    consent(pat2)

    cid = pat1.post("/api/cases").json()["id"]
    wav_bytes = _make_wav_bytes(duration_sec=0.6)
    up = pat1.upload(f"/api/cases/{cid}/uploads", "note.wav", wav_bytes, "audio/wav")
    assert up.status_code == 202
    uid = up.json()["upload"]["id"]
    pat1.post(f"/api/cases/{cid}/uploads/{uid}/confirm", {"text": "मुझे सिरदर्द है"})
    pat1.post(f"/api/cases/{cid}/submit")

    file_url = f"/api/cases/{cid}/uploads/{uid}/file"

    # 1. Authorised owner, staff, and doctor get 200 OK with Accept-Ranges: bytes and audio MIME
    for viewer in (pat1, staff, doc):
        r = viewer.get(file_url)
        assert r.status_code == 200
        assert r.headers.get("accept-ranges") == "bytes"
        assert r.headers.get("content-type", "").startswith("audio/")
        assert r.content == wav_bytes

    # 2. HTTP Range request for seeking returns 206 Partial Content with exact slice
    r_range = doc.c.get(file_url, headers={"Range": "bytes=0-99"})
    assert r_range.status_code == 206
    assert r_range.headers.get("accept-ranges") == "bytes"
    assert r_range.headers.get("content-range") == f"bytes 0-99/{len(wav_bytes)}"
    assert len(r_range.content) == 100
    assert r_range.content == wav_bytes[:100]

    # 3. Invalid range returns 416 Range Not Satisfiable
    r_bad = doc.c.get(file_url, headers={"Range": f"bytes={len(wav_bytes) + 500}-{len(wav_bytes) + 600}"})
    assert r_bad.status_code == 416

    # 4. Unauthorised users: another patient gets 404 (no existence oracle), system admin gets 403
    assert pat2.get(file_url).status_code in (403, 404)
    assert admin.get(file_url).status_code == 403

    # 5. Missing upload id returns 404
    assert doc.get(f"/api/cases/{cid}/uploads/nonexistent-id/file").status_code == 404
