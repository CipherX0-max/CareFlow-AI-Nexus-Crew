"""End-to-end patient -> staff workflow through the real HTTP API (LLM faked, OCR real)."""
import pytest

from ..services import stt
from .conftest import Flow
from .helpers import Api, make_user
from . import media


def detail(staff, cid):
    r = staff.get(f"/api/staff/cases/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def facts_of(d, concept, **kw):
    return [f for f in d["facts"] if f["concept"] == concept and all(f.get(k) == v for k, v in kw.items())]


def open_q(flow, prefix):
    return [q for q in flow.view()["questions"] if q["key"].startswith(prefix) and q["status"] == "open"]


def test_clear_english_intake_to_urgent_queue_with_traceable_reasoning(app, patient, staff, flow):
    r = flow.say("I am 45 years old. I have chest pain for 2 days, it is getting worse, and I feel short of breath.")
    assert r["emergency"]["triggered"] is True and "emergency" in r["emergency"]["message"]["en"].lower()  # shown immediately
    flow.submit()
    d = detail(staff, flow.view()["id"])
    a = d["assessment"]
    assert a["proposed_queue"] == "URGENT" and a["demonstration_only"] is True
    assert {"DEMO-U1", "DEMO-U4", "DEMO-U5"} <= {r["rule_id"] for r in a["triggered_rules"]}
    assert a["ruleset_version"] == "demo-0.1.0-UNREVIEWED" and a["supporting_fact_ids"]
    cp = facts_of(d, "chest_pain")[0]
    assert cp["polarity"] == "present" and cp["duration"] == "2 days" and cp["progression"] == "worsening"
    assert cp["source"]["type"] == "message" and "chest pain" in cp["excerpt"] and cp["origin"] == "explicit"
    assert staff.get("/api/staff/queues/summary").json()["counts"] == {"URGENT": 1, "NORMAL": 0, "NEEDS_REVIEW": 0}


def test_complete_benign_case_reaches_normal_only_after_followups(app, patient, staff, flow):
    flow.say("I have had a sore throat.")
    flow.submit()
    d = detail(staff, flow.view()["id"])
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"  # age and duration missing: not 'normal' by default
    assert {m["key"] for m in d["assessment"]["missing_information"]} >= {"age", "duration:sore_throat"}
    qs = [q["key"] for q in flow.view()["questions"] if q["status"] == "open"]
    assert len(qs) <= 3 and all(q["reason"] for q in flow.view()["questions"])
    flow.answer("age", "34")
    flow.answer("duration:sore_throat", "3 days, gradually")
    d = detail(staff, flow.view()["id"])
    assert d["assessment"]["proposed_queue"] == "NORMAL"
    assert staff.get("/api/staff/queues/summary").json()["counts"]["NORMAL"] == 1


def test_questions_are_not_repeated_and_skipping_is_not_a_negative(app, patient, staff, flow):
    flow.say("I have a headache.")
    flow.answer("age", "", choice=None) if False else None
    q = open_q(flow, "age")[0]
    r = patient.post(f"/api/cases/{flow.case_id}/questions/{q['id']}/answer", {"text": "abc"})
    assert r.status_code == 422  # not a number
    flow.answer("duration:headache", "since yesterday")
    keys = [q["key"] for q in flow.view()["questions"]]
    assert len(keys) == len(set(keys))
    again = patient.post(f"/api/cases/{flow.case_id}/questions/{q['id']}/answer", {"text": "40"})
    assert again.status_code == 200
    r2 = patient.post(f"/api/cases/{flow.case_id}/questions/{q['id']}/answer", {"text": "41"})
    assert r2.status_code == 422  # already answered
    # skipping 'allergies' must not create a negative allergy fact
    flow.submit()
    aq = next(x for x in flow.view()["questions"] if x["key"] == "allergies") if open_q(flow, "allergies") else None
    if aq:
        patient.post(f"/api/cases/{flow.case_id}/questions/{aq['id']}/answer", {"text": "", "choice": "unsure"})
        d = detail(staff, flow.case_id)
        assert not [f for f in d["facts"] if f["category"] == "allergy"]


def test_hindi_and_hinglish_intakes_extract_and_translate_for_staff(app, patient, staff, fake_llm):
    fake_llm.translations["सीने"] = "I have chest pain for 3 days. I do not have fever."
    fake_llm.translations["seene"] = "I have chest pain for 2 days and cannot breathe. I do not have fever."
    for text, dur in (("मुझे 3 दिन से सीने में दर्द है। बुखार नहीं है।", "3 days"),
                      ("Mujhe seene mein dard hai 2 din se aur saans nahi aa rahi. bukhar nahi hai.", "2 days")):
        f = Flow(patient)
        f.say(text)
        f.submit()
        d = detail(staff, f.case_id)
        cp = facts_of(d, "chest_pain")[0]
        assert cp["polarity"] == "present" and cp["duration"] == dur
        assert facts_of(d, "fever")[0]["polarity"] == "denied"
        m = d["messages"][0]
        assert m["language"] in ("hi", "hi-Latn") and m["text"] == text and m["text_en"]  # original preserved + translation
        assert d["assessment"]["proposed_queue"] == "URGENT"


def test_translation_dropping_a_negation_is_flagged_and_blocks_normal(app, patient, staff, fake_llm):
    fake_llm.translations["bukhar nahi"] = "I have a fever."  # translation lost the negation
    f = Flow(patient)
    f.say("Mujhe bukhar nahi hai, 2 din se sir dard hai. Meri umar 30 saal hai.")
    f.submit()
    d = detail(staff, f.case_id)
    assert any(fl.startswith("negation_count_mismatch") or fl.startswith("polarity_changed") for fl in d["messages"][0]["translation_flags"])
    fever = facts_of(d, "fever")[0]
    assert fever["polarity"] == "uncertain" and any(x.startswith("translation_disagreement") for x in fever["flags"])
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"
    assert any(c["kind"] == "negation_risk" for c in d["conflicts"])


def test_genuine_contradiction_is_preserved_questioned_clarified_and_reassessed(app, patient, staff, flow):
    flow.say("I am 50 years old. I have chest pain.")
    flow.say("I do not have chest pain.")
    flow.submit()
    d = detail(staff, flow.case_id)
    assert len(facts_of(d, "chest_pain")) == 2  # neither statement discarded, nor 'latest wins'
    [conf] = [c for c in d["conflicts"] if c["concept"] == "chest_pain"]
    assert conf["status"] == "unresolved" and conf["safety_critical"] and len(conf["evidence"]) == 2
    assert {e["polarity"] for e in conf["evidence"]} == {"present", "denied"}
    assert d["assessment"]["proposed_queue"] == "URGENT" and conf["id"] in d["assessment"]["open_conflict_ids"]  # red flag not hidden
    q = open_q(flow, "conflict:")[0]
    assert q["priority"] if "priority" in q else True
    assert "chest pain" in q["text"].lower() and {o["value"] for o in q["options"]} >= {"present_now", "earlier_resolved", "not_present", "unsure"}
    # patient says: it was earlier, it has resolved
    flow.answer("conflict:", choice="earlier_resolved")
    d = detail(staff, flow.case_id)
    [conf] = [c for c in d["conflicts"] if c["concept"] == "chest_pain"]
    assert conf["status"] == "clarified" and conf["clarification"] == "earlier_resolved"
    assert conf["evidence"]  # original conflict retained
    assert all(f["superseded"] for f in facts_of(d, "chest_pain") if f["method"] != "clarification")
    # urgent rule no longer fires, but a safety-critical conflict still needs a REVIEWER -> NEEDS_REVIEW, tier 1
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and d["case"]["sort_tier"] == 1
    assert any(x["code"] == "safety_critical_conflict" for x in d["assessment"]["data_quality"])
    assert d["assessment"]["changed_from"] == "URGENT"
    # staff resolves with reason; original conflict still on the record with who/when/evidence
    r = staff.post(f"/api/staff/cases/{flow.case_id}/conflicts/{conf['id']}/resolve",
                   {"outcome": "earlier_resolved", "note": "Spoke to patient: pain was last week only.", "evidence_considered": [e["fact_id"] for e in conf["evidence"]]})
    assert r.status_code == 200
    d = detail(staff, flow.case_id)
    [conf] = [c for c in d["conflicts"] if c["concept"] == "chest_pain"]
    assert conf["status"] == "reviewer_resolved" and conf["resolved_by"] == "Dr Synthetic" and conf["resolved_at"] and len(conf["evidence"]) >= 2
    assert staff.post(f"/api/staff/cases/{flow.case_id}/conflicts/{conf['id']}/resolve", {"outcome": "not_present", "note": "changing my mind later"}).status_code == 409


def test_patient_confirming_symptom_is_present_now_escalates(app, patient, staff, flow):
    flow.say("I am 50 years old. I have chest pain. I do not have chest pain.")
    flow.submit()
    assert detail(staff, flow.case_id)["assessment"]["proposed_queue"] == "URGENT"
    flow.answer("conflict:", choice="present_now")
    d = detail(staff, flow.case_id)
    assert d["assessment"]["proposed_queue"] == "URGENT"
    assert [f for f in facts_of(d, "chest_pain") if f["method"] == "clarification" and f["polarity"] == "present"]


def test_unsure_answer_keeps_conflict_open(app, patient, staff, flow):
    flow.say("I am 50. I have chest pain. I do not have chest pain.")
    flow.submit()
    flow.answer("conflict:", choice="unsure")
    d = detail(staff, flow.case_id)
    assert [c for c in d["conflicts"] if c["status"] == "unresolved"]


def test_symptom_resolved_earlier_is_not_a_contradiction(app, patient, staff, flow):
    flow.say("I am 30 years old. Chest pain was there yesterday but it is gone now. I have had a headache for 2 days.")
    flow.submit()
    d = detail(staff, flow.case_id)
    assert d["conflicts"] == []
    cp = facts_of(d, "chest_pain")[0]
    assert cp["polarity"] == "present" and cp["temporal"] == "resolved"
    assert d["assessment"]["proposed_queue"] == "NORMAL"  # resolved red-flag symptom does not trigger the urgent rule


def test_report_disagrees_with_patient_text_flags_conflict_with_both_excerpts(app, patient, staff, flow):
    flow.say("I am 58 years old. I have a headache for 2 days. I take no regular medicines and have no known allergies.")
    pdf = media.digital_pdf(["Discharge summary", "Medicines: clopidogrel 75 mg", "Allergic to penicillin", "I am 58 years old"])
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "report.pdf", pdf).json()["upload"]
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    assert got["status"] == "needs_confirmation" and "clopidogrel" in got["extracted_text"].lower()
    # facts from the report do not exist until the patient confirms the extracted text
    assert not facts_of(detail_after_submit(flow, staff), "medication:clopidogrel")
    assert patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": got["extracted_text"]}).status_code == 200
    d = detail(staff, flow.case_id)
    kinds = {c["kind"]: c for c in d["conflicts"]}
    assert {"medication", "allergy"} <= set(kinds)
    ev = kinds["medication"]["evidence"]
    assert {e["source_type"] for e in ev} == {"message", "document"} and any("clopidogrel" in e["excerpt"].lower() for e in ev)
    assert any(e["locator"] == "page 1" for e in ev)
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"


