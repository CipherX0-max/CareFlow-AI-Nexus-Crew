"""Evaluate local Whisper on a user-supplied synthetic audio manifest (never downloads samples)."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..services import stt


def wer(reference: str, hypothesis: str) -> float | None:
    """Token WER supporting both Latin and Indic (Devanagari/Bengali) scripts."""
    ref = re.findall(r"[\w\u0900-\u097F\u0980-\u09FF]+", (reference or "").casefold())
    hyp = re.findall(r"[\w\u0900-\u097F\u0980-\u09FF]+", (hypothesis or "").casefold())
    if not ref:
        return None
    row = list(range(len(hyp) + 1))
    for i, token in enumerate(ref, 1):
        nxt = [i]
        for j, candidate in enumerate(hyp, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (token != candidate)))
        row = nxt
    return round(row[-1] / len(ref), 4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list: {audio, selected_language, reference, clinical_terms, negations}")
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        selected = case["selected_language"]
        transcript = stt.transcribe(str((args.manifest.parent / case["audio"]).resolve()), selected)
        text = transcript.text
        low = text.casefold()
        results.append({
            "audio": case["audio"], "selected_language": selected, "detected_language": transcript.language,
            "detected_confidence": transcript.language_probability, "original_transcript": text,
            "reference_transcript": case["reference"], "wer": wer(case["reference"], text),
            "clinical_term_preserved": {term: term.casefold() in low for term in case.get("clinical_terms", [])},
            "negation_preserved": {term: term.casefold() in low for term in case.get("negations", [])},
            "english_translation": transcript.english if transcript.language != "en" else None,
            "review_warnings": transcript.flags,
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
