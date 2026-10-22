"""Synthetic acceptance matrix (addendum F). Every scenario is driven through the real HTTP API and then checked for:
structured facts, preserved evidence, uncertainty/conflict flags, follow-up questions, proposed routing, audit history
and the final reviewer workflow (human decision + clinician-approved note). Expected outcomes come from the scenario
fixtures and the DEMONSTRATION ruleset - not clinical ground truth."""
import pytest

from ..demo.scenarios import SCENARIOS, finish_review, run_scenario
from ..services import stt
from . import media


def _available(needs) -> bool:
    if "tts" in needs and not media.have_tts():
        return False
    if "whisper" in needs and not stt.status()["available"]:
        return False
    return True


def _match(f, spec):
    return all(f.get(k) == v for k, v in spec.items() if k != "source_type") and ("source_type" not in spec or f["source"]["type"] == spec["source_type"])


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_scenario(scn, app, patient, staff, tmp_path):
    if not _available(scn.needs):
        pytest.skip(f"requires {scn.needs} (local speech model / macOS TTS not available)")
    out = run_scenario(patient, staff, scn, tmp_path)
    d, cid = out["detail"], out["case_id"]

    # routing
    assert d["assessment"]["proposed_queue"] == scn.queue, (d["assessment"]["summary"], d["assessment"]["data_quality"])
    assert d["assessment"]["ruleset_version"] and d["assessment"]["demonstration_only"] is True
    for rid in scn.rules:
        assert rid in {r["rule_id"] for r in d["assessment"]["triggered_rules"]}
    # structured facts with provenance
    for spec in scn.facts:
        assert any(_match(f, spec) for f in d["facts"]), (spec, [(f["concept"], f["polarity"], f["temporal"], f["source"]["type"]) for f in d["facts"]])
    for f in d["facts"]:
        assert f["source"]["type"] and f["source"]["id"] and f["origin"] in ("explicit", "inferred") and f["excerpt"] is not None
        assert f["verification"] in ("unverified", "patient_confirmed", "staff_verified", "rejected")
    # preserved evidence: originals are never deleted by clarification, resolution or reassessment
    for m in d["messages"]:
        assert m["text"]
    for c in d["conflicts"]:
        assert c["evidence"] and c["description"]
    # conflicts
    kinds = {c["kind"] for c in d["conflicts"]}
    for k in scn.conflicts:
        assert k in kinds, (k, kinds)
    if not scn.conflicts and scn.queue == "NORMAL":
        assert not d["conflicts"]
    # follow-up questions
    for prefix in scn.questions:
        assert any(k.startswith(prefix) for k in out["asked"]), (prefix, out["asked"])
    assert all(q["reason"] for q in d["questions"])
    # uncertainty / data-quality flags
    haystack = " ".join(
        [fl for m in d["messages"] for fl in m["translation_flags"]] + [fl for u in d["uploads"] for fl in u["flags"]] + [fl for f in d["facts"] for fl in f["flags"]]
        + [x["code"] for x in d["assessment"]["data_quality"]])
    for frag in scn.flags:
        assert frag in haystack, (frag, haystack)
    # audit trail of the intake itself
    actions = [e["action"] for e in d["audit"]]
    assert "case.submitted" in actions and "assessment.created" in actions

    # final reviewer workflow
    done = finish_review(staff, cid)
    dec = done["decisions"][0]
    assert dec["decided_by"] == "Dr Synthetic" and dec["proposed_queue"] == scn.queue and dec["decided_at"] and dec["reason"]
    note = done["notes"][0]
    assert note["status"] == "approved" and note["reviewed_by"] and note["reviewed_at"] and note["label"] == "Approved by clinician"
    assert note["content"]["meta"]["assessment_id"] and note["content"]["reviewer_comments"] and note["content"]["final_disposition"]
    assert note["change_summary"] and note["change_summary"][0]["section"] == "reviewer_comments"
    actions = [e["action"] for e in done["audit"]]
    assert {"decision.recorded", "note.draft_generated", "note.edited", "note.approved"} <= set(actions)
    assert done["case"]["confirmed_queue"] and done["case"]["confirmed_by"] == "Dr Synthetic"


