"""Tests for faster-whisper `large-v3-turbo` optimization, configurable decoding (`beam_size`, `vad_filter`),
optional/disabled second-pass translation (`WHISPER_TRANSLATE=false`), single-load model caching,
no silent model fallback, and re-uploading audio after removal or discard.
"""
from __future__ import annotations

import io
import math
import struct
import sys
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from .. import config
from ..services import stt, uploads as U
from .helpers import Api, consent


def _make_wav_bytes(duration_sec: float = 0.6, rate: int = 16000, freq: float = 440.0) -> bytes:
    buf = io.BytesIO()
    n_samples = int(rate * duration_sec)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n_samples):
            val = int(8000 * math.sin(2 * math.pi * freq * (i / rate)))
            frames.extend(struct.pack("<h", val))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def test_default_whisper_settings_and_beam_size_validation(monkeypatch):
    for k in (
        "WHISPER_MODEL",
        "WHISPER_DEVICE",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_BEAM_SIZE",
        "WHISPER_VAD_FILTER",
        "WHISPER_TRANSLATE",
    ):
        monkeypatch.delenv(k, raising=False)
    s = config.Settings(_env_file=None)
    assert s.whisper_model == "large-v3-turbo"
    assert s.whisper_device == "cpu"
    assert s.whisper_compute_type == "int8"
    assert s.whisper_beam_size == 3
    assert s.whisper_vad_filter is True
    assert s.whisper_translate is False

    # Beam size bounds validation (1..10)
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, whisper_beam_size=0)
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, whisper_beam_size=15)


def test_installed_faster_whisper_supports_large_v3_turbo_and_multilingual_hints():
    import ctranslate2
    import faster_whisper

    models = getattr(faster_whisper.utils, "_MODELS", {})
    assert "large-v3-turbo" in models
    assert "large-v3" in models
    assert ctranslate2.__version__
    assert stt.SUPPORTED_LANGUAGE_HINTS == {"en", "hi", "mr", "bn", "hinglish", "auto"}


def test_model_initialization_caching_and_no_silent_fallback(monkeypatch, tmp_path):
    init_calls = []

    class DummyWhisperModel:
        def __init__(self, model_size_or_path, **kwargs):
            init_calls.append((model_size_or_path, kwargs))

    fake_fw = ModuleType("faster_whisper")
    fake_fw.WhisperModel = DummyWhisperModel
    fake_fw.utils = SimpleNamespace(_MODELS={"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "large-v3": "Systran/faster-whisper-large-v3"})
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_model_key", None)
    monkeypatch.setattr(stt, "_load_error", None)
    monkeypatch.setattr(stt, "_load_count", 0)

    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            stt_enabled=True,
            whisper_model="large-v3-turbo",
            whisper_device="cpu",
            whisper_compute_type="int8",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=False,
            whisper_model_dir=tmp_path,
            whisper_local_only=False,
        ),
    )

    m1 = stt.get_model()
    m2 = stt.get_model()
    assert m1 is m2
    assert len(init_calls) == 1
    assert init_calls[0][0] == "large-v3-turbo"
    assert init_calls[0][1]["device"] == "cpu"
    assert init_calls[0][1]["compute_type"] == "int8"
    st = stt.status()
    assert st["loaded"] is True
    assert st["model"] == "large-v3-turbo"
    assert st["beam_size"] == 3
    assert st["translate_enabled"] is False
    assert st["load_count"] == 1

    # Verify no silent fallback when an invalid model identifier is configured
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_model_key", None)
    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            stt_enabled=True,
            whisper_model="nonexistent-whisper-model",
            whisper_device="cpu",
            whisper_compute_type="int8",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=False,
            whisper_model_dir=tmp_path,
            whisper_local_only=True,
        ),
    )
    with pytest.raises(stt.SpeechError) as exc_info:
        stt.get_model()
    assert exc_info.value.code == "stt_model_unavailable"
    assert len(init_calls) == 1  # did not fall back to another model


