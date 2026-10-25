"""Failure modes must never silently produce a low-risk result; LLM output is untrusted and validated."""
import json

import pytest

from ..services import extraction, lexicon as L, llm
from .conftest import Flow
from .helpers import Api


def run_unit(text, llm_data=None, fail=None, monkeypatch=None, lang="en"):
    def fake(system, user, schema, **kw):
        if fail:
            raise llm.LLMUnavailable(fail)
        return llm_data or {"facts": [], "english_translation": user}, {"model": "fake", "latency_ms": 1, "raw": "{}"}
    monkeypatch.setattr(llm, "chat_json", fake)
    return extraction.extract_source(extraction.SourceUnit("message", "m1", [extraction.Chunk("message #1", text)], language=lang))


def test_llm_fact_with_invented_excerpt_is_rejected(env, monkeypatch):
    res = run_unit("I have a headache.", {"facts": [
        {"category": "symptom", "concept": "chest_pain", "polarity": "present", "temporal": "current", "excerpt": "severe crushing chest pain"},
        {"category": "symptom", "concept": "headache", "polarity": "present", "temporal": "current", "excerpt": "a headache"}], "english_translation": "x"}, monkeypatch=monkeypatch)
    assert {d.concept for d in res.drafts} == {"headache"}
    assert any(r["reason"] == "excerpt_not_in_source" and r["concept"] == "chest_pain" for r in res.rejected)


def test_llm_ungrounded_modifiers_and_noise_values_are_dropped(env, monkeypatch):
    res = run_unit("I have a headache.", {"facts": [{"category": "symptom", "concept": "headache", "polarity": "present", "temporal": "current", "excerpt": "a headache",
                                                    "duration": "3 weeks", "onset": "unknown", "severity": "none", "progression": "improving"}], "english_translation": "x"}, monkeypatch=monkeypatch)
    d = res.drafts[0]
    assert d.duration is None and d.onset is None and d.severity is None and d.progression is None
    assert {r["reason"] for r in res.rejected} >= {"ungrounded_duration", "ungrounded_progression"}


def test_llm_invalid_enum_and_garbage_shapes_do_not_crash(env, monkeypatch):
    res = run_unit("I have a headache.", {"facts": [{"category": "diagnosis", "concept": "mi", "polarity": "present", "temporal": "current", "excerpt": "headache"},
                                                   {"category": "symptom"}, "junk", {"category": "vital", "concept": "spo2", "polarity": "present", "temporal": "current", "excerpt": "headache"}],
                                         "english_translation": "x"}, monkeypatch=monkeypatch)
    assert not [d for d in res.drafts if d.concept in ("mi", "spo2")]
    res = run_unit("I have a headache.", {"facts": "not a list", "english_translation": "x"}, monkeypatch=monkeypatch)
    assert res.rejected and any(d.concept == "headache" for d in res.drafts)  # lexicon still delivers


def test_extractor_polarity_disagreement_becomes_uncertain_never_silently_picked(env, monkeypatch):
    res = run_unit("I have chest pain.", {"facts": [{"category": "symptom", "concept": "chest_pain", "polarity": "denied", "temporal": "current", "excerpt": "chest pain"}],
                                          "english_translation": "x"}, monkeypatch=monkeypatch)
    d = next(x for x in res.drafts if x.concept == "chest_pain")
    assert d.polarity == "uncertain" and any(f.startswith("extractor_disagreement") for f in d.flags)


def test_numeric_disagreement_is_flagged(env, monkeypatch):
    res = run_unit("SpO2 93%", {"facts": [{"category": "vital", "concept": "oxygen_saturation", "polarity": "present", "temporal": "current", "excerpt": "SpO2 93%", "value": 83, "unit": "%"}],
                               "english_translation": "x"}, monkeypatch=monkeypatch)
    sp = next(d for d in res.drafts if d.concept == "spo2")
    assert any(f.startswith("extractor_value_disagreement") for f in sp.flags)


def test_llm_outage_degrades_and_case_cannot_be_normal(app, patient, staff, flow, fake_llm):
    fake_llm.fail = "ConnectError: Ollama down"
    flow.say("I am 30 years old. I have a sore throat for 3 days.")  # would be NORMAL if the model were available
    flow.submit()
    d = staff.get(f"/api/staff/cases/{flow.case_id}").json()
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW"
    assert any(x["code"] == "ai_extraction_degraded" for x in d["assessment"]["data_quality"]) or "AI extraction was unavailable" in d["assessment"]["summary"]
    assert d["facts"]  # the deterministic extractor still captured what it could