def test_unauthorised_user_cannot_reach_another_patients_record_acceptance(app, patient, patient2, staff, tmp_path):
    out = run_scenario(patient, staff, SCENARIOS[0], tmp_path)
    cid = out["case_id"]
    for method, url in (("get", f"/api/cases/{cid}"), ("get", f"/api/staff/cases/{cid}"), ("post", f"/api/cases/{cid}/submit"), ("get", "/api/staff/queues/summary")):
        r = getattr(patient2, method)(url)
        assert r.status_code in (403, 404), (url, r.status_code)
        assert cid not in r.text
    events = [e["action"] for e in staff.get(f"/api/staff/cases/{cid}").json()["audit"]]
    assert "case.viewed" in events  # legitimate staff access is itself audited


def test_note_cannot_be_approved_without_human_decision_or_after_new_evidence(app, patient, staff, tmp_path):
    out = run_scenario(patient, staff, SCENARIOS[0], tmp_path)
    cid = out["case_id"]
    n = staff.post(f"/api/staff/cases/{cid}/notes/draft").json()
    assert n["status"] == "draft" and n["label"].startswith("AI-assisted DRAFT") and "not clinician-approved" in n["content"]["disclaimer"]
    r = staff.post(f"/api/staff/cases/{cid}/notes/{n['id']}/approve", {"reason": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "decision_required"
    d = staff.get(f"/api/staff/cases/{cid}").json()
    staff.post(f"/api/staff/cases/{cid}/decision", {"assessment_id": d["assessment"]["id"], "final_queue": "URGENT", "reason": ""})
    patient.post(f"/api/cases/{cid}/messages", {"text": "I also have a headache and vomiting."})
    r = staff.post(f"/api/staff/cases/{cid}/notes/{n['id']}/approve", {"reason": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] in ("note_stale", "reassessment_pending")
    # reject needs a reason; rejected drafts stay on record; a regenerated draft supersedes
    assert staff.post(f"/api/staff/cases/{cid}/notes/{n['id']}/reject", {"reason": "no"}).status_code == 422
    assert staff.post(f"/api/staff/cases/{cid}/notes/{n['id']}/reject", {"reason": "Draft omits the new vomiting report."}).json()["status"] == "rejected"
    n2 = staff.post(f"/api/staff/cases/{cid}/notes/draft").json()
    assert n2["version"] == 2 and n2["stale"] is False
    assert staff.put(f"/api/staff/cases/{cid}/notes/{n['id']}", {"changes": {"reviewer_comments": "x"}}).status_code == 409  # rejected note is locked
    assert staff.put(f"/api/staff/cases/{cid}/notes/{n2['id']}", {"changes": {"disclaimer": "removed"}}).status_code == 422  # disclaimer not editable


def test_approved_note_is_immutable_and_text_export_is_labelled(app, patient, staff, tmp_path):
    out = run_scenario(patient, staff, SCENARIOS[0], tmp_path)
    done = finish_review(staff, out["case_id"])
    n = done["notes"][0]
    assert staff.put(f"/api/staff/cases/{out['case_id']}/notes/{n['id']}", {"changes": {"reviewer_comments": "tamper"}}).status_code == 409
    txt = staff.get(f"/api/staff/cases/{out['case_id']}/notes/{n['id']}/text").json()["text"]
    assert "APPROVED" in txt and "Approved by clinician id" in txt
    draft = staff.post(f"/api/staff/cases/{out['case_id']}/notes/draft").json()
    assert "DRAFT (not approved)" in staff.get(f"/api/staff/cases/{out['case_id']}/notes/{draft['id']}/text").json()["text"]


def test_g_end_to_end_demonstration_text_audio_report_contradiction_followup_reassessment_note(app, patient, staff, tmp_path, monkeypatch):
    """Addendum G: one patient submission with typed text + an audio file + a medical report; extracted facts and original evidence;
    a genuine contradiction; a follow-up question asked and answered; assessment updated; correct staff queue; human-approved note."""
    from ..demo.scenarios import BUILDERS
    if not (media.have_tts() and stt.status()["available"]):  # keep the test meaningful on machines without speech tooling
        monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: stt.Transcript(
            "en", 0.95, [stt.Segment(0, 5, "I have chest pain for two days and I feel short of breath.", 0.9, 0.01)],
            [stt.Segment(0, 5, "I have chest pain for two days and I feel short of breath.", 0.9, 0.01)], 5.0, []))
        audio = media.silent_wav(5.0)
    else:
        audio = BUILDERS["wav_english"](tmp_path)
    cid = patient.post("/api/cases").json()["id"]
    patient.post(f"/api/cases/{cid}/messages", {"text": "I am 58 years old. I do not have chest pain. I have a headache for 2 days. I take no regular medicines and have no known allergies."})
    up_a = patient.upload(f"/api/cases/{cid}/uploads", "voice.wav", audio, "audio/wav").json()["upload"]
    got_a = patient.get(f"/api/cases/{cid}/uploads/{up_a['id']}").json()
    assert got_a["status"] == "needs_confirmation" and got_a["segments"] and got_a["detected_language"]
    patient.post(f"/api/cases/{cid}/uploads/{up_a['id']}/confirm", {"text": "I have chest pain for two days and I feel short of breath."})  # patient reviews/corrects the transcript
    up_r = patient.upload(f"/api/cases/{cid}/uploads", "report.pdf", BUILDERS["pdf_conflicting"](tmp_path), "application/pdf").json()["upload"]
    got_r = patient.get(f"/api/cases/{cid}/uploads/{up_r['id']}").json()
    assert "clopidogrel" in got_r["extracted_text"].lower()
    patient.post(f"/api/cases/{cid}/uploads/{up_r['id']}/confirm", {"text": got_r["extracted_text"]})
    patient.post(f"/api/cases/{cid}/submit")

    d1 = staff.get(f"/api/staff/cases/{cid}").json()
    sources = {f["source"]["type"] for f in d1["facts"] if f["concept"] == "chest_pain"}
    assert sources == {"message", "audio", "document"}  # facts from all three inputs, original excerpts preserved
    assert all(f["excerpt"] for f in d1["facts"] if f["concept"] == "chest_pain")
    kinds = {c["kind"] for c in d1["conflicts"]}
    assert {"presence", "medication", "allergy"} <= kinds
    assert d1["assessment"]["proposed_queue"] == "URGENT" and staff.get("/api/staff/queues/summary").json()["counts"]["URGENT"] == 1
    first_assessment, open_before = d1["assessment"]["id"], len(d1["assessment"]["open_conflict_ids"])

    # the patient answers the follow-up questions raised by the conflicts
    qs = [q for q in patient.get(f"/api/cases/{cid}").json()["questions"] if q["status"] == "open"]
    assert any(q["key"].startswith("conflict:") and "chest pain" in q["text"].lower() for q in qs)
    for q in qs:
        if q["key"].startswith("conflict:"):
            choice = "present_now" if any(o["value"] == "present_now" for o in q["options"]) else q["options"][0]["value"]
            assert patient.post(f"/api/cases/{cid}/questions/{q['id']}/answer", {"text": "", "choice": choice}).status_code == 200
    d2 = staff.get(f"/api/staff/cases/{cid}").json()
    assert d2["assessment"]["id"] != first_assessment and d2["assessment"]["trigger"] == "clarification"  # reassessed
    assert len(d2["assessment"]["open_conflict_ids"]) < open_before
    assert d2["assessment"]["proposed_queue"] == "URGENT"
    assert any(f["method"] == "clarification" and f["verification"] == "patient_confirmed" for f in d2["facts"])

    # human review: resolve the safety-critical conflict, decide, edit and approve the note
    chest = next(c for c in d2["conflicts"] if c["concept"] == "chest_pain" and c["status"] != "reviewer_resolved")
    assert staff.post(f"/api/staff/cases/{cid}/conflicts/{chest['id']}/resolve", {"outcome": "present_now", "note": "Patient confirmed ongoing chest pain; typed denial was an error.",
                                                                                 "evidence_considered": [e["fact_id"] for e in chest["evidence"] if "fact_id" in e]}).status_code == 200
    done = finish_review(staff, cid)
    assert done["case"]["queue"] == "URGENT" and done["notes"][0]["status"] == "approved" and done["decisions"][0]["decided_by"] == "Dr Synthetic"
    assert [c["status"] for c in done["conflicts"] if c["concept"] == "chest_pain"] == ["reviewer_resolved"]
    assert len({e["action"] for e in done["audit"]} & {"case.submitted", "assessment.created", "question.answered", "conflict.resolved", "decision.recorded", "note.approved"}) == 6