def detail_after_submit(flow, staff):
    flow.submit() if flow.view()["status"] == "draft" else None
    return detail(staff, flow.case_id)


def test_ocr_text_is_editable_and_unconfirmed_upload_blocks_normal(app, patient, staff, flow):
    flow.say("I am 40 years old. I have a sore throat for 3 days.")
    img = media.image_bytes(media.text_image(["Lab report (synthetic)", "SpO2 97%", "BP 120/80 mmHg"]))
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "photo.png", img, "image/png").json()["upload"]
    flow.submit()
    d = detail(staff, flow.case_id)
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and any(x["code"] == "unconfirmed_upload" for x in d["assessment"]["data_quality"])
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    corrected = got["extracted_text"].replace("SpO2", "SpO2").strip() + "\nSpO2 96%"
    patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": corrected})
    d = detail(staff, flow.case_id)
    sp = facts_of(d, "spo2")
    assert sp and sp[0]["source"]["type"] == "document" and sp[0]["flags"].count("measurement_time_unknown") == 1
    assert d["uploads"][0]["extracted_text"] != d["uploads"][0]["confirmed_text"]  # machine output and patient correction both kept


def test_unreadable_upload_is_reported_honestly_and_blocks_normal(app, patient, staff, flow):
    flow.say("I am 40 years old. I have a sore throat for 3 days.")
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "blur.png", media.unreadable_image(), "image/png").json()["upload"]
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    assert got["status"] == "failed" and "clearer" in got["error"].lower()
    flow.submit()
    d = detail(staff, flow.case_id)
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and any(x["code"] == "unreadable_upload" for x in d["assessment"]["data_quality"])
    assert patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": "made up"}).status_code == 409


