"""Local speech-to-text with faster-whisper. Preserves the original-language transcript with timestamps,
reports detected language + probability, and optionally obtains an English rendering via Whisper's translate task.
Nothing leaves the machine; when the model cannot be loaded we raise and the UI falls back to text."""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

_model = None
_model_key: tuple | None = None
_lock = threading.Lock()
_load_error: str | None = None
_load_ms: int = 0
_load_count: int = 0


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
    transcription_ms: int = 0
    translation_ms: int = 0
    model: str = ""
    beam_size: int = 3

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    @property
    def english(self) -> str:
        return " ".join(s.text.strip() for s in self.english_segments).strip()

    @property
    def mean_confidence(self) -> float:
        return round(sum(s.confidence for s in self.segments) / len(self.segments), 3) if self.segments else 0.0


def _dedupe_segments(raw_segments: list[Segment]) -> tuple[list[Segment], bool]:
    """Remove consecutive identical or hallucinated loop segments while preserving legitimate speech."""
    cleaned: list[Segment] = []
    had_duplicates = False
    for seg in raw_segments:
        txt = re.sub(r"\s+", " ", (seg.text or "")).strip()
        if not txt:
            continue
        # collapse internal phrase loops inside a single segment (e.g., "I have fever. I have fever. I have fever." or unpunctuated loops)
        parts = [p.strip() for p in re.split(r"(?<=[.!?।])\s+", txt) if p.strip()]
        if len(parts) >= 3:
            dedup_parts: list[str] = []
            for p in parts:
                if not dedup_parts or dedup_parts[-1].casefold() != p.casefold():
                    dedup_parts.append(p)
                else:
                    had_duplicates = True
            txt = " ".join(dedup_parts)
        collapsed = re.sub(r"\b(\S+(?:\s+\S+){1,6})(?:\s+\1){2,}\b", r"\1", txt, flags=re.IGNORECASE)
        if collapsed != txt:
            txt = collapsed
            had_duplicates = True
        norm = txt.casefold()
        if cleaned and cleaned[-1].text.strip().casefold() == norm:
            had_duplicates = True
            continue
        cleaned.append(Segment(seg.start, seg.end, txt, seg.confidence, seg.no_speech_prob))
    return cleaned, had_duplicates


def _is_model_cached(model_name: str, model_dir: Path) -> bool:
    """Check whether the exact configured Whisper model is cached on disk without confusing large-v3 with large-v3-turbo."""
    if not model_dir or not model_dir.exists():
        return False
    try:
        from faster_whisper.utils import _MODELS
        repo = _MODELS.get(model_name)
        if repo:
            hf_dir = model_dir / f"models--{repo.replace('/', '--')}"
            if hf_dir.exists():
                return True
    except Exception:  # noqa: BLE001
        pass
    for item in model_dir.iterdir():
        name = item.name
        if name == model_name or name.endswith(f"--{model_name}") or name.endswith(f"-{model_name}"):
            return True
        if model_name in name and not (not model_name.endswith("turbo") and "turbo" in name):
            return True
    return False


def get_model():
    global _model, _model_key, _load_error, _load_ms, _load_count
    s = get_settings()
    if not s.stt_enabled:
        raise SpeechError("stt_disabled", "Speech transcription is disabled in this deployment. Please type your symptoms instead.")
    key = (s.whisper_model, s.whisper_device, s.whisper_compute_type, str(Path(s.whisper_model_dir)), s.whisper_local_only)
    with _lock:
        if _model is not None and _model_key == key:
            return _model
        try:
            import faster_whisper
            from faster_whisper import WhisperModel
            known_models = getattr(getattr(faster_whisper, "utils", None), "_MODELS", None)
            if isinstance(known_models, dict) and s.whisper_model not in known_models and not Path(s.whisper_model).exists():
                raise ValueError(f"Unsupported faster-whisper model identifier: '{s.whisper_model}'")
            Path(s.whisper_model_dir).mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            _model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type,
                                  download_root=str(s.whisper_model_dir), local_files_only=s.whisper_local_only)
            _load_ms = int((time.perf_counter() - t0) * 1000)
            _load_count += 1
            _model_key = key
            _load_error = None
        except Exception as exc:  # noqa: BLE001 -- never silently fall back to a different model
            _model = None
            _model_key = None
            _load_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise SpeechError("stt_model_unavailable",
                              "The local speech model is not available, so this recording could not be transcribed. Please type your symptoms instead.") from exc
    return _model


def status() -> dict:
    s = get_settings()
    beam_size = int(getattr(s, "whisper_beam_size", 3))
    vad_filter = bool(getattr(s, "whisper_vad_filter", True))
    translate_enabled = bool(getattr(s, "whisper_translate", False))
    if not s.stt_enabled:
        return {
            "available": False,
            "loaded": False,
            "model": s.whisper_model,
            "device": s.whisper_device,
            "compute_type": s.whisper_compute_type,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "translate_enabled": translate_enabled,
            "detail": "disabled by configuration",
        }
    cached = _is_model_cached(s.whisper_model, Path(s.whisper_model_dir))
    return {
        "available": _model is not None or cached,
        "loaded": _model is not None,
        "model": s.whisper_model,
        "device": s.whisper_device,
        "compute_type": s.whisper_compute_type,
        "beam_size": beam_size,
        "vad_filter": vad_filter,
        "translate_enabled": translate_enabled,
        "load_ms": _load_ms,
        "load_count": _load_count,
        "detail": "ready" if (_model is not None or cached) else f"model '{s.whisper_model}' not downloaded yet (downloads on first use unless WHISPER_LOCAL_ONLY=true)",
        "last_error": _load_error,
    }


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


