"""Authentication, role-based access, ownership, CSRF, uploads security."""
from .conftest import Flow
from .helpers import Api, PASSWORD, consent, make_user
from . import media


def test_signup_always_creates_patient_even_if_role_is_sent(app):
    a = Api(app)
    r = a.c.post("/api/auth/signup", json={"email": "sneaky@demo-clinic.org", "password": PASSWORD, "display_name": "S", "role": "admin"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "patient"
    assert a.get("/api/admin/users").status_code == 403


def test_passwords_are_hashed_and_weak_ones_rejected(app):
    a = Api(app)
    assert a.signup("weak@demo-clinic.org", "short1!").status_code == 422
    assert a.signup("weak@demo-clinic.org", "alllettersnodigit").status_code == 422
    assert a.signup("ok@demo-clinic.org").status_code == 201
    from ..db import session_scope
    from ..models import User
    with session_scope() as db:
        h = db.query(User).filter_by(email="ok@demo-clinic.org").one().password_hash
    assert h.startswith("$argon2") and PASSWORD not in h


def test_login_logout_and_session_cookie_flags(app):
    make_user("p@demo-clinic.org", "patient")
    a = Api(app)
    assert a.login("p@demo-clinic.org", "wrong-password!").status_code == 401
    r = a.login("p@demo-clinic.org")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert a.get("/api/auth/me").status_code == 200
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/auth/me").status_code == 401


def test_error_messages_do_not_reveal_whether_account_exists(app):
    make_user("exists@demo-clinic.org", "patient")
    a, b = Api(app), Api(app)
    r1 = a.c.post("/api/auth/login", json={"email": "exists@demo-clinic.org", "password": "nope-nope-1!"})
    r2 = b.c.post("/api/auth/login", json={"email": "ghost@demo-clinic.org", "password": "nope-nope-1!"})
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()
    f1 = a.c.post("/api/auth/password/forgot", json={"email": "exists@demo-clinic.org"}).json()
    f2 = a.c.post("/api/auth/password/forgot", json={"email": "ghost@demo-clinic.org"}).json()
    assert f1["message"] == f2["message"]


def test_account_lockout_after_repeated_failures(app):
    make_user("lock@demo-clinic.org", "patient")
    a = Api(app)
    for _ in range(5):
        assert a.login("lock@demo-clinic.org", "bad-password-1!").status_code == 401
    assert a.login("lock@demo-clinic.org").status_code == 423  # even the right password is refused while locked


def test_rate_limit_on_auth_endpoints(app, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "3")
    from .. import config
    from ..security import rate_limiter
    config.get_settings.cache_clear()
    rate_limiter.reset()
    a = Api(app)
    codes = [a.c.post("/api/auth/login", json={"email": "x@demo-clinic.org", "password": "x"}).status_code for _ in range(5)]
    assert 429 in codes


def test_csrf_required_for_state_changes(app, patient):
    r = patient.c.post("/api/cases")  # cookie but no CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert patient.c.post("/api/cases", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert patient.post("/api/cases").status_code == 201


def test_cross_origin_requests_refused(app, patient):
    r = patient.c.post("/api/cases", headers={"X-CSRF-Token": patient.csrf, "Origin": "https://evil.example.org"})
    assert r.status_code == 403


def test_unauthenticated_access_is_401_everywhere(app):
    a = Api(app)
    for url in ("/api/cases", "/api/staff/cases", "/api/staff/queues/summary", "/api/admin/users", "/api/admin/audit"):
        assert a.get(url).status_code == 401, url


def test_patient_cannot_use_staff_or_admin_endpoints(app, patient):
    for url in ("/api/staff/cases", "/api/staff/queues/summary", "/api/admin/users", "/api/admin/system", "/api/admin/audit"):
        assert patient.get(url).status_code == 403, url


def test_patient_cannot_read_or_write_another_patients_case(app, patient, patient2):
    f = Flow(patient)
    f.say("I have a headache for 3 days.")
    cid = f.case_id
    # reads
    assert patient2.get(f"/api/cases/{cid}").status_code == 404
    assert patient2.get(f"/api/cases/{cid}/uploads/anything").status_code == 404
    # writes
    assert patient2.post(f"/api/cases/{cid}/messages", {"text": "injected"}).status_code == 404
    assert patient2.post(f"/api/cases/{cid}/submit").status_code == 404
    assert patient2.post(f"/api/cases/{cid}/deletion-request", {"reason": "x"}).status_code == 404
    assert patient2.upload(f"/api/cases/{cid}/uploads", "r.pdf", media.digital_pdf()).status_code == 404
    q = f.view()["questions"][0]
    assert patient2.post(f"/api/cases/{cid}/questions/{q['id']}/answer", {"text": "1", "choice": None}).status_code == 404
    # owner is unaffected and list endpoints are scoped
    assert len(patient.get("/api/cases").json()["cases"]) == 1
    assert patient2.get("/api/cases").json()["cases"] == []
    assert patient.get(f"/api/cases/{cid}").json()["messages"][0]["text"] == "I have a headache for 3 days."


def test_patient_cannot_read_other_patients_uploaded_file(app, patient, patient2):
    f = Flow(patient)
    up = patient.upload(f"/api/cases/{f.case_id}/uploads", "r.pdf", media.digital_pdf()).json()["upload"]
    assert patient.get(f"/api/cases/{f.case_id}/uploads/{up['id']}/file").status_code == 200
    assert patient2.get(f"/api/cases/{f.case_id}/uploads/{up['id']}/file").status_code == 404
    assert Api(app).get(f"/api/cases/{f.case_id}/uploads/{up['id']}/file").status_code == 401


def test_staff_cannot_see_draft_cases_and_admin_cannot_open_cases(app, patient, staff, admin):
    f = Flow(patient)
    f.say("I have a headache for 3 days.")
    assert staff.get(f"/api/staff/cases/{f.case_id}").status_code == 404  # still a draft
    assert staff.get("/api/staff/cases").json()["total"] == 0
    f.submit()
    assert staff.get(f"/api/staff/cases/{f.case_id}").status_code == 200
    assert admin.get(f"/api/staff/cases/{f.case_id}").status_code == 403
    assert admin.get(f"/api/cases/{f.case_id}").status_code == 403


def test_consent_required_before_creating_cases_or_messages(app):
    a = Api(app)
    a.signup("noconsent@demo-clinic.org")
    r = a.post("/api/cases")
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"
    consent(a)
    cid = a.post("/api/cases").json()["id"]
    a.post("/api/patient/consent/withdraw")
    r = a.post(f"/api/cases/{cid}/messages", {"text": "headache"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "consent_required"


def test_only_admin_can_create_staff_accounts(app, admin, staff, patient):
    body = {"email": "new.doc@demo-clinic.org", "display_name": "New Doc", "role": "staff"}
    assert patient.post("/api/admin/users", body).status_code == 403
    assert staff.post("/api/admin/users", body).status_code == 403
    r = admin.post("/api/admin/users", body)
    assert r.status_code == 201 and r.json()["user"]["role"] == "staff" and r.json()["temporary_password"]
    n = Api(app)
    assert n.login("new.doc@demo-clinic.org", r.json()["temporary_password"]).json()["user"]["must_change_password"] is True


def test_admin_cannot_lock_self_out_or_convert_patients(app, admin, patient):
    me = admin.user["id"]
    assert admin.patch(f"/api/admin/users/{me}", {"is_active": False}).status_code == 409
    assert admin.patch(f"/api/admin/users/{patient.user['id']}", {"role": "admin"}).status_code == 409


def test_disabled_user_loses_access_immediately(app, admin, staff):
    assert staff.get("/api/staff/queues/summary").status_code == 200
    admin.patch(f"/api/admin/users/{staff.user['id']}", {"is_active": False})
    assert staff.get("/api/staff/queues/summary").status_code == 401


def test_password_reset_flow_dev_mode_token_and_session_revocation(app):
    make_user("reset@demo-clinic.org", "patient")
    a = Api(app)
    a.login("reset@demo-clinic.org")
    tok = a.c.post("/api/auth/password/forgot", json={"email": "reset@demo-clinic.org"}).json()["dev_reset_token"]
    assert a.c.post("/api/auth/password/reset", json={"token": tok, "new_password": "weak"}).status_code == 422
    assert a.c.post("/api/auth/password/reset", json={"token": tok, "new_password": "A-brand-new-pass-9"}).status_code == 200
    assert a.c.post("/api/auth/password/reset", json={"token": tok, "new_password": "Another-pass-99!"}).status_code == 400  # single use
    assert a.get("/api/auth/me").status_code == 401  # old session revoked
    assert Api(app).login("reset@demo-clinic.org", "A-brand-new-pass-9").status_code == 200


def test_email_verification_dev_token(app):
    a = Api(app)
    tok = a.signup("verify@demo-clinic.org").json()["dev_verification_token"]
    assert a.get("/api/auth/me").json()["user"]["email_verified"] is False
    assert a.c.post("/api/auth/verify-email", json={"token": tok}).status_code == 200
    assert a.get("/api/auth/me").json()["user"]["email_verified"] is True


def test_dev_tokens_refused_in_production_config(monkeypatch):
    from .. import config
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("EXPOSE_DEV_TOKENS", "true")
    config.get_settings.cache_clear()
    import pytest
    with pytest.raises(RuntimeError):
        config.get_settings().validate_for_runtime()
    config.get_settings.cache_clear()


def test_upload_rejects_disguised_and_oversized_files(app, patient):
    f = Flow(patient)
    url = f"/api/cases/{f.case_id}/uploads"
    r = patient.upload(url, "report.pdf", b"MZ\x90\x00 not a pdf at all", "application/pdf")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_type"
    assert patient.upload(url, "empty.pdf", b"", "application/pdf").status_code == 422
    big = b"%PDF-1.4\n" + b"0" * (21 * 1024 * 1024)
    assert patient.upload(url, "big.pdf", big, "application/pdf").status_code == 413
    r = patient.upload(url, "../../../etc/passwd.pdf", media.digital_pdf(), "application/pdf")
    assert r.status_code == 202
    assert "/" not in r.json()["upload"]["filename"]
    assert "stored_name" not in r.json()["upload"] and "path" not in str(r.json()["upload"]).lower().replace("filename", "")


def test_uploaded_files_are_stored_outside_static_and_served_with_safe_headers(app, patient, env):
    f = Flow(patient)
    up = patient.upload(f"/api/cases/{f.case_id}/uploads", "r.pdf", media.digital_pdf()).json()["upload"]
    r = patient.get(f"/api/cases/{f.case_id}/uploads/{up['id']}/file")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    stored = list((env / "uploads" / f.case_id).iterdir())
    assert len(stored) == 1 and stored[0].suffix == ".pdf" and oct(stored[0].stat().st_mode)[-3:] == "600"


def test_duplicate_upload_is_deduplicated(app, patient):
    f = Flow(patient)
    pdf = media.digital_pdf()
    a = patient.upload(f"/api/cases/{f.case_id}/uploads", "r.pdf", pdf).json()
    b = patient.upload(f"/api/cases/{f.case_id}/uploads", "again.pdf", pdf).json()
    assert b["duplicate"] is True and a["upload"]["id"] == b["upload"]["id"]


def test_validation_errors_use_consistent_error_shape(app, patient):
    f = Flow(patient)
    r = patient.post(f"/api/cases/{f.case_id}/messages", {"text": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] in ("validation_error", "empty_message")
    r = patient.post(f"/api/cases/{f.case_id}/messages", {"nope": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = patient.post(f"/api/cases/{f.case_id}/messages", {"text": "x" * 6000})
    assert r.status_code == 422