def test_is_model_cached_distinguishes_large_v3_from_large_v3_turbo(tmp_path):
    # Only large-v3-turbo is in the directory
    (tmp_path / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo").mkdir()
    assert stt._is_model_cached("large-v3-turbo", tmp_path) is True
    assert stt._is_model_cached("large-v3", tmp_path) is False

    # Now add large-v3
    (tmp_path / "models--Systran--faster-whisper-large-v3").mkdir()
    assert stt._is_model_cached("large-v3", tmp_path) is True


def test_translation_disabled_by_default_skips_second_pass_and_never_fabricates_english(monkeypatch, tmp_path):
    calls = []

    class FakeModel:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            seg = SimpleNamespace(start=0.0, end=2.2, text="मुझे दो दिन से बुखार नहीं है, सिर दर्द है।", avg_logprob=-0.12, no_speech_prob=0.01)
            info = SimpleNamespace(language="hi", language_probability=0.96)
            return iter([seg]), info

    wav = tmp_path / "hi.wav"
    wav.write_bytes(_make_wav_bytes(0.8))

    monkeypatch.setattr(stt, "get_model", lambda: FakeModel())
    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            max_audio_seconds=300,
            whisper_model="large-v3-turbo",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=False,
        ),
    )

    tr = stt.transcribe(str(wav), language_hint="hi")
    assert len(calls) == 1
    assert calls[0]["task"] == "transcribe"
    assert calls[0]["beam_size"] == 3
    assert calls[0]["vad_filter"] is True
    assert calls[0]["language"] == "hi"
    assert tr.text == "मुझे दो दिन से बुखार नहीं है, सिर दर्द है।"
    assert tr.english == ""
    assert tr.translation_ms == 0
    assert "translation_skipped" in tr.flags
    assert "translation_unavailable" in tr.flags

    # Verify uploads._run preserves original transcript and sets translated_text=None without bogus negation mismatch
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: tr)
    up_res = U._run("audio", wav, "hi")
    assert up_res["extracted_text"] == "मुझे दो दिन से बुखार नहीं है, सिर दर्द है।"
    assert up_res["translated_text"] is None
    assert "translation_skipped" in up_res["flags"]
    assert "translation_requires_review" in up_res["flags"]
    assert not any(f.startswith("negation_count_mismatch") for f in up_res["flags"])


def test_optional_user_triggered_translation_runs_second_pass_when_requested(monkeypatch, tmp_path):
    calls = []

    class FakeModel:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            if kwargs["task"] == "translate":
                txt = "I do not have fever for two days, I have headache."
            else:
                txt = "मला दोन दिवसांपासून ताप नाही, डोकेदुखी आहे."
            seg = SimpleNamespace(start=0.0, end=2.0, text=txt, avg_logprob=-0.1, no_speech_prob=0.01)
            info = SimpleNamespace(language="mr", language_probability=0.95)
            return iter([seg]), info

    wav = tmp_path / "mr.wav"
    wav.write_bytes(_make_wav_bytes(0.8))

    monkeypatch.setattr(stt, "get_model", lambda: FakeModel())
    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            max_audio_seconds=300,
            whisper_model="large-v3-turbo",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=False,  # disabled by default, but overridden per call
        ),
    )

    tr = stt.transcribe(str(wav), language_hint="mr", translate=True, beam_size=5)
    assert [c["task"] for c in calls] == ["transcribe", "translate"]
    assert all(c["beam_size"] == 5 for c in calls)
    assert tr.text == "मला दोन दिवसांपासून ताप नाही, डोकेदुखी आहे."
    assert tr.english == "I do not have fever for two days, I have headache."
    assert "translation_skipped" not in tr.flags


def test_audio_file_can_be_uploaded_again_after_removal_or_discard(app, monkeypatch):
    def fake_transcribe(path: str, language_hint: str | None = None, **kw):
        return stt.Transcript(
            language=language_hint if language_hint in ("en", "hi", "mr", "bn") else "en",
            language_probability=0.95,
            segments=[stt.Segment(0.0, 1.5, "I have a headache for two days and no fever.", 0.9, 0.01)],
            english_segments=[stt.Segment(0.0, 1.5, "I have a headache for two days and no fever.", 0.9, 0.01)],
            duration=1.5,
            flags=[],
        )

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)

    pat = Api(app)
    assert pat.signup("reupload-pat@demo-clinic.org", name="Sameer", age=34, mobile="+919833344455").status_code == 201
    assert consent(pat).status_code == 201
    cid = pat.post("/api/cases").json()["id"]
    wav_bytes = _make_wav_bytes(duration_sec=0.8)

    # 1. Upload audio -> delete it via DELETE /uploads/{uid} -> upload identical bytes again
    r1 = pat.c.post(f"/api/cases/{cid}/uploads", files={"file": ("voice.wav", wav_bytes, "audio/wav")}, data={"language": "en"}, headers=pat._h())
    assert r1.status_code == 202 and r1.json()["duplicate"] is False
    uid1 = r1.json()["upload"]["id"]

    del_res = pat.delete(f"/api/cases/{cid}/uploads/{uid1}")
    assert del_res.status_code == 200
    assert pat.get(f"/api/cases/{cid}").json()["uploads"] == []

    r2 = pat.c.post(f"/api/cases/{cid}/uploads", files={"file": ("voice.wav", wav_bytes, "audio/wav")}, data={"language": "en"}, headers=pat._h())
    assert r2.status_code == 202 and r2.json()["duplicate"] is False
    uid2 = r2.json()["upload"]["id"]
    assert uid2 != uid1
    assert len(pat.get(f"/api/cases/{cid}").json()["uploads"]) == 1

    # 2. Discard the upload via POST /uploads/{uid}/discard -> upload identical bytes again
    disc_res = pat.post(f"/api/cases/{cid}/uploads/{uid2}/discard", {})
    assert disc_res.status_code == 200
    assert pat.get(f"/api/cases/{cid}").json()["uploads"] == []

    r3 = pat.c.post(f"/api/cases/{cid}/uploads", files={"file": ("voice.wav", wav_bytes, "audio/wav")}, data={"language": "hi"}, headers=pat._h())
    assert r3.status_code == 202 and r3.json()["duplicate"] is False
    uid3 = r3.json()["upload"]["id"]
    assert uid3 != uid2
    uploads_now = pat.get(f"/api/cases/{cid}").json()["uploads"]
    assert len(uploads_now) == 1
    assert uploads_now[0]["id"] == uid3
    assert uploads_now[0]["detected_language"] == "hi"