SUPPORTED_LANGUAGE_HINTS = {"en", "hi", "mr", "bn", "hinglish", "auto"}


def transcribe(
    path: str,
    language_hint: str | None = None,
    *,
    translate: bool | None = None,
    beam_size: int | None = None,
) -> Transcript:
    if language_hint not in (None, *SUPPORTED_LANGUAGE_HINTS):
        raise SpeechError("invalid_language", "Choose English, Hindi, Marathi, Bengali, Hinglish, or automatic detection.")
    s = get_settings()
    duration = probe_duration(path)
    if duration <= 0.3:
        raise SpeechError("empty_audio", "The recording is empty or too short.")
    if duration > s.max_audio_seconds:
        raise SpeechError("audio_too_long", f"Recordings are limited to {s.max_audio_seconds // 60} minutes.")
    # Keep the legacy omitted-field behavior automatic. Hinglish/auto intentionally have no hint.
    whisper_language = language_hint if language_hint in {"en", "hi", "mr", "bn"} else None
    eff_beam_size = int(beam_size if beam_size is not None else getattr(s, "whisper_beam_size", 3))
    eff_vad_filter = bool(getattr(s, "whisper_vad_filter", True))
    eff_translate = bool(translate if translate is not None else getattr(s, "whisper_translate", False))
    model_name = str(getattr(s, "whisper_model", "large-v3-turbo"))
    model = get_model()
    flags: list[str] = []
    t_transcribe_ms = 0
    t_translate_ms = 0
    try:
        t0 = time.perf_counter()
        segs, info = model.transcribe(
            path,
            task="transcribe",
            language=whisper_language,
            beam_size=eff_beam_size,
            vad_filter=eff_vad_filter,
            temperature=0.0,
            condition_on_previous_text=False,
        )
        raw_segments = [Segment(x.start, x.end, x.text, round(math.exp(x.avg_logprob), 3), round(x.no_speech_prob, 3)) for x in segs]
        t_transcribe_ms = int((time.perf_counter() - t0) * 1000)
        segments, had_dup_orig = _dedupe_segments(raw_segments)
        if had_dup_orig:
            flags.append("asr_repeated_segments_removed")
        if not segments or not " ".join(x.text for x in segments).strip():
            raise SpeechError("no_speech", "No speech was detected in the recording. Please try again in a quieter place or type your symptoms.")
        detected_lang = getattr(info, "language", None) or whisper_language or "en"
        orig_text = " ".join(x.text.strip() for x in segments).strip()
        english_segments: list[Segment] = segments if detected_lang == "en" else []
        do_translate = detected_lang != "en" and eff_translate
        if do_translate:
            t1 = time.perf_counter()
            from . import extraction
            is_real_whisper_turbo = (
                type(model).__module__.startswith("faster_whisper")
                and "turbo" in model_name.lower()
            )
            whisper_val_flags: list[str] = []
            whisper_raised = False
            if not is_real_whisper_turbo:
                try:
                    esegs, _ = model.transcribe(
                        path,
                        task="translate",
                        language=detected_lang,
                        beam_size=eff_beam_size,
                        vad_filter=eff_vad_filter,
                        temperature=0.0,
                        condition_on_previous_text=False,
                    )
                    raw_en = [Segment(x.start, x.end, x.text, round(math.exp(x.avg_logprob), 3), round(x.no_speech_prob, 3)) for x in esegs]
                    english_segments, had_dup_en = _dedupe_segments(raw_en)
                    if had_dup_en:
                        flags.append("translation_repetition")
                    cand_en = " ".join(s.text.strip() for s in english_segments).strip()
                    ok_en, whisper_val_flags = extraction.is_valid_english_translation(orig_text, cand_en, source_lang=detected_lang)
                    if not ok_en:
                        english_segments = []
                except Exception:  # noqa: BLE001 -- never fail the original transcript if translation fails; mark for human review
                    whisper_raised = True
                    english_segments = []
                    flags.extend(("translation_unavailable", "translation_requires_review"))
            if not english_segments and not whisper_raised:
                llm_en, llm_flags = extraction.translate_only(orig_text, source_lang=detected_lang)
                if llm_en:
                    english_segments = [Segment(segments[0].start, segments[-1].end, llm_en, segments[0].confidence, segments[0].no_speech_prob)]
                    flags.extend(f for f in llm_flags if f != "translation_unavailable")
                else:
                    flags.extend(whisper_val_flags or llm_flags or ["translation_unavailable", "translation_requires_review"])
            t_translate_ms = int((time.perf_counter() - t1) * 1000)
    except SpeechError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpeechError("transcription_failed", "Transcription failed. Please try again or type your symptoms.") from exc
    if detected_lang != "en" and not eff_translate:
        flags.extend(("translation_skipped", "translation_unavailable"))
    tr = Transcript(
        detected_lang,
        round(getattr(info, "language_probability", 1.0), 3),
        segments,
        english_segments,
        round(duration, 1),
        flags,
        transcription_ms=t_transcribe_ms,
        translation_ms=t_translate_ms,
        model=model_name,
        beam_size=eff_beam_size,
    )
    if tr.language_probability < 0.6:
        flags.append("language_uncertain")
    if whisper_language and detected_lang != whisper_language:
        flags.append("selected_detected_language_mismatch")
    if tr.mean_confidence < 0.55:
        flags.append("low_transcription_confidence")
    if any(x.confidence < 0.4 for x in segments):
        flags.append("some_segments_unclear")
    if detected_lang not in ("en", "hi", "mr", "bn"):
        flags.append("language_outside_primary_support")
    return tr