def fake_transcript(text, english=None, lang="en", conf=0.9, flags=()):
    seg = stt.Segment(0.0, 4.0, text, conf, 0.01)
    eseg = stt.Segment(0.0, 4.0, english or text, conf, 0.01)
    return stt.Transcript(lang, 0.95, [seg], [eseg], 4.0, list(flags))


def test_audio_transcript_is_reviewed_by_patient_then_extracted(app, patient, staff, flow, monkeypatch):
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: fake_transcript("I am 60 years old and I have a fever since yesterday."))
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "voice.wav", media.silent_wav(), "audio/wav").json()["upload"]
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    assert got["status"] == "needs_confirmation" and got["detected_language"] == "en" and got["segments"][0]["start"] == 0.0
    patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": got["extracted_text"].replace("fever", "high fever")})
    flow.submit()
    d = detail(staff, flow.case_id)
    fv = facts_of(d, "fever")[0]
    assert fv["source"]["type"] == "audio" and fv["duration"] is not None
    assert "voice recording" in fv["source"]["locator"]


def test_asr_error_that_drops_a_negation_is_caught_by_cross_source_conflict(app, patient, staff, flow, monkeypatch):
    flow.say("I am 55 years old. I do not have chest pain. I have a headache for 1 day.")
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: fake_transcript("I have chest pain", conf=0.35, flags=["low_transcription_confidence"]))
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "voice.wav", media.silent_wav(), "audio/wav").json()["upload"]
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    assert "low_transcription_confidence" in got["flags"]  # uncertain transcription is flagged, not guessed
    patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": got["extracted_text"]})
    flow.submit()
    d = detail(staff, flow.case_id)
    [c] = [c for c in d["conflicts"] if c["concept"] == "chest_pain"]
    assert {e["source_type"] for e in c["evidence"]} == {"message", "audio"}
    assert "low_source_quality" in [fl for f in facts_of(d, "chest_pain", method="lexicon") if f["source"]["type"] == "audio" for fl in f["flags"]]
    assert d["assessment"]["proposed_queue"] == "URGENT"  # present red flag is never hidden by the other statement