BENGALI_SENTENCE = "ঢাকা শহর মসজিদের শহর নামে সুপরিচিত। এখানে এক হাজারের বেশি মসজিদ আছে।"
BENGALI_PUNCT_CHANGED = "দাকা শহর মজজিদের শহর নামে সুপরিচিত. এখানে এখ হাজারেরের বেশি মজজজিদাছে."
BENGALI_ENGLISH = "Dhaka city is well known as the city of mosques. Here there are more than one thousand mosques."


def test_is_valid_english_translation_rejects_bengali_and_unchanged_text_but_accepts_proper_nouns():
    from ..services import extraction

    # 1. Exact copy or punctuation-only change of Bengali sentence is rejected
    ok1, flags1 = extraction.is_valid_english_translation(
        BENGALI_SENTENCE,
        "ঢাকা শহর মসজিদের শহর নামে সুপরিচিত. এখানে এক হাজারের বেশি মসজিদ আছে.",
        source_lang="bn",
    )
    assert ok1 is False
    assert "translation_unchanged_from_source" in flags1
    assert "translation_non_english_output" in flags1
    assert "translation_unavailable" in flags1

    # 2. Whisper large-v3-turbo task="translate" output that remains in Bengali script is rejected
    ok2, flags2 = extraction.is_valid_english_translation(
        BENGALI_SENTENCE,
        BENGALI_PUNCT_CHANGED,
        source_lang="bn",
    )
    assert ok2 is False
    assert "translation_non_english_output" in flags2
    assert "translation_unavailable" in flags2
    assert "translation_requires_review" in flags2

    # 3. Genuine English translation containing non-English proper nouns ("Dhaka", "Rahim", "Kolkata") is accepted
    ok3, flags3 = extraction.is_valid_english_translation(
        BENGALI_SENTENCE,
        BENGALI_ENGLISH,
        source_lang="bn",
    )
    assert ok3 is True
    assert flags3 == []

    ok4, flags4 = extraction.is_valid_english_translation(
        BENGALI_SENTENCE,
        "Patient Mohammad Rahim from Dhaka took Paracetamol 500mg and has no fever [অস্পষ্ট].",
        source_lang="bn",
    )
    assert ok4 is True
    assert flags4 == []


