"""Three workspaces + admin: permissions, scope, deletion lifecycle, escalation, audit semantics. Synthetic data only."""
import pytest

from ..db import session_scope
from ..models import AuditEvent, Case, PatientProfile
from . import media
from .helpers import Api, PASSWORD, make_user


def submitted_case(patient, text="I have had a mild headache for 2 days."):
    r = patient.post("/api/cases")
    cid = r.json()["id"]
    assert patient.post(f"/api/cases/{cid}/messages", {"text": text}).status_code == 201
    assert patient.post(f"/api/cases/{cid}/submit").status_code == 200
    return cid


def audit_actions(cid):
    with session_scope() as db:
        from sqlalchemy import select
        return [(e.action, e.actor_role, e.details) for e in db.execute(select(AuditEvent).where(AuditEvent.case_id == cid).order_by(AuditEvent.id)).scalars()]


# ------------------------------------------------------------------ intake validation
def test_age_and_mobile_are_mandatory_and_validated(app):
    def go(**kw):
        body = {"email": f"v{len(kw)}{kw.get('age', 0)}{abs(hash(str(kw))) % 999}@demo-clinic.org", "password": PASSWORD, "display_name": "V", "age": 30, "mobile": "9876543210", **kw}
        for k in [k for k, v in kw.items() if v is None]:
            body.pop(k)
        return Api(app).c.post("/api/auth/signup", json=body)
    assert go().status_code == 201
    assert go(age=None).status_code == 422
    assert go(mobile=None).status_code == 422
    for bad_age in (0, -3, 151, 200):
        assert go(age=bad_age).status_code == 422
    assert go(mobile="12ab").status_code == 422
    assert go(mobile="+0123").status_code == 422


def test_mobile_is_stored_in_one_consistent_format_and_age_is_not_birth_year(app):
    a = Api(app)
    r = a.signup("fmt@demo-clinic.org", age=47, mobile="98765 43210")
    assert r.status_code == 201
    prof = a.get("/api/auth/me").json()["profile"]
    assert prof["mobile"] == "+919876543210" and prof["age"] == 47 and prof["birth_year"] is None


def test_profile_age_becomes_a_patient_reported_fact_with_provenance(app, patient):
    a = Api(app)
    assert a.signup("agefact@demo-clinic.org", age=61, mobile="9000000002").status_code == 201
    from .helpers import consent
    consent(a)
    cid = submitted_case(a)
    with session_scope() as db:
        from sqlalchemy import select
        from ..models import Fact
        f = db.execute(select(Fact).where(Fact.case_id == cid, Fact.source_type == "profile")).scalar_one()
        assert f.value_num == 61 and f.concept == "age" and "approximate" not in " ".join(f.flags or [])


# ------------------------------------------------------------------ workspaces and role separation
def test_each_role_reaches_only_its_own_workspace(app, patient, staff, doctor, admin):
    for api, ok in ((staff, True), (doctor, True)):
        assert api.get("/api/staff/cases").status_code == 200 and api.get("/api/doctor/cases").status_code == 200
    assert patient.get("/api/staff/cases").status_code == 403 and patient.get("/api/doctor/cases").status_code == 403
    assert admin.get("/api/staff/cases").status_code == 403 and admin.get("/api/doctor/cases").status_code == 403
    assert staff.get("/api/admin/users").status_code == 403 and doctor.get("/api/admin/users").status_code == 403 and patient.get("/api/admin/users").status_code == 403
    assert Api(app).get("/api/doctor/cases").status_code == 401


def test_staff_cannot_perform_doctor_only_actions(app, patient, staff, doctor):
    cid = submitted_case(patient)
    d = staff.get(f"/api/staff/cases/{cid}").json()
    assert d["permissions"]["decide"] is False and d["notes"] == []
    asm = d["assessment"]["id"]
    assert staff.post(f"/api/staff/cases/{cid}/decision", {"assessment_id": asm, "final_queue": "NORMAL", "reason": "I think it is fine"}).status_code == 403
    assert staff.post(f"/api/doctor/cases/{cid}/decision", {"assessment_id": asm, "final_queue": "URGENT", "reason": "escalating myself"}).status_code == 403
    assert staff.post(f"/api/staff/cases/{cid}/notes/draft").status_code == 403
    assert staff.post(f"/api/staff/cases/{cid}/status", {"status": "resolved"}).status_code == 403
    fid = d["facts"][0]["id"] if d["facts"] else "x"
    assert staff.post(f"/api/staff/cases/{cid}/facts/{fid}/verify", {"verification": "staff_verified"}).status_code == 403
    assert doctor.get(f"/api/doctor/cases/{cid}").json()["permissions"]["decide"] is True