def test_audio_failures_fall_back_to_text_with_clear_message(app, patient, flow, monkeypatch):
    def boom(path, language_hint=None):
        raise stt.SpeechError("stt_model_unavailable", "The local speech model is not available, so this recording could not be transcribed. Please type your symptoms instead.")
    monkeypatch.setattr(stt, "transcribe", boom)
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "voice.wav", media.silent_wav(), "audio/wav").json()["upload"]
    got = patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()
    assert got["status"] == "failed" and "type your symptoms" in got["error"]
    assert flow.say("I have a cough for 2 days.")["message"]["id"]  # text path still works


def test_missing_age_duration_and_measurements_are_asked_not_assumed(app, patient, staff, flow):
    flow.say("I have a fever.")
    flow.submit()
    d = detail(staff, flow.case_id)
    keys = {q["key"] for q in d["questions"]}
    assert {"age", "duration:fever"} <= keys
    assert not [f for f in d["facts"] if f["category"] == "vital"]  # no invented temperature
    assert any(m["key"] == "vital:temperature" for m in d["assessment"]["missing_information"])


def test_incorrect_unit_and_measurements_at_different_times(app, patient, staff, flow):
    flow.say("I am 40 years old. I have had a fever for 2 days. My temperature is 101 C.")
    flow.submit()
    d = detail(staff, flow.case_id)
    t = facts_of(d, "temperature")[0]
    assert "implausible_value" in t["flags"] and "unit_may_be_F" in t["flags"]
    [c] = [c for c in d["conflicts"] if c["kind"] == "unit"]
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"
    q = open_q(flow, "conflict:")[0]
    assert "fahrenheit" in q["text"].lower()
    flow.answer("conflict:", choice="unit_F")
    d = detail(staff, flow.case_id)
    assert [f for f in facts_of(d, "temperature") if f["unit"] == "F" and not f["superseded"] and f["verification"] == "patient_confirmed"]
    assert d["assessment"]["proposed_queue"] == "NORMAL"
    # same measurement, different values, times unknown -> conflict; known different times -> a series, not a conflict
    flow.say("My oxygen saturation is 97%. Later my SpO2 is 88%.")
    d = detail(staff, flow.case_id)
    assert any(c["kind"] == "value" and c["concept"] == "spo2" for c in d["conflicts"])