def test_bengali_audio_translation_end_to_end_backend_db_and_api_response(app, fake_llm, monkeypatch, tmp_path):
    from ..db import session_scope
    from ..models import Upload

    calls = []

    class FakeBengaliWhisperModel:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            # Simulate faster-whisper large-v3-turbo returning Bengali with Latin periods on task="translate"
            txt = BENGALI_PUNCT_CHANGED if kwargs.get("task") == "translate" else BENGALI_SENTENCE
            seg = SimpleNamespace(start=0.0, end=4.2, text=txt, avg_logprob=-0.15, no_speech_prob=0.01)
            info = SimpleNamespace(language="bn", language_probability=0.98)
            return iter([seg]), info

    monkeypatch.setattr(stt, "get_model", lambda: FakeBengaliWhisperModel())
    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            max_audio_seconds=300,
            whisper_model="large-v3-turbo",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=True,
        ),
    )

    pat = Api(app)
    assert pat.signup("bn-pat@demo-clinic.org", name="Tanvir", age=31, mobile="+919844455566").status_code == 201
    assert consent(pat).status_code == 201
    cid = pat.post("/api/cases").json()["id"]
    wav_bytes = _make_wav_bytes(duration_sec=1.0)

    r = pat.c.post(
        f"/api/cases/{cid}/uploads",
        files={"file": ("bengali.wav", wav_bytes, "audio/wav")},
        data={"language": "bn"},
        headers=pat._h(),
    )
    assert r.status_code == 202
    uid = r.json()["upload"]["id"]

    # 1. Verify Whisper ran transcribe + translate, and the non-English Whisper translate output was replaced by genuine English
    assert [c["task"] for c in calls] == ["transcribe", "translate"]

    # 2. Verify database persistence
    with session_scope() as db:
        up_row = db.get(Upload, uid)
        assert up_row is not None
        assert up_row.detected_language == "bn"
        assert up_row.extracted_text == BENGALI_SENTENCE
        assert up_row.translated_text == BENGALI_ENGLISH
        assert up_row.translated_text != up_row.extracted_text
        assert not any(0x0980 <= ord(ch) <= 0x09FF for ch in (up_row.translated_text or ""))

    # 3. Verify API response mapping on GET /api/cases/{cid}/uploads/{uid} and GET /api/cases/{cid}
    up_api = pat.get(f"/api/cases/{cid}/uploads/{uid}").json()
    assert up_api["status"] == "needs_confirmation"
    assert up_api["detected_language"] == "bn"
    assert up_api["extracted_text"] == BENGALI_SENTENCE
    assert up_api["translated_text"] == BENGALI_ENGLISH
    assert "translation_non_english_output" not in up_api["flags"]
    assert "translation_unavailable" not in up_api["flags"]

    case_api = pat.get(f"/api/cases/{cid}").json()
    assert len(case_api["uploads"]) == 1
    assert case_api["uploads"][0]["extracted_text"] == BENGALI_SENTENCE
    assert case_api["uploads"][0]["translated_text"] == BENGALI_ENGLISH


def test_bengali_translation_failure_never_copies_bengali_to_english_and_supports_retry(app, fake_llm, monkeypatch):
    from ..db import session_scope
    from ..models import Upload

    class FakeBrokenBengaliWhisperModel:
        def transcribe(self, path, **kwargs):
            txt = BENGALI_PUNCT_CHANGED if kwargs.get("task") == "translate" else BENGALI_SENTENCE
            seg = SimpleNamespace(start=0.0, end=4.0, text=txt, avg_logprob=-0.15, no_speech_prob=0.01)
            info = SimpleNamespace(language="bn", language_probability=0.97)
            return iter([seg]), info

    monkeypatch.setattr(stt, "get_model", lambda: FakeBrokenBengaliWhisperModel())
    monkeypatch.setattr(
        stt,
        "get_settings",
        lambda: SimpleNamespace(
            max_audio_seconds=300,
            whisper_model="large-v3-turbo",
            whisper_beam_size=3,
            whisper_vad_filter=True,
            whisper_translate=True,
        ),
    )

    # Force LLM fallback to also return untranslated Bengali initially
    fake_llm.translations["ঢাকা শহর"] = BENGALI_PUNCT_CHANGED

    pat = Api(app)
    assert pat.signup("bn-fail-pat@demo-clinic.org", name="Nadia", age=28, mobile="+919855566677").status_code == 201
    assert consent(pat).status_code == 201
    cid = pat.post("/api/cases").json()["id"]
    wav_bytes = _make_wav_bytes(duration_sec=1.0)

    r = pat.c.post(
        f"/api/cases/{cid}/uploads",
        files={"file": ("bengali_fail.wav", wav_bytes, "audio/wav")},
        data={"language": "bn"},
        headers=pat._h(),
    )
    assert r.status_code == 202
    uid = r.json()["upload"]["id"]

    # Verify Bengali is preserved in extracted_text, translated_text is None (never copied), and failure flags are set
    up_api = pat.get(f"/api/cases/{cid}/uploads/{uid}").json()
    assert up_api["extracted_text"] == BENGALI_SENTENCE
    assert up_api["translated_text"] is None
    assert "translation_non_english_output" in up_api["flags"]
    assert "translation_unavailable" in up_api["flags"]
    assert "translation_requires_review" in up_api["flags"]

    with session_scope() as db:
        up_row = db.get(Upload, uid)
        assert up_row.extracted_text == BENGALI_SENTENCE
        assert up_row.translated_text is None

    # Now restore the translator and trigger retry via POST /api/cases/{cid}/uploads/{uid}/retry
    fake_llm.translations["ঢাকা শহর"] = BENGALI_ENGLISH
    retry_res = pat.post(f"/api/cases/{cid}/uploads/{uid}/retry", {"language": "bn", "translate": True})
    assert retry_res.status_code == 202

    up_retried = pat.get(f"/api/cases/{cid}/uploads/{uid}").json()
    assert up_retried["extracted_text"] == BENGALI_SENTENCE
    assert up_retried["translated_text"] == BENGALI_ENGLISH
    assert "translation_non_english_output" not in up_retried["flags"]
    assert "translation_unavailable" not in up_retried["flags"]