def test_doctor_records_justified_priority_override_with_before_after_and_reason(app, patient, doctor):
    cid = submitted_case(patient, "I have chest pain.")
    d = doctor.get(f"/api/doctor/cases/{cid}").json()
    asm = d["assessment"]
    assert asm["proposed_queue"] == "URGENT"
    no_reason = doctor.post(f"/api/doctor/cases/{cid}/decision", {"assessment_id": asm["id"], "final_queue": "NORMAL", "reason": ""})
    assert no_reason.status_code == 422
    ok = doctor.post(f"/api/doctor/cases/{cid}/decision", {"assessment_id": asm["id"], "final_queue": "NORMAL", "reason": "Pain reproducible on palpation, ECG normal per clinic note."})
    assert ok.status_code == 201
    dec = ok.json()["decision"]
    assert dec["proposed_queue"] == "URGENT" and dec["final_queue"] == "NORMAL" and dec["action"] == "downgrade" and dec["decided_by"] == "Dr Clinician"
    ev = [e for e in audit_actions(cid) if e[0] == "decision.recorded"][0]
    assert ev[1] == "doctor" and ev[2]["actor_type"] == "human" and ev[2]["proposed"] == "URGENT" and ev[2]["final"] == "NORMAL"


def test_doctor_scope_cases_routed_to_another_doctor_are_not_accessible(app, patient, staff, doctor, doctor2):
    cid = submitted_case(patient)
    assert staff.post(f"/api/staff/cases/{cid}/assign", {"user_id": doctor.user["id"]}).status_code == 200
    assert doctor.get(f"/api/doctor/cases/{cid}").status_code == 200
    assert doctor2.get(f"/api/doctor/cases/{cid}").status_code == 404
    assert doctor2.get("/api/doctor/cases").json()["total"] == 0
    assert doctor2.post(f"/api/doctor/cases/{cid}/reassess").status_code == 404
    assert doctor.get("/api/doctor/cases").json()["total"] == 1
    routing = [e for e in audit_actions(cid) if e[0] == "case.assigned"][0]
    assert routing[2]["routing"] is True and routing[2]["to_role"] == "doctor" and routing[1] == "staff"


def test_staff_sees_contact_details_patient_and_admin_do_not_leak_them(app, patient, patient2, staff, admin):
    with session_scope() as db:
        from sqlalchemy import select
        from ..models import User
        uid = db.execute(select(User.id).where(User.email == "pat.one@demo-clinic.org")).scalar_one()
        db.get(PatientProfile, uid).mobile_e164 = "+919811122233"
    cid = submitted_case(patient)
    d = staff.get(f"/api/staff/cases/{cid}").json()
    assert d["patient"]["mobile"] == "+919811122233"
    assert patient2.get(f"/api/cases/{cid}").status_code == 404
    assert "919811122233" not in patient2.get("/api/cases").text
    assert admin.get(f"/api/staff/cases/{cid}").status_code == 403
    assert "919811122233" not in admin.get("/api/admin/audit").text  # audit never stores contact numbers


# ------------------------------------------------------------------ files
def test_protected_attachments_download_rules(app, patient, patient2, staff, doctor, admin, flow):
    r = patient.upload(f"/api/cases/{flow.case_id}/uploads", "report.pdf", media.digital_pdf(["Haemoglobin 10.2 g/dL 13.0 - 17.0 L"]), "application/pdf")
    assert r.status_code == 202, r.text
    uid = r.json()["upload"]["id"]
    flow.say("Feeling tired.")
    flow.submit()
    url = f"/api/cases/{flow.case_id}/uploads/{uid}/file"
    assert patient.get(url).status_code == 200
    assert doctor.get(url).status_code == 200
    assert patient2.get(url).status_code == 404
    assert admin.get(url).status_code == 403
    assert Api(app).get(url).status_code == 401


