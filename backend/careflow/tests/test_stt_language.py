from types import SimpleNamespace
from pathlib import Path

import pytest

from ..services import extraction, stt, uploads


class FakeModel:
    def __init__(self, detected="hi", probability=0.9, text="मुझे बुखार नहीं है"):
        self.calls = []
        self.detected, self.probability, self.text = detected, probability, text

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        txt = "I do not have fever" if kwargs["task"] == "translate" else self.text
        seg = SimpleNamespace(start=0, end=1, text=txt, avg_logprob=-0.1, no_speech_prob=0.01)
        return iter([seg]), SimpleNamespace(language=self.detected, language_probability=self.probability)


@pytest.mark.parametrize("language", ["en", "hi", "mr", "bn"])
def test_selected_language_reaches_whisper(language, monkeypatch):
    model = FakeModel(detected=language)
    monkeypatch.setattr(stt, "probe_duration", lambda _: 1.0)
    monkeypatch.setattr(stt, "get_model", lambda: model)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(max_audio_seconds=300, whisper_translate=True))
    stt.transcribe("synthetic.wav", language)
    assert model.calls[0]["language"] == language
    assert model.calls[0]["task"] == "transcribe"


@pytest.mark.parametrize("language", ["hinglish", "auto", None])
def test_auto_strategy_passes_no_language_hint(language, monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(stt, "probe_duration", lambda _: 1.0)
    monkeypatch.setattr(stt, "get_model", lambda: model)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(max_audio_seconds=300, whisper_translate=True))
    stt.transcribe("synthetic.wav", language)
    assert model.calls[0]["language"] is None


def test_invalid_language_rejected_and_mismatch_warned(monkeypatch):
    with pytest.raises(stt.SpeechError, match="Choose English"):
        stt.transcribe("synthetic.wav", "cy")
    model = FakeModel(detected="cy", probability=0.3)
    monkeypatch.setattr(stt, "probe_duration", lambda _: 1.0)
    monkeypatch.setattr(stt, "get_model", lambda: model)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(max_audio_seconds=300, whisper_translate=True))
    tr = stt.transcribe("synthetic.wav", "mr")
    assert "language_uncertain" in tr.flags
    assert "selected_detected_language_mismatch" in tr.flags


def test_translation_quality_flags_repetition_empty_and_negation():
    assert "translation_requires_review" in extraction.translation_quality_flags("")
    assert "translation_repetition" in extraction.translation_quality_flags("pain pain pain pain pain pain pain pain")
    assert extraction.translation_negation_check("fever nahi", "I have fever")


def test_failed_transcription_is_reported_as_failed_upload(monkeypatch):
    def fail(*args, **kwargs):
        raise stt.SpeechError("no_speech", "No speech was detected.")
    monkeypatch.setattr(stt, "transcribe", fail)
    result = uploads._run("audio", Path("synthetic.wav"), "en")
    assert result["status"] == "failed"
    assert result["flags"] == ["no_speech"]


def test_audio_pipeline_keeps_original_and_translation_separate(monkeypatch):
    tr = stt.Transcript("hi", 0.9, [stt.Segment(0, 1, "मुझे बुखार नहीं है", .9, .01)],
                        [stt.Segment(0, 1, "I do not have fever", .9, .01)], 1.0)
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: tr)
    result = uploads._run("audio", Path("synthetic.wav"), "hi")
    assert result["extracted_text"] == "मुझे बुखार नहीं है"
    assert result["translated_text"] == "I do not have fever"


def test_translation_can_be_skipped_without_losing_original_or_hiding_warning(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(stt, "probe_duration", lambda _: 1.0)
    monkeypatch.setattr(stt, "get_model", lambda: model)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(max_audio_seconds=300, whisper_translate=False))
    tr = stt.transcribe("synthetic.wav", "hi")
    assert tr.text == "मुझे बुखार नहीं है"
    assert tr.english == ""
    assert len(model.calls) == 1 and model.calls[0]["task"] == "transcribe"
    assert "translation_skipped" in tr.flags and "translation_unavailable" in tr.flags


def test_translation_remains_available_when_enabled(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(stt, "probe_duration", lambda _: 1.0)
    monkeypatch.setattr(stt, "get_model", lambda: model)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(max_audio_seconds=300, whisper_translate=True))
    tr = stt.transcribe("synthetic.wav", "hi")
    assert tr.text == "मुझे बुखार नहीं है" and tr.english == "I do not have fever"
    assert [call["task"] for call in model.calls] == ["transcribe", "translate"]


def test_skipped_english_rendering_is_returned_as_unavailable(monkeypatch):
    tr = stt.Transcript("mr", 0.9, [stt.Segment(0, 1, "मला ताप नाही", .9, .01)], [], 1.0,
                        ["translation_skipped", "translation_unavailable"])
    monkeypatch.setattr(stt, "transcribe", lambda path, language_hint=None: tr)
    result = uploads._run("audio", Path("synthetic.wav"), "mr")
    assert result["extracted_text"] == "मला ताप नाही"
    assert result["translated_text"] is None
    assert "translation_skipped" in result["flags"]
    assert "translation_requires_review" in result["flags"]


def test_model_is_loaded_once_and_reused(monkeypatch, tmp_path):
    from types import ModuleType
    import sys

    calls = []
    fake_module = ModuleType("faster_whisper")
    fake_module.WhisperModel = lambda *args, **kwargs: calls.append((args, kwargs)) or object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_load_error", None)
    monkeypatch.setattr(stt, "get_settings", lambda: SimpleNamespace(stt_enabled=True, whisper_model="small", whisper_device="cpu",
                         whisper_compute_type="int8", whisper_model_dir=tmp_path, whisper_local_only=True))
    first = stt.get_model()
    second = stt.get_model()
    assert first is second
    assert len(calls) == 1
    assert calls[0][1]["device"] == "cpu" and calls[0][1]["compute_type"] == "int8"