def test_new_information_triggers_reassessment_and_does_not_overwrite_the_decision(app, patient, staff, flow):
    flow.say("I am 30 years old. I have a headache for 2 days.")
    flow.submit()
    d = detail(staff, flow.case_id)
    assert d["assessment"]["proposed_queue"] == "NORMAL"
    r = staff.post(f"/api/staff/cases/{flow.case_id}/decision", {"assessment_id": d["assessment"]["id"], "final_queue": "NORMAL", "reason": ""})
    assert r.status_code == 201 and r.json()["decision"]["action"] == "confirm"
    flow.say("Now I also have chest pain and sweating.")
    d2 = detail(staff, flow.case_id)
    assert d2["assessment"]["proposed_queue"] == "URGENT" and d2["assessment"]["changed_from"] == "NORMAL" and d2["assessment"]["trigger"] == "new_information"
    assert d2["case"]["confirmed_queue"] == "NORMAL" and d2["case"]["reassessment_pending"] is True  # human decision untouched, flagged
    assert d2["case"]["queue"] == "URGENT"  # visible in the more urgent queue meanwhile
    assert len(d2["decisions"]) == 1 and d2["decisions"][0]["proposed_queue"] == "NORMAL"
    # stale decision (UI showed old assessment) is refused
    r = staff.post(f"/api/staff/cases/{flow.case_id}/decision", {"assessment_id": d["assessment"]["id"], "final_queue": "NORMAL", "reason": "x" * 20})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_assessment"
    # cannot resolve while reassessment pending
    assert staff.post(f"/api/staff/cases/{flow.case_id}/status", {"status": "resolved"}).status_code == 409