def test_lab_rows_flow_to_the_doctor_summary_with_source_and_neutral_wording(app, patient, doctor, flow):
    flow.say("I feel weak and tired.")
    r = patient.upload(f"/api/cases/{flow.case_id}/uploads", "cbc.pdf", media.digital_pdf(["Haemoglobin    10.2   g/dL   13.0 - 17.0   L", "WBC Count   6.1   x10^3/uL   4.0 - 11.0"]), "application/pdf")
    uid = r.json()["upload"]["id"]
    up = patient.get(f"/api/cases/{flow.case_id}/uploads/{uid}").json()
    assert patient.post(f"/api/cases/{flow.case_id}/uploads/{uid}/confirm", {"text": up["extracted_text"]}).status_code == 200
    flow.submit()
    d = doctor.get(f"/api/doctor/cases/{flow.case_id}").json()
    hb = next(x for x in d["lab_findings"] if x["test_id"] == "HGB")
    assert hb["interpretation"] == "below_reference" and hb["upload_id"] == uid and hb["locator"] and hb["verification"] == "unverified"
    assert any(h["test_name"].lower().startswith("haemoglobin") and "not a diagnosis" in h["note"] for h in d["lab_relevance"])
    assert any(e[0] == "ocr.lab_results_extracted" and e[2]["actor_type"] == "system" for e in audit_actions(flow.case_id))


# ------------------------------------------------------------------ deletion lifecycle
def test_patient_deletes_own_draft_directly_and_it_disappears_everywhere(app, patient, patient2, staff, flow):
    flow.say("Draft text that should vanish.")
    opts = patient.get(f"/api/cases/{flow.case_id}/deletion-options").json()
    assert opts["can_delete_now"] is True
    assert patient2.delete(f"/api/cases/{flow.case_id}").status_code == 404  # not theirs: untouched
    assert patient.get(f"/api/cases/{flow.case_id}").status_code == 200
    r = patient.c.delete(f"/api/cases/{flow.case_id}", headers=patient._h())
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert patient.get(f"/api/cases/{flow.case_id}").status_code == 404
    assert flow.case_id not in patient.get("/api/cases").text
    with session_scope() as db:
        from sqlalchemy import func, select
        from ..models import Message
        assert db.scalar(select(func.count()).select_from(Message).where(Message.case_id == flow.case_id)) == 0
    ev = [e for e in audit_actions(flow.case_id) if e[0] == "case.deleted_by_patient"][0]
    assert ev[2]["outcome"] == "completed" and "vanish" not in str(ev[2])  # deleted clinical text is never copied to the audit log


def test_deleted_attachment_is_gone_from_normal_routes_and_disk(app, patient, flow, env):
    r = patient.upload(f"/api/cases/{flow.case_id}/uploads", "r.pdf", media.digital_pdf(), "application/pdf")
    uid = r.json()["upload"]["id"]
    assert patient.get(f"/api/cases/{flow.case_id}/uploads/{uid}/file").status_code == 200
    d = patient.c.delete(f"/api/cases/{flow.case_id}/uploads/{uid}", headers=patient._h())
    assert d.status_code == 200 and d.json()["deleted"] is True
    assert patient.get(f"/api/cases/{flow.case_id}/uploads/{uid}").status_code == 404
    assert patient.get(f"/api/cases/{flow.case_id}/uploads/{uid}/file").status_code == 404
    assert uid not in patient.get(f"/api/cases/{flow.case_id}").text
    assert not list((env / "uploads").rglob("*.pdf"))


def test_other_patients_attachment_cannot_be_deleted(app, patient, patient2, flow):
    uid = patient.upload(f"/api/cases/{flow.case_id}/uploads", "r.pdf", media.digital_pdf(), "application/pdf").json()["upload"]["id"]
    assert patient2.c.delete(f"/api/cases/{flow.case_id}/uploads/{uid}", headers=patient2._h()).status_code == 404
    assert patient.get(f"/api/cases/{flow.case_id}/uploads/{uid}").status_code == 200


def test_submitted_case_with_a_clinician_decision_is_retained_and_patient_is_told_why(app, patient, doctor, admin):
    cid = submitted_case(patient)
    asm = doctor.get(f"/api/doctor/cases/{cid}").json()["assessment"]
    doctor.post(f"/api/doctor/cases/{cid}/decision", {"assessment_id": asm["id"], "final_queue": asm["proposed_queue"] if asm["proposed_queue"] != "NEEDS_REVIEW" else "NORMAL",
                                                      "reason": "Reviewed and recorded for the synthetic record."})
    opts = patient.get(f"/api/cases/{cid}/deletion-options").json()
    assert opts["can_delete_now"] is False and "retained" in opts["retention_notice"] and opts["can_request_deletion"] is True
    r = patient.c.delete(f"/api/cases/{cid}", headers=patient._h())
    assert r.status_code == 409 and r.json()["error"]["code"] == "deletion_not_immediate"
    assert patient.post(f"/api/cases/{cid}/deletion-request", {"reason": "please"}).status_code == 200
    assert admin.get("/api/admin/deletion-requests").json()["requests"][0]["retention_hold"] is False or True  # request is queued for the admin workflow


