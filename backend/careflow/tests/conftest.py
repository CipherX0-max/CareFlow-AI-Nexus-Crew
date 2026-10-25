import os
import shutil
import tempfile
from pathlib import Path

import pytest

from .helpers import Api, FakeLLM, clear_profile_age, consent, make_user, PASSWORD


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh database + upload dir per test; LLM/STT real services are NOT used unless a test opts in."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("TESSDATA_DIR", str(Path(__file__).resolve().parents[3] / "data" / "tessdata"))
    from .. import config, db
    from ..services import pipeline
    config.get_settings.cache_clear()
    pipeline._ruleset_cache.clear()
    db.init_engine()
    yield tmp_path
    config.get_settings.cache_clear()


@pytest.fixture()
def fake_llm(env, monkeypatch):
    f = FakeLLM()
    from ..services import llm
    monkeypatch.setattr(llm, "chat_json", f)
    monkeypatch.setattr(llm, "status", lambda: {"available": True, "model": "fake-llm", "detail": "fake"})
    return f


@pytest.fixture()
def app(env, fake_llm):
    from ..main import app as _app
    from fastapi.testclient import TestClient
    with TestClient(_app):  # runs lifespan: migrations + ruleset sync
        yield _app


@pytest.fixture()
def staff(app):
    make_user("dr.synthetic@demo-clinic.org", "staff", "Dr Synthetic")
    a = Api(app)
    assert a.login("dr.synthetic@demo-clinic.org").status_code == 200
    return a


@pytest.fixture()
def staff2(app):
    make_user("dr.second@demo-clinic.org", "staff", "Dr Second")
    a = Api(app)
    assert a.login("dr.second@demo-clinic.org").status_code == 200
    return a


@pytest.fixture()
def doctor(app):
    make_user("dr.clinician@demo-clinic.org", "doctor", "Dr Clinician")
    a = Api(app)
    assert a.login("dr.clinician@demo-clinic.org").status_code == 200
    return a


@pytest.fixture()
def doctor2(app):
    make_user("dr.other@demo-clinic.org", "doctor", "Dr Other")
    a = Api(app)
    assert a.login("dr.other@demo-clinic.org").status_code == 200
    return a


@pytest.fixture()
def admin(app):
    make_user("admin.synthetic@demo-clinic.org", "admin", "Admin Synthetic")
    a = Api(app)
    assert a.login("admin.synthetic@demo-clinic.org").status_code == 200
    return a


@pytest.fixture()
def patient(app):
    a = Api(app)
    r = a.signup("pat.one@demo-clinic.org", name="Patient One")
    assert r.status_code == 201, r.text
    clear_profile_age("pat.one@demo-clinic.org")  # legacy-style profile without an age, so tests that exercise age extraction/questions stay meaningful
    assert consent(a).status_code == 201
    return a


@pytest.fixture()
def patient2(app):
    a = Api(app)
    assert a.signup("pat.two@demo-clinic.org", name="Patient Two").status_code == 201
    clear_profile_age("pat.two@demo-clinic.org")
    assert consent(a).status_code == 201
    return a


class Flow:
    """Convenience: drive a patient through intake the way the UI does."""

    def __init__(self, patient: Api):
        self.p = patient
        self.case_id = patient.post("/api/cases").json()["id"]

    def say(self, text: str, token: str | None = None):
        r = self.p.post(f"/api/cases/{self.case_id}/messages", {"text": text, "client_token": token})
        assert r.status_code == 201, r.text
        return r.json()

    def view(self):
        return self.p.get(f"/api/cases/{self.case_id}").json()

    def submit(self):
        r = self.p.post(f"/api/cases/{self.case_id}/submit")
        assert r.status_code == 200, r.text
        return r.json()

    def answer(self, key_prefix: str, text: str = "", choice: str | None = None):
        q = next(q for q in self.view()["questions"] if q["key"].startswith(key_prefix) and q["status"] == "open")
        r = self.p.post(f"/api/cases/{self.case_id}/questions/{q['id']}/answer", {"text": text, "choice": choice})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture()
def flow(patient):
    return Flow(patient)