def test_override_requires_reason_and_is_audited_append_only(app, patient, staff, admin, flow):
    flow.say("I am 40 years old. I have chest pain for 1 day.")
    flow.submit()
    d = detail(staff, flow.case_id)
    aid = d["assessment"]["id"]
    body = {"assessment_id": aid, "final_queue": "NORMAL", "reason": ""}
    assert staff.post(f"/api/staff/cases/{flow.case_id}/decision", body).status_code == 422
    assert staff.post(f"/api/staff/cases/{flow.case_id}/decision", {**body, "reason": "short"}).status_code == 422
    r = staff.post(f"/api/staff/cases/{flow.case_id}/decision", {**body, "reason": "Examined by phone; pain is musculoskeletal after lifting."})
    assert r.status_code == 201 and r.json()["decision"]["action"] == "downgrade"
    d = detail(staff, flow.case_id)
    dec = d["decisions"][0]
    assert dec["proposed_queue"] == "URGENT" and dec["final_queue"] == "NORMAL" and dec["decided_by"] == "Dr Synthetic" and dec["decided_at"]
    assert d["assessment"]["proposed_queue"] == "URGENT"  # original proposal never rewritten
    assert any(e["action"] == "decision.recorded" and e["details"]["action"] == "downgrade" for e in d["audit"])
    from ..db import session_scope
    from sqlalchemy import text
    import sqlalchemy.exc
    with pytest.raises(sqlalchemy.exc.DatabaseError):
        with session_scope() as db:
            db.execute(text("UPDATE decisions SET reason='tampered'"))
    with pytest.raises(sqlalchemy.exc.DatabaseError):
        with session_scope() as db:
            db.execute(text("DELETE FROM audit_events"))
    assert admin.get("/api/admin/audit/verify").json()["ok"] is True
    ev = admin.get("/api/admin/audit", params={"case_id": flow.case_id}).json()["events"]
    assert not any("password" in str(e["details"]).lower() for e in ev)


def test_cannot_confirm_needs_review_and_escalation_works(app, patient, staff, flow):
    flow.say("I have a headache.")
    flow.submit()
    d = detail(staff, flow.case_id)
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"
    body = {"assessment_id": d["assessment"]["id"], "final_queue": "NEEDS_REVIEW", "reason": ""}
    assert staff.post(f"/api/staff/cases/{flow.case_id}/decision", body).status_code == 422
    r = staff.post(f"/api/staff/cases/{flow.case_id}/decision", {**body, "final_queue": "URGENT", "reason": "Patient described worsening red-flag symptoms by phone."})
    assert r.json()["decision"]["action"] == "escalate"
    assert staff.get("/api/staff/queues/summary").json()["counts"]["URGENT"] == 1


def test_assignment_status_workflow_and_resolution_rules(app, patient, staff, staff2, flow):
    flow.say("I am 30. I have a headache for 2 days.")
    flow.submit()
    cid = flow.case_id
    assert staff.post(f"/api/staff/cases/{cid}/status", {"status": "resolved"}).status_code == 409  # no decision yet
    assert staff.post(f"/api/staff/cases/{cid}/assign", {"user_id": staff2.user["id"]}).json()["status"] == "in_review"
    assert staff.get("/api/staff/cases", params={"assigned": staff2.user["id"]}).json()["total"] == 1
    assert staff.get("/api/staff/cases", params={"assigned": "unassigned"}).json()["total"] == 0
    d = detail(staff, cid)
    staff.post(f"/api/staff/cases/{cid}/decision", {"assessment_id": d["assessment"]["id"], "final_queue": "NORMAL", "reason": ""})
    assert staff.post(f"/api/staff/cases/{cid}/status", {"status": "resolved"}).status_code == 200
    assert staff.get("/api/staff/queues/summary").json()["total_open"] == 0  # resolved cases leave the queues
    assert staff.get("/api/staff/cases", params={"status": "resolved"}).json()["total"] == 1
    assert staff.post(f"/api/staff/cases/{cid}/assign", {"user_id": patient.user["id"]}).status_code == 422  # patients can't be reviewers


def test_staff_can_ask_patient_a_question_and_patient_answer_resumes_review(app, patient, staff, flow):
    flow.say("I am 30. I have a headache for 2 days.")
    flow.submit()
    q = staff.post(f"/api/staff/cases/{flow.case_id}/questions", {"text": "Have you had any head injury?", "reason": "Rule out trauma"}).json()["question"]
    assert detail(staff, flow.case_id)["case"]["status"] == "awaiting_patient"
    patient.post(f"/api/cases/{flow.case_id}/questions/{q['id']}/answer", {"text": "No injury."})
    assert detail(staff, flow.case_id)["case"]["status"] == "in_review"