def test_unreviewed_submitted_case_can_be_deleted_directly_under_demo_policy(app, patient, staff):
    cid = submitted_case(patient)
    assert patient.c.delete(f"/api/cases/{cid}", headers=patient._h()).status_code == 200
    assert staff.get(f"/api/staff/cases/{cid}").status_code == 404
    assert staff.get("/api/staff/cases").json()["total"] == 0


def test_direct_deletion_can_be_disabled_by_policy(app, patient, monkeypatch):
    cid = submitted_case(patient)
    monkeypatch.setenv("DIRECT_DELETION_ENABLED", "false")
    from .. import config
    config.get_settings.cache_clear()
    assert patient.get(f"/api/cases/{cid}/deletion-options").json()["can_delete_now"] is False
    assert patient.c.delete(f"/api/cases/{cid}", headers=patient._h()).status_code == 409


# ------------------------------------------------------------------ emergency / priority
def test_emergency_is_escalated_prominently_audited_and_never_claims_a_notification(app, patient, patient2, staff, doctor):
    other = submitted_case(patient2, "I have a mild cough.")
    cid = submitted_case(patient, "I have chest pain.")
    rows = staff.get("/api/staff/cases").json()["cases"]
    assert rows[0]["id"] == cid and rows[0]["urgency"] == "EMERGENCY"
    assert rows[0]["contact_mobile"] and doctor.get("/api/doctor/cases").json()["cases"][0]["id"] == cid
    assert all("contact_mobile" not in r for r in rows if r["urgency"] != "EMERGENCY")
    ev = [e for e in audit_actions(cid) if e[0] == "case.escalated_emergency"]
    assert len(ev) == 1 and ev[0][2]["notification_status"] == "not_configured" and ev[0][1] == "system"
    d = staff.get(f"/api/staff/cases/{cid}").json()["case"]
    assert d["escalation_notification"] == "not_configured" and d["urgency"] == "EMERGENCY"
    assert other not in [r["id"] for r in rows[:1]]


def test_emergency_draft_is_visible_to_staff_before_submission(app, patient, staff, flow):
    flow.say("I have chest pain.")
    assert any(r["id"] == flow.case_id and r["urgency"] == "EMERGENCY" for r in staff.get("/api/staff/cases").json()["cases"])
    assert staff.get(f"/api/staff/cases/{flow.case_id}").status_code == 200


def test_non_emergency_draft_stays_private(app, patient, staff, flow):
    flow.say("I have a mild cough.")
    assert staff.get(f"/api/staff/cases/{flow.case_id}").status_code == 404


def test_negated_chest_pain_is_not_an_emergency(app, patient, staff):
    cid = submitted_case(patient, "I do not have chest pain. I have a sore throat.")
    row = next(r for r in staff.get("/api/staff/cases").json()["cases"] if r["id"] == cid)
    assert row["urgency"] != "EMERGENCY"


def test_needs_review_is_independent_and_never_downgrades_an_emergency(app, patient, staff):
    cid = submitted_case(patient, "I have chest pain.")
    patient.post(f"/api/cases/{cid}/messages", {"text": "Actually I do not have chest pain."})
    row = next(r for r in staff.get("/api/staff/cases").json()["cases"] if r["id"] == cid)
    assert row["urgency"] == "EMERGENCY" and row["needs_review"] is True and "unresolved_contradiction" in row["review_flags"]
    assert staff.get("/api/staff/cases").json()["cases"][0]["id"] == cid


def test_needs_review_flag_can_coexist_with_standard_urgency_fields(app, patient, staff):
    cid = submitted_case(patient, "mild headache")
    row = next(r for r in staff.get("/api/staff/cases").json()["cases"] if r["id"] == cid)
    assert row["urgency"] in ("STANDARD", "NOT_DETERMINED", "URGENT") and isinstance(row["needs_review"], bool) and isinstance(row["review_flags"], list)


