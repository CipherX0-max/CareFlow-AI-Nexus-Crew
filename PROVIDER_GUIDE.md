# Local models guide

CareFlow AI runs every AI capability **locally**. There are no cloud providers and **no silent fallback** to one: if a local
model is missing the feature reports it honestly and the case routes to human review.

| Capability | Component | Config (`.env`) | Install |
|---|---|---|---|
| Structured extraction + English rendering | Ollama, `qwen3:4b` (schema-constrained JSON, temperature 0) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS`, `LLM_ENABLED` | `ollama pull qwen3:4b` |
| Speech-to-text (+ language detection, Whisper translate task) | `faster-whisper` | `WHISPER_MODEL` (tiny/base/small/medium/large-v3), `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, `WHISPER_LOCAL_ONLY`, `STT_ENABLED` | downloads once to `data/models/whisper` |
| Digital PDF text | PyMuPDF | - | `pip install -r backend/requirements.txt` |
| Scans / photos | Tesseract via `pytesseract` + Pillow preprocessing | `TESSERACT_LANGS`, `TESSDATA_DIR`, `OCR_MIN_CONFIDENCE` | `brew install tesseract` + `hin.traineddata` |
| Urgency proposal | `backend/careflow/services/rules_engine.py` + `rules/demo_ruleset.json` | `RULES_PATH` | deterministic; no model |

* All prompts are centralised and versioned in `backend/careflow/services/prompts.py` (`extract-v1`, ...); each stored extraction run
  records model name, prompt version, status and any facts rejected by validation.
* The LLM never assigns urgency. Its output is validated against a strict schema and every fact must quote an excerpt that appears in
  the source text.
* Check health in the UI (Administration -> System and rules) or `GET /api/admin/system`.
* Swapping models: change `OLLAMA_MODEL` / `WHISPER_MODEL`; the integration tests in `tests/test_models_integration.py` and the
  acceptance matrix exercise them (`python -m pytest -m requires_models`).