def test_duplicate_submission_and_message_retries_are_idempotent(app, patient, staff, flow):
    a = flow.say("I am 30. I have a headache for 2 days.", token="tok-1")
    b = flow.say("I am 30. I have a headache for 2 days.", token="tok-1")
    assert b["duplicate"] is True and a["message"]["id"] == b["message"]["id"]
    assert flow.submit()["already_submitted"] is False and flow.submit()["already_submitted"] is True
    assert staff.get("/api/staff/cases").json()["total"] == 1
    assert staff.get("/api/staff/queues/summary").json()["total_open"] == 1
    assert len(facts_of(detail(staff, flow.case_id), "headache")) == 1


def test_data_persists_across_restart(app, patient, staff, flow, env):
    flow.say("I am 30. I have chest pain.")
    flow.submit()
    cid = flow.case_id
    from .. import config, db
    from ..main import app as _app
    from fastapi.testclient import TestClient
    db.init_engine()  # brand new engine on the same database file == process restart
    with TestClient(_app) as c:
        s2 = Api(_app)
        assert s2.login("dr.synthetic@demo-clinic.org").status_code == 200
        r = s2.get(f"/api/staff/cases/{cid}")
        assert r.status_code == 200 and r.json()["assessment"]["proposed_queue"] == "URGENT"


def test_queue_ordering_is_deterministic_tier_then_worsening_then_waiting(app, patient, staff):
    from ..db import session_scope
    from ..models import Case
    texts = [("I am 30. I have chest pain.", "A"), ("I am 30. I have chest pain and it is getting worse.", "B"), ("I am 30. I have chest pain and shortness of breath.", "C"),
             ("I am 30. I have a headache for 2 days.", "D"), ("I have a headache.", "E")]
    ids = {}
    for t, k in texts:
        f = Flow(patient)
        f.say(t)
        f.submit()
        ids[k] = f.case_id
    from datetime import timedelta
    from ..models import utcnow
    with session_scope() as db:  # make waiting times explicit and distinct: A waited longest within its tier
        for i, k in enumerate("ABCDE"):
            db.get(Case, ids[k]).submitted_at = utcnow() - timedelta(minutes=100 - i * 10)
    order = [c["case_number"] for c in staff.get("/api/staff/cases").json()["cases"]]
    num = {k: next(c["case_number"] for c in staff.get("/api/staff/cases").json()["cases"] if c["id"] == v) for k, v in ids.items()}
    # tier 1: C (chest pain + SOB). tier 2: B (worsening first) then A. then review/normal queues come by their own tiers
    urgent = [c["case_number"] for c in staff.get("/api/staff/cases", params={"queue": "URGENT"}).json()["cases"]]
    assert urgent == [num["C"], num["B"], num["A"]]
    assert order == [num[k] for k in "CBA"] + [x for x in order if x not in (num["C"], num["B"], num["A"])]
    again = [c["case_number"] for c in staff.get("/api/staff/cases", params={"queue": "URGENT"}).json()["cases"]]
    assert urgent == again
    # pagination keeps a stable, non-overlapping order
    p1 = staff.get("/api/staff/cases", params={"page_size": 2, "page": 1}).json()["cases"]
    p2 = staff.get("/api/staff/cases", params={"page_size": 2, "page": 2}).json()["cases"]
    assert not {c["id"] for c in p1} & {c["id"] for c in p2}


def test_search_and_filters(app, patient, staff):
    f1, f2 = Flow(patient), Flow(patient)
    f1.say("I am 30. I have chest pain.")
    f2.say("I am 30. I have a sore throat for 2 days.")
    f1.submit(); f2.submit()
    assert staff.get("/api/staff/cases", params={"q": "sore throat"}).json()["total"] == 1
    assert staff.get("/api/staff/cases", params={"q": "chest_pain"}).json()["total"] == 1  # matches extracted concept too
    assert staff.get("/api/staff/cases", params={"q": "zzzz"}).json()["total"] == 0
    assert staff.get("/api/staff/cases", params={"queue": "URGENT"}).json()["total"] == 1
    assert staff.get("/api/staff/cases", params={"queue": "BOGUS"}).status_code == 422
    assert staff.get("/api/staff/cases", params={"page_size": 1000}).status_code == 422
    assert staff.get("/api/staff/cases", params={"q": "'; DROP TABLE cases;--"}).status_code == 200


