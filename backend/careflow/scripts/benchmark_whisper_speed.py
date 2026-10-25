"""Compare local faster-whisper models (e.g. `large-v3` vs `large-v3-turbo` and `small`) and beam sizes (`5` vs `3`)
on single synthetic audio files or a multi-language manifest (English, Hindi, Marathi).

No model download is performed when `--local-only` is set (default). Model-load and transcription times,
WER against reference transcripts, and clinical term / negation checks are reported separately.
"""
from __future__ import annotations

import argparse
import gc
import json
import re
import sys
import time
from pathlib import Path

from ..config import get_settings


def word_error_rate(reference: str, hypothesis: str) -> float | None:
    ref = re.findall(r"[\w\u0900-\u097F\u0980-\u09FF]+", (reference or "").casefold())
    hyp = re.findall(r"[\w\u0900-\u097F\u0980-\u09FF]+", (hypothesis or "").casefold())
    if not ref:
        return None
    row = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        nxt = [i]
        for j, other in enumerate(hyp, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (word != other)))
        row = nxt
    return round(row[-1] / len(ref), 4)


def _check_terms(text: str, terms: list[str]) -> dict[str, bool]:
    low = (text or "").casefold()
    return {t: t.casefold() in low for t in terms}


def run_single(audio_path: Path, reference: str, models: list[str], beam_sizes: list[int], language: str | None = None) -> dict:
    from faster_whisper import WhisperModel
    settings = get_settings()
    results = []
    for name in models:
        start = time.perf_counter()
        model = WhisperModel(
            name,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=str(settings.whisper_model_dir),
            local_files_only=True,
        )
        load_seconds = time.perf_counter() - start
        for beam_size in beam_sizes:
            start = time.perf_counter()
            segments, info = model.transcribe(
                str(audio_path.resolve()),
                task="transcribe",
                language=language if language not in (None, "auto", "hinglish") else None,
                beam_size=beam_size,
                vad_filter=True,
                temperature=0.0,
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            transcription_seconds = time.perf_counter() - start
            results.append({
                "model": name,
                "beam_size": beam_size,
                "device": settings.whisper_device,
                "compute_type": settings.whisper_compute_type,
                "model_load_seconds": round(load_seconds, 3),
                "transcription_seconds": round(transcription_seconds, 3),
                "detected_language": info.language,
                "detected_language_confidence": round(info.language_probability, 3),
                "transcript": text,
                "reference": reference,
                "word_error_rate": word_error_rate(reference, text),
            })
        del model
        gc.collect()
    return {"audio": str(audio_path.resolve()), "results": results}


def run_manifest(manifest_path: Path, models: list[str], beam_sizes: list[int]) -> dict:
    from faster_whisper import WhisperModel
    settings = get_settings()
    cases = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_summaries = []
    for name in models:
        t0 = time.perf_counter()
        model = WhisperModel(
            name,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=str(settings.whisper_model_dir),
            local_files_only=True,
        )
        load_seconds = round(time.perf_counter() - t0, 3)
        for beam_size in beam_sizes:
            sample_results = []
            for item in cases:
                audio_file = (manifest_path.parent / item["audio"]).resolve()
                sel_lang = item.get("selected_language", "auto")
                whisper_lang = sel_lang if sel_lang in ("en", "hi", "mr", "bn") else None
                t1 = time.perf_counter()
                segments, info = model.transcribe(
                    str(audio_file),
                    task="transcribe",
                    language=whisper_lang,
                    beam_size=beam_size,
                    vad_filter=True,
                    temperature=0.0,
                    condition_on_previous_text=False,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                trans_sec = round(time.perf_counter() - t1, 3)
                ref = item.get("reference", "")
                clin_terms = _check_terms(text, item.get("clinical_terms", []))
                neg_terms = _check_terms(text, item.get("negations", []))
                sample_results.append({
                    "id": item.get("id", audio_file.name),
                    "language": sel_lang,
                    "detected_language": info.language,
                    "detected_language_confidence": round(info.language_probability, 3),
                    "transcription_seconds": trans_sec,
                    "transcript": text,
                    "reference": ref,
                    "word_error_rate": word_error_rate(ref, text) if ref else None,
                    "clinical_terms_preserved": clin_terms,
                    "negations_preserved": neg_terms,
                    "all_clinical_terms_ok": all(clin_terms.values()) if clin_terms else True,
                    "all_negations_ok": all(neg_terms.values()) if neg_terms else True,
                })
            model_summaries.append({
                "model": name,
                "beam_size": beam_size,
                "device": settings.whisper_device,
                "compute_type": settings.whisper_compute_type,
                "model_load_seconds": load_seconds,
                "total_transcription_seconds": round(sum(r["transcription_seconds"] for r in sample_results), 3),
                "mean_wer": round(
                    sum(r["word_error_rate"] for r in sample_results if r["word_error_rate"] is not None)
                    / max(1, sum(1 for r in sample_results if r["word_error_rate"] is not None)),
                    4,
                ),
                "samples": sample_results,
            })
        del model
        gc.collect()
    return {"manifest": str(manifest_path.resolve()), "runs": model_summaries}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?", type=Path, help="short synthetic WAV file")
    parser.add_argument("--reference", default="", help="exact synthetic spoken words")
    parser.add_argument("--manifest", type=Path, default=None, help="JSON manifest of English, Hindi and Marathi recordings")
    parser.add_argument("--models", default="", help="comma-separated models (default: large-v3,large-v3-turbo,small)")
    parser.add_argument("--beams", default="5,3", help="comma-separated beam sizes (default: 5,3)")
    args = parser.parse_args()

    settings = get_settings()
    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else list(dict.fromkeys(("large-v3", settings.whisper_model, "small")))
    beams = [int(b.strip()) for b in args.beams.split(",") if b.strip()]

    if args.manifest:
        if not args.manifest.is_file():
            parser.error(f"manifest file not found: {args.manifest}")
        out = run_manifest(args.manifest, models, beams)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not args.audio or not args.audio.is_file():
        parser.error(f"audio file not found: {args.audio}")
    out = run_single(args.audio, args.reference, models, beams)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
