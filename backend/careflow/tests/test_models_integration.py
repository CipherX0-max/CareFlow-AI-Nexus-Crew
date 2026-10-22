"""Integration with the REAL local models (Ollama qwen3:4b, faster-whisper). Skipped automatically when unavailable."""
import pytest

from ..services import llm, stt
from .conftest import Flow
from .helpers import Api, consent, make_user, PASSWORD
from . import media

pytestmark = pytest.mark.requires_models


@pytest.fixture()
def real_app(env):
    if not llm.status()["available"]:
        pytest.skip("Ollama / qwen3:4b not available")
    from fastapi.testclient import TestClient
    from ..main import app
    with TestClient(app):
        yield app


def test_real_llm_extracts_hinglish_with_grounded_excerpts_and_translation(real_app):
    make_user("dr.real@demo-clinic.org", "staff", "Dr Real")
    staff, pat = Api(real_app), Api(real_app)
    staff.login("dr.real@demo-clinic.org")
    pat.signup("real.pat@demo-clinic.org"); consent(pat)
    f = Flow(pat)
    text = "Mujhe kal raat se tez sir dard hai aur do baar ulti hui. Bukhar nahi hai. Meri umar 41 saal hai."
    f.say(text)
    f.submit()
    d = staff.get(f"/api/staff/cases/{f.case_id}").json()
    by = {(x["concept"], x["polarity"]) for x in d["facts"]}
    assert ("headache", "present") in by and ("fever", "denied") in by and ("vomiting", "present") in by
    assert all(x["excerpt"] and x["excerpt"].lower() in text.lower() or x["method"] == "lexicon" for x in d["facts"])  # every LLM fact quotes the source
    m = d["messages"][0]
    assert m["text"] == text and m["text_en"] and "headache" in m["text_en"].lower()
    runs_ok = d["assessment"]["data_quality"]
    assert not any(x["code"] == "ai_extraction_degraded" for x in runs_ok)


def test_real_whisper_hindi_speech_is_transcribed_translated_and_flagged_for_confirmation(real_app, tmp_path):
    if not (media.have_tts() and stt.status()["available"]):
        pytest.skip("macOS TTS or Whisper model unavailable")
    pat = Api(real_app)
    pat.signup("voice.pat@demo-clinic.org"); consent(pat)
    f = Flow(pat)
    wav = media.speech_wav("मुझे दो दिन से सीने में दर्द है और सांस लेने में तकलीफ है।", "Lekha", tmp_path / "hi.wav").read_bytes()
    up = pat.upload(f"/api/cases/{f.case_id}/uploads", "v.wav", wav, "audio/wav").json()["upload"]
    got = pat.get(f"/api/cases/{f.case_id}/uploads/{up['id']}").json()
    assert got["status"] == "needs_confirmation" and got["detected_language"] == "hi" and got["language_confidence"] > 0.5
    assert got["translated_text"] and got["segments"] and got["method"] == "faster-whisper"
    assert got["extracted_text"] != got["translated_text"]  # original transcript preserved separately from the English rendering