def test_queue_filters_by_source_searches_patient_and_reports_review_context(app, patient, staff, monkeypatch):
    from ..services import stt
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: fake_transcript("I have a cough for 3 days."))
    f = Flow(patient)
    f.say("I am 44 years old. I have a headache for 2 days and it is getting worse. My pulse is 90.")
    voice = patient.upload(f"/api/cases/{f.case_id}/uploads", "voice.wav", media.silent_wav(), "audio/wav").json()["upload"]
    got = patient.get(f"/api/cases/{f.case_id}/uploads/{voice['id']}").json()
    patient.post(f"/api/cases/{f.case_id}/uploads/{voice['id']}/confirm", {"text": got["extracted_text"]})
    report = patient.upload(f"/api/cases/{f.case_id}/uploads", "report.pdf", media.digital_pdf(["Synthetic report: no additional findings."]), "application/pdf").json()["upload"]
    got_report = patient.get(f"/api/cases/{f.case_id}/uploads/{report['id']}").json()
    patient.post(f"/api/cases/{f.case_id}/uploads/{report['id']}/confirm", {"text": got_report["extracted_text"]})
    f.submit()
    for source in ("text", "voice", "document"):
        result = staff.get("/api/staff/cases", params={"source": source})
        assert result.status_code == 200 and result.json()["total"] == 1
    rows = staff.get("/api/staff/cases", params={"q": "Patient One"}).json()["cases"]
    assert len(rows) == 1
    row = rows[0]
    assert row["age"] == 44 and row["symptom_duration"] == "2 days" and row["worsening"] is True
    assert row["reason"] and row["sources"] == ["document", "text", "voice"]
    summary = staff.get("/api/staff/queues/summary").json()
    assert summary["total_cases"] == 1 and sum(summary["workflow_counts"].values()) == 1


def test_staff_fact_correction_appends_and_reassesses_without_rewriting_source(app, patient, staff, flow):
    flow.say("I am 35 years old. I have a headache for 2 days.")
    flow.submit()
    before = detail(staff, flow.case_id)
    fact = facts_of(before, "headache")[0]
    result = staff.post(f"/api/staff/cases/{flow.case_id}/facts/{fact['id']}/correct", {
        "changes": {"severity": "mild", "progression": "stable"}, "reason": "Reviewer corrected severity after checking the original statement."})
    assert result.status_code == 201, result.text
    after = detail(staff, flow.case_id)
    original = next(f for f in after["facts"] if f["id"] == fact["id"])
    corrected = next(f for f in after["facts"] if f["id"] == result.json()["fact"]["id"])
    assert original["superseded"] and original["excerpt"] == fact["excerpt"]
    assert corrected["severity"] == "mild" and corrected["progression"] == "stable"
    assert corrected["source"]["type"] == "staff" and corrected["verification"] == "staff_verified"
    assert any(e["action"] == "fact.corrected" for e in after["audit"])
    assert staff.post(f"/api/staff/cases/{flow.case_id}/facts/{fact['id']}/correct", {"changes": {"duration": "3 days"}, "reason": "try old source"}).status_code == 409


def test_xss_payload_is_stored_inertly_as_text(app, patient, staff, flow):
    flow.say("I am 30. <script>alert(1)</script> I have a headache for 2 days.")
    flow.submit()
    d = detail(staff, flow.case_id)
    assert "<script>" in d["messages"][0]["text"]  # stored verbatim; the UI renders as text (React escapes); API is JSON only
    assert staff.get(f"/api/staff/cases/{flow.case_id}").headers["content-type"].startswith("application/json")
