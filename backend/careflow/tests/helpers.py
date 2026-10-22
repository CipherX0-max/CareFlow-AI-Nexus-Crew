"""Test helpers: API client wrapper with CSRF handling, user factories, fake LLM."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from fastapi.testclient import TestClient

from ..db import session_scope
from ..models import Consent, PatientProfile, User
from ..security import hash_password
from ..content import CONSENT_VERSION

PASSWORD = "Synthetic-Pass-1!"


class Api:
    """A browser-like session: cookie jar + CSRF header, against the real app."""

    def __init__(self, app):
        self.c = TestClient(app, base_url="http://localhost:8000")
        self.csrf: Optional[str] = None
        self.user: dict | None = None

    def login(self, email: str, password: str = PASSWORD):
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.csrf, self.user = r.json()["csrf_token"], r.json()["user"]
        return r

    def signup(self, email: str, password: str = PASSWORD, name: str = "Synthetic Patient", **kw):
        r = self.c.post("/api/auth/signup", json={"email": email, "password": password, "display_name": name, **kw})
        if r.status_code == 201:
            self.csrf, self.user = r.json()["csrf_token"], r.json()["user"]
        return r

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self._h(), **kw)

    def put(self, url, json=None, **kw):
        return self.c.put(url, json=json, headers=self._h(), **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch(url, json=json, headers=self._h(), **kw)

    def upload(self, url, name: str, data: bytes, ctype: str = "application/octet-stream"):
        return self.c.post(url, files={"file": (name, data, ctype)}, headers=self._h())


def make_user(email: str, role: str = "staff", name: str = "", synthetic: bool = True, password: str = PASSWORD) -> str:
    with session_scope() as db:
        u = User(email=email.lower(), password_hash=hash_password(password), role=role, display_name=name or email.split("@")[0], is_synthetic=synthetic)
        db.add(u)
        db.flush()
        if role == "patient":
            db.add(PatientProfile(user_id=u.id))
        return u.id


def consent(api: Api):
    return api.post("/api/patient/consent", {"accept": True, "text_version": CONSENT_VERSION})


class FakeLLM:
    """Deterministic stand-in for Ollama. `responses` maps a substring of the user text to a result dict;
    default = no extra facts and an identity 'translation'. Set `fail=True` to simulate an outage."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.fail: str | None = None
        self.calls: list[str] = []
        self.translations: dict[str, str] = {}

    def __call__(self, system: str, user: str, schema: dict, **kw):
        from ..services import llm
        self.calls.append(user)
        if self.fail:
            raise llm.LLMUnavailable(self.fail)
        out: dict[str, Any] = {"facts": [], "english_translation": user}
        for k, v in self.responses.items():
            if k in user:
                out = {**out, **v}
        for k, v in self.translations.items():
            if k in user:
                out["english_translation"] = v
        return out, {"model": "fake-llm", "latency_ms": 1, "raw": json.dumps(out)}
