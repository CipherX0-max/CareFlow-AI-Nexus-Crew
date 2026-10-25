"""Upload validation, secure storage and background processing (OCR / transcription). Files are stored under an opaque
name outside any static directory and are only streamed through an authorised API route."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import Case, Upload
from . import extraction, ocr, stt

log = logging.getLogger("careflow.uploads")


class UploadRejected(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def sniff(data: bytes) -> tuple[str, str, str]:
    """Return (kind, mime, ext) from file CONTENT (never trust the extension or the client MIME type)."""
    h = data[:16]
    if h.startswith(b"%PDF-"):
        return "pdf", "application/pdf", ".pdf"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", ".png"
    if h.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", ".jpg"
    if h[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp", ".webp"
    if h[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio", "audio/wav", ".wav"
    if h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0):
        return "audio", "audio/mpeg", ".mp3"
    if data[4:8] == b"ftyp":
        return "audio", "audio/mp4", ".m4a"
    if h.startswith(b"OggS"):
        return "audio", "audio/ogg", ".ogg"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio", "audio/webm", ".webm"
    if h.startswith(b"fLaC"):
        return "audio", "audio/flac", ".flac"
    raise UploadRejected("unsupported_type", "Unsupported file type. Upload a PDF, a PNG/JPEG/WebP photo, or WAV/MP3/M4A/OGG/WebM/FLAC audio.", 415)


def safe_display_name(name: str) -> str:
    name = os.path.basename((name or "upload").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "", name).strip() or "upload"
    return name[:120]


def case_dir(case_id: str) -> Path:
    d = get_settings().upload_dir / case_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(up: Upload) -> Path:
    base = (get_settings().upload_dir / up.case_id).resolve()
    p = (base / up.stored_name).resolve()
    if base not in p.parents:  # path traversal guard (defence in depth: stored_name is server-generated)
        raise UploadRejected("bad_path", "Invalid file reference.", 400)
    return p


def store(db: Session, case: Case, data: bytes, filename: str, existing_count: int) -> tuple[Upload, bool]:
    s = get_settings()
    if not data:
        raise UploadRejected("empty_file", "The file is empty.")
    kind, mime, ext = sniff(data)
    limit = (s.max_audio_mb if kind == "audio" else s.max_upload_mb) * 1024 * 1024
    if len(data) > limit:
        raise UploadRejected("file_too_large", f"File exceeds the {limit // (1024 * 1024)} MB limit.", 413)
    if existing_count >= s.max_uploads_per_case:
        raise UploadRejected("too_many_files", f"At most {s.max_uploads_per_case} files can be attached to one case.", 409)
    digest = hashlib.sha256(data).hexdigest()
    dup = db.query(Upload).filter(Upload.case_id == case.id, Upload.sha256 == digest, Upload.status != "discarded").first()
    if dup:
        return dup, False
    stored = uuid.uuid4().hex + ext
    p = case_dir(case.id) / stored
    p.write_bytes(data)
    os.chmod(p, 0o600)
    up = Upload(case_id=case.id, kind=kind, original_filename=safe_display_name(filename), stored_name=stored, mime=mime, size_bytes=len(data),
                sha256=digest, status="pending")
    db.add(up)
    db.flush()
    return up, True


def process_upload(upload_id: str, selected_language: str | None = None, translate: bool | None = None) -> None:
    """Runs in a background thread. Never raises; failures are recorded on the upload so the UI can say so honestly."""
    try:
        with session_scope() as db:
            up = db.get(Upload, upload_id)
            if not up or up.status not in ("pending", "failed"):
                return
            up.status, up.error = "processing", None
            kind, path = up.kind, path_for(up)
            if kind == "audio":
                up.selected_language = selected_language or "auto"
        result = _run(kind, path, selected_language, translate=translate)
        with session_scope() as db:
            up = db.get(Upload, upload_id)
            if up:
                for k, v in result.items():
                    setattr(up, k, v)
        if (
            kind == "audio"
            and result.get("status") == "needs_confirmation"
            and result.get("detected_language") not in (None, "en")
            and not result.get("translated_text")
            and translate is not False
            and "translation_non_english_output" not in (result.get("flags") or [])
        ):
            orig_txt = result.get("extracted_text") or ""
            det_lang = result.get("detected_language") or ""
            t_en, t_flags = extraction.translate_only(orig_txt, source_lang=det_lang)
            with session_scope() as db:
                up = db.get(Upload, upload_id)
                if up and not up.translated_text:
                    cur_flags = list(up.flags or [])
                    if t_en:
                        up.translated_text = t_en
                        cur_flags = [
                            f for f in cur_flags
                            if f not in ("translation_skipped", "translation_unavailable", "translation_requires_review", "translation_non_english_output", "translation_unchanged_from_source")
                        ]
                        cur_flags += extraction.translation_negation_check(orig_txt, t_en)
                        cur_flags += extraction.reading_flags(orig_txt, t_en, "asr")
                        cur_flags += extraction.translation_quality_flags(t_en, orig_txt, source_lang=det_lang)
                        cur_flags += [f for f in t_flags if f != "translation_unavailable"]
                        up.flags = sorted(set(cur_flags))
                    else:
                        cur_flags = [f for f in cur_flags if f != "translation_skipped"] + t_flags
                        up.flags = sorted(set(cur_flags))
    except Exception as exc:  # noqa: BLE001
        log.exception("upload processing crashed for %s", upload_id)
        with session_scope() as db:
            up = db.get(Upload, upload_id)
            if up:
                up.status, up.error = "failed", "Processing failed unexpectedly. Please try again or describe the content in text."


def _run(kind: str, path: Path, selected_language: str | None = None, translate: bool | None = None) -> dict:
    try:
        if kind == "audio":
            if translate is None:
                tr = stt.transcribe(str(path), language_hint=selected_language)
            else:
                try:
                    tr = stt.transcribe(str(path), language_hint=selected_language, translate=translate)
                except TypeError:
                    tr = stt.transcribe(str(path), language_hint=selected_language)
            segs = [{"start": x.start, "end": x.end, "text": x.text.strip(), "confidence": x.confidence, "no_speech_prob": x.no_speech_prob} for x in tr.segments]
            flags = list(tr.flags)
            en_text: str | None = None
            if tr.language != "en":
                cand_en = (tr.english or "").strip()
                if cand_en:
                    ok_en, val_flags = extraction.is_valid_english_translation(tr.text, cand_en, source_lang=tr.language)
                    if ok_en:
                        en_text = cand_en
                    else:
                        t_en, t_flags = extraction.translate_only(tr.text, source_lang=tr.language)
                        if t_en:
                            en_text = t_en
                            flags += [f for f in t_flags if f != "translation_unavailable"]
                        else:
                            flags += val_flags
                if en_text:
                    flags = [
                        f for f in flags
                        if f not in ("translation_unavailable", "translation_non_english_output", "translation_unchanged_from_source", "translation_requires_review", "translation_skipped")
                    ]
                    flags += extraction.translation_negation_check(tr.text, en_text)
                    flags += extraction.reading_flags(tr.text, en_text, "asr")
                    flags += extraction.translation_quality_flags(en_text, tr.text, source_lang=tr.language)
                else:
                    flags += extraction.translation_quality_flags("", tr.text, source_lang=tr.language)
            if any(ord(ch) > 0x900 for ch in tr.text) and any("a" <= ch.lower() <= "z" for ch in tr.text):
                flags.append("mixed_scripts_possible_code_switching")
            return dict(status="needs_confirmation", method="faster-whisper", detected_language=tr.language, language_confidence=tr.language_probability,
                        extracted_text=tr.text, translated_text=en_text if tr.language != "en" else None, segments=segs, flags=sorted(set(flags)),
                        mean_confidence=tr.mean_confidence)
        data = path.read_bytes()
        res = ocr.extract_pdf(data) if kind == "pdf" else ocr.extract_image(data)
        pages = [{"page": p.page, "text": p.text, "method": p.method, "confidence": p.confidence, "flags": p.flags} for p in res.pages]
        if not any(p.text.strip() for p in res.pages):
            return dict(status="failed", method=res.method, error="No readable text was found. Please upload a clearer, well-lit, upright photo or a text-based PDF, or type the key details.",
                        segments=pages, flags=res.flags)
        return dict(status="needs_confirmation", method=res.method, extracted_text=res.text, segments=pages, flags=res.flags, mean_confidence=res.mean_confidence)
    except ocr.DocumentError as exc:
        return dict(status="failed", error=exc.message, flags=[exc.code])
    except stt.SpeechError as exc:
        return dict(status="failed", error=exc.message, flags=[exc.code])