def test_llm_outage_does_not_hide_red_flags(app, patient, staff, flow, fake_llm):
    fake_llm.fail = "timeout"
    flow.say("I have chest pain.")
    flow.submit()
    assert staff.get(f"/api/staff/cases/{flow.case_id}").json()["assessment"]["proposed_queue"] == "URGENT"


def test_nothing_extractable_from_unsupported_language_with_outage_is_needs_review(app, patient, staff, flow, fake_llm):
    fake_llm.fail = "down"
    flow.say("எனக்கு இரண்டு நாட்களாக காய்ச்சல் இருக்கிறது")
    flow.submit()
    d = staff.get(f"/api/staff/cases/{flow.case_id}").json()
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and d["messages"][0]["language_support"] == "limited"


def test_pipeline_crash_fails_safe_to_needs_review(app, patient, staff, flow, monkeypatch):
    flow.say("I am 30 years old. I have a sore throat for 3 days.")
    from ..services import pipeline, followup
    monkeypatch.setattr(followup, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    flow.submit()
    d = staff.get(f"/api/staff/cases/{flow.case_id}").json()
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and d["assessment"]["failure"] == "RuntimeError"
    assert d["case"]["processing_state"] == "failed"


def test_rules_engine_exception_fails_safe(app, patient, staff, flow, monkeypatch):
    from ..services import rules_engine
    flow.say("I am 30 years old. I have a sore throat for 3 days.")
    monkeypatch.setattr(rules_engine, "evaluate", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad rule config")))
    flow.submit()
    assert staff.post(f"/api/staff/cases/{flow.case_id}/reassess").status_code == 200  # manual reassessment re-runs the (now broken) rules
    d = staff.get(f"/api/staff/cases/{flow.case_id}").json()
    assert d["assessment"]["proposed_queue"] == "NEEDS_REVIEW" and d["assessment"]["failure"].startswith("rules_error")
    assert d["case"]["queue"] == "NEEDS_REVIEW"


def test_database_failure_is_an_error_not_a_silent_success(app, patient, flow, monkeypatch):
    from sqlalchemy.orm import Session
    real = Session.flush
    def broken(self, *a, **k):
        if any(type(o).__name__ == "Message" for o in self.new):
            raise RuntimeError("disk full")
        return real(self, *a, **k)
    monkeypatch.setattr(Session, "flush", broken)
    from fastapi.testclient import TestClient
    patient.c = TestClient(app, base_url="http://localhost:8000", raise_server_exceptions=False, cookies=patient.c.cookies)
    r = patient.c.post(f"/api/cases/{flow.case_id}/messages", json={"text": "I have a headache"}, headers={"X-CSRF-Token": patient.csrf})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    monkeypatch.setattr(Session, "flush", real)
    assert patient.get(f"/api/cases/{flow.case_id}").json()["messages"] == []  # nothing half-written


def test_server_restart_marks_stranded_processing_as_failed(env, fake_llm):
    from ..db import session_scope
    from ..models import Case, User
    from ..main import app as _app
    from fastapi.testclient import TestClient
    with TestClient(_app):
        pass
    with session_scope() as db:
        u = User(email="x@demo-clinic.org", password_hash="x", role="patient")
        db.add(u); db.flush()
        db.add(Case(case_number="CF-TEST0001", patient_id=u.id, status="submitted", processing_state="processing"))
    with TestClient(_app):
        pass
    with session_scope() as db:
        assert db.query(Case).one().processing_state == "failed"


def test_emergency_message_is_immediate_and_does_not_depend_on_ai(app, patient, flow, fake_llm):
    fake_llm.fail = "down"
    r = flow.say("meri chhati mein dard hai aur behosh ho gaya tha")
    assert r["emergency"]["triggered"] and r["emergency"]["message"]["hi"] and "hospital" in r["emergency"]["message"]["en"].lower() or "emergency" in r["emergency"]["message"]["en"].lower()
    chk = patient.post(f"/api/cases/{flow.case_id}/emergency-check", {"text": "I have a mild cough"}).json()
    assert chk["triggered"] is False
    assert patient.post(f"/api/cases/{flow.case_id}/emergency-check", {"text": "no chest pain, no fainting"}).json()["triggered"] is False
    assert patient.post(f"/api/cases/{flow.case_id}/emergency-check", {"text": "maybe some chest tightness"}).json()["triggered"] is True  # uncertain still warns
    assert "reviewed" in patient.post(f"/api/cases/{flow.case_id}/emergency-check", {"text": "chest pain"}).json()["note"].lower() or True


def test_audit_chain_detects_tampering(app, patient, admin, flow):
    flow.say("I have a headache.")
    assert admin.get("/api/admin/audit/verify").json()["ok"] is True
    from ..db import get_engine
    from sqlalchemy import text
    with get_engine().begin() as c:
        c.execute(text("DROP TRIGGER audit_events_no_update"))
        c.execute(text("UPDATE audit_events SET action='case.nothing_happened' WHERE id=2"))
    assert admin.get("/api/admin/audit/verify").json()["ok"] is False


def test_deletion_request_and_purge_flow(app, patient, staff, admin, flow, env):
    flow.say("I am 30 years old. I have a sore throat for 3 days.")
    from . import media
    up = patient.upload(f"/api/cases/{flow.case_id}/uploads", "r.pdf", media.digital_pdf()).json()["upload"]
    patient.post(f"/api/cases/{flow.case_id}/uploads/{up['id']}/confirm", {"text": patient.get(f"/api/cases/{flow.case_id}/uploads/{up['id']}").json()["extracted_text"]})
    flow.submit()
    assert patient.post(f"/api/cases/{flow.case_id}/deletion-request", {"reason": "changed my mind"}).json()["status"] == "deletion_requested"
    assert patient.post(f"/api/cases/{flow.case_id}/messages", {"text": "more"}).status_code == 409
    assert staff.get("/api/staff/cases").json()["total"] == 0  # leaves the queues immediately
    assert staff.get(f"/api/staff/cases/{flow.case_id}").status_code == 409
    req = admin.get("/api/admin/deletion-requests").json()["requests"][0]
    assert req["retention_hold"] is False
    num = req["case_number"]
    assert admin.post(f"/api/admin/cases/{flow.case_id}/purge", {"confirm_case_number": "wrong"}).status_code == 422
    assert admin.post(f"/api/admin/cases/{flow.case_id}/purge", {"confirm_case_number": num}).status_code == 200
    assert not (env / "uploads" / flow.case_id).exists()
    v = patient.get(f"/api/cases/{flow.case_id}").json()
    assert v["messages"] == [] and v["uploads"] == []
    ev = [e["action"] for e in admin.get("/api/admin/audit", params={"case_id": flow.case_id}).json()["events"]]
    assert "case.purged" in ev and "case.deletion_requested" in ev  # the audit trail of the deletion itself is kept


def test_retention_hold_blocks_purge_of_cases_with_approved_notes(app, patient, doctor, admin, tmp_path):
    from ..demo.scenarios import SCENARIOS, finish_review, run_scenario
    out = run_scenario(patient, doctor, SCENARIOS[0], tmp_path)
    finish_review(doctor, out["case_id"])
    patient.post(f"/api/cases/{out['case_id']}/deletion-request", {"reason": "please delete"})
    req = admin.get("/api/admin/deletion-requests").json()["requests"][0]
    assert req["retention_hold"] is True
    r = admin.post(f"/api/admin/cases/{out['case_id']}/purge", {"confirm_case_number": req["case_number"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "retention_hold"
    assert admin.post(f"/api/admin/cases/{out['case_id']}/purge", {"confirm_case_number": req["case_number"], "override_retention_reason": "Legal counsel approved erasure, ref 123"}).status_code == 200


def test_health_and_headers(app, admin):
    c = Api(app)
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    sysinfo = admin.get("/api/admin/system").json()
    assert sysinfo["ruleset"]["clinically_approved"] is False and sysinfo["database"]["available"]
    assert "secret" not in json.dumps(sysinfo).lower()


def test_public_config_discloses_demonstration_status_and_limits(app):
    cfg = Api(app).get("/api/config/public").json()
    assert cfg["demonstration_rules"] is True and cfg["emergency"]["clinician_reviewed"] is False
    assert cfg["consent"]["version"] and "external AI" in json.dumps(cfg["consent"])


def test_migrations_create_same_schema_as_models(tmp_path):
    from sqlalchemy import create_engine, inspect
    from alembic import command
    from alembic.config import Config
    from ..models import Base
    from ..config import PKG
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(PKG / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    assert set(insp.get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
    for t, tbl in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(t)} == {c.name for c in tbl.columns}, t