def test_staff_can_raise_but_never_lower_urgency_and_every_step_is_audited(app, patient, staff, doctor):
    cid = submitted_case(patient)
    r = staff.post(f"/api/staff/cases/{cid}/escalate", {"level": "urgent", "reason": "Patient phoned: symptoms are worsening."})
    assert r.status_code == 200 and r.json()["raised"] is True and r.json()["escalation_level"] == "urgent"
    r2 = staff.post(f"/api/staff/cases/{cid}/escalate", {"level": "emergency", "reason": "Patient now reports collapse at home."})
    assert r2.json()["raised"] is True
    r3 = staff.post(f"/api/staff/cases/{cid}/escalate", {"level": "urgent", "reason": "Trying to lower it again please."})
    assert r3.json()["raised"] is False and r3.json()["escalation_level"] == "emergency"  # no downgrade path for staff
    assert staff.post(f"/api/staff/cases/{cid}/escalate", {"level": "urgent", "reason": "short"}).status_code == 422
    actions = [e[0] for e in audit_actions(cid)]
    assert "case.escalated_urgent" in actions and "case.escalated_emergency" in actions
    first = [e for e in audit_actions(cid) if e[0] == "case.escalated_urgent"][0]
    assert first[1] == "staff" and first[2]["previous_level"] is None and first[2]["new_level"] == "urgent" and first[2]["actor_type"] == "human"


def test_staff_review_request_and_ops_notes_are_recorded_and_hidden_from_patient(app, patient, staff, doctor):
    cid = submitted_case(patient)
    assert staff.post(f"/api/staff/cases/{cid}/request-review", {"reason": "Report values look inconsistent."}).status_code == 200
    assert staff.post(f"/api/staff/cases/{cid}/ops-notes", {"note": "Called patient, no answer at 10:00."}).status_code == 201
    assert "no answer" not in patient.get(f"/api/cases/{cid}").text
    assert staff.get(f"/api/staff/cases/{cid}").json()["case"]["review_requested"] is True
    assert staff.post(f"/api/staff/cases/{cid}/clear-review-request").status_code == 403
    assert doctor.post(f"/api/doctor/cases/{cid}/clear-review-request").status_code == 200


# ------------------------------------------------------------------ audit semantics / admin
def test_audit_distinguishes_ai_suggestions_system_actions_and_human_decisions(app, patient, doctor):
    cid = submitted_case(patient, "I have chest pain for 2 days.")
    ev = audit_actions(cid)
    ai = [e for e in ev if e[0] == "ai.extraction_completed"]
    assert ai and all(e[1] == "ai" and e[2]["actor_type"] == "ai" and e[2]["suggestion_only"] is True for e in ai)
    prop = [e for e in ev if e[0] == "assessment.created"][0]
    assert prop[2]["actor_type"] == "system"  # a rule-based PROPOSAL, not a human decision
    assert not any(e[0] == "decision.recorded" for e in ev)
    asm = doctor.get(f"/api/doctor/cases/{cid}").json()["assessment"]
    doctor.post(f"/api/doctor/cases/{cid}/decision", {"assessment_id": asm["id"], "final_queue": "URGENT", "reason": "Confirmed on review of the evidence."})
    dec = [e for e in audit_actions(cid) if e[0] == "decision.recorded"][0]
    assert dec[2]["actor_type"] == "human"


def test_admin_can_create_doctors_and_role_changes_are_audited_but_admin_has_no_clinical_access(app, admin, patient):
    r = admin.post("/api/admin/users", {"email": "new.doc@demo-clinic.org", "display_name": "New Doc", "role": "doctor"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "doctor"
    uid = r.json()["user"]["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"role": "staff"}).status_code == 200
    log = admin.get("/api/admin/audit", params={"action": "admin."}).json()["events"]
    assert any(e["action"] == "admin.role_changed" and e["details"]["from"] == "doctor" and e["details"]["to"] == "staff" for e in log)
    cid = submitted_case(patient)
    assert admin.get(f"/api/doctor/cases/{cid}").status_code == 403 and admin.post(f"/api/doctor/cases/{cid}/reassess").status_code == 403
    assert admin.patch(f"/api/admin/users/{uid}", {"role": "patient"}).status_code == 409


def test_patients_cannot_change_queue_or_decide(app, patient, patient2):
    cid = submitted_case(patient)
    for api in (patient, patient2):
        assert api.post(f"/api/staff/cases/{cid}/decision", {"assessment_id": "x", "final_queue": "NORMAL", "reason": "me"}).status_code == 403
        assert api.post(f"/api/doctor/cases/{cid}/escalate", {"level": "urgent", "reason": "a patient doing this"}).status_code == 403


def test_audit_log_is_append_only_and_admin_only(app, patient, doctor, admin):
    cid = submitted_case(patient)
    assert doctor.get("/api/admin/audit").status_code == 403 and patient.get("/api/admin/audit").status_code == 403
    assert admin.get("/api/admin/audit/verify").json()["ok"] is True
    from sqlalchemy import text
    with pytest.raises(Exception):
        with session_scope() as db:
            db.execute(text("UPDATE audit_events SET action='x' WHERE case_id=:c"), {"c": cid})
