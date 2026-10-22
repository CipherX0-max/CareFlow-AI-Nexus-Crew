"""Local speech-to-text with faster-whisper. Preserves the original-language transcript with timestamps,
reports detected language + probability, and obtains an English rendering for staff via Whisper's own
translate task. Nothing leaves the machine; when the model cannot be loaded we raise and the UI falls back to text."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from ..config import get_settings

_model = None
_lock = threading.Lock()
_load_error: str | None = None


class SpeechError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: float  # 0..1 (exp of avg logprob), a transcription-quality signal NOT a clinical one
    no_speech_prob: float


@dataclass
class Transcript:
    language: str
    language_probability: float
    segments: list[Segment]
    english_segments: list[Segment]
    duration: float
    flags: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    @property
    def english(self) -> str:
        return " ".join(s.text.strip() for s in self.english_segments).strip()

    @property
    def mean_confidence(self) -> float:
        return round(sum(s.confidence for s in self.segments) / len(self.segments), 3) if self.segments else 0.0


def get_model():
    global _model, _load_error
    s = get_settings()
    if not s.stt_enabled:
        raise SpeechError("stt_disabled", "Speech transcription is disabled in this deployment. Please type your symptoms instead.")
    with _lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
            s.whisper_model_dir.mkdir(parents=True, exist_ok=True)
            _model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type,
                                  download_root=str(s.whisper_model_dir), local_files_only=s.whisper_local_only)
            _load_error = None
        except Exception as exc:  # noqa: BLE001
            _load_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise SpeechError("stt_model_unavailable",
                              "The local speech model is not available, so this recording could not be transcribed. Please type your symptoms instead.") from exc
    return _model


def status() -> dict:
    s = get_settings()
    if not s.stt_enabled:
        return {"available": False, "model": s.whisper_model, "detail": "disabled by configuration"}
    cached = any(s.whisper_model_dir.glob(f"*{s.whisper_model}*")) if s.whisper_model_dir.exists() else False
    return {"available": _model is not None or cached, "loaded": _model is not None, "model": s.whisper_model,
            "detail": "ready" if (_model is not None or cached) else f"model '{s.whisper_model}' not downloaded yet (downloads on first use unless WHISPER_LOCAL_ONLY=true)",
            "last_error": _load_error}


def probe_duration(path: str) -> float:
    try:
        import av  # installed with faster-whisper
        with av.open(path) as c:
            if c.duration:
                return c.duration / 1_000_000
            st = c.streams.audio[0]
            return float(st.duration * st.time_base) if st.duration else 0.0
    except Exception as exc:  # noqa: BLE001
        raise SpeechError("unreadable_audio", "The audio file could not be decoded. Please record again or upload WAV/MP3/M4A.") from exc


def transcribe(path: str, language_hint: str | None = None) -> Transcript:
    s = get_settings()
    duration = probe_duration(path)
    if duration <= 0.3:
        raise SpeechError("empty_audio", "The recording is empty or too short.")
    if duration > s.max_audio_seconds:
        raise SpeechError("audio_too_long", f"Recordings are limited to {s.max_audio_seconds // 60} minutes.")
    model = get_model()
    try:
        segs, info = model.transcribe(path, task="transcribe", language=language_hint, beam_size=5, vad_filter=True, temperature=0.0,
                                      condition_on_previous_text=False)
        segments = [Segment(x.start, x.end, x.text, round(math.exp(x.avg_logprob), 3), round(x.no_speech_prob, 3)) for x in segs]
        if not segments or not " ".join(x.text for x in segments).strip():
            raise SpeechError("no_speech", "No speech was detected in the recording. Please try again in a quieter place or type your symptoms.")
        english_segments = segments
        if info.language != "en":
            esegs, _ = model.transcribe(path, task="translate", language=info.language, beam_size=5, vad_filter=True, temperature=0.0,
                                        condition_on_previous_text=False)
            english_segments = [Segment(x.start, x.end, x.text, round(math.exp(x.avg_logprob), 3), round(x.no_speech_prob, 3)) for x in esegs]
    except SpeechError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpeechError("transcription_failed", "Transcription failed. Please try again or type your symptoms.") from exc
    flags: list[str] = []
    tr = Transcript(info.language, round(info.language_probability, 3), segments, english_segments, round(duration, 1), flags)
    if info.language_probability < 0.6:
        flags.append("language_uncertain")
    if tr.mean_confidence < 0.55:
        flags.append("low_transcription_confidence")
    if any(x.confidence < 0.4 for x in segments):
        flags.append("some_segments_unclear")
    if info.language not in ("en", "hi"):
        flags.append("language_outside_primary_support")
    return tr
