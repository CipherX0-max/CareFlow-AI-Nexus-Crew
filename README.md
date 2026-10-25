# CareFlow AI

[![CI](https://github.com/AMITSINGH098/careflow-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/AMITSINGH098/careflow-ai/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-black.svg?logo=next.js&logoColor=white)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6.svg?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)

A **local-first, human-in-the-loop** healthcare **intake and urgency-prioritisation** platform.

Patients describe symptoms by text, voice, and uploaded reports (English, Hindi, Marathi, Bengali, and Hinglish). Local models turn that into structured, source-traceable facts; a deterministic engine flags missing and contradictory information; targeted follow-up questions are generated; a transparent **rules engine** proposes an intake queue (**Urgent / Normal / Needs review**); and clinical staff inspect evidence and confirm or override proposals with a reason. Everything is audited in an append-only log.

> [!CAUTION]
> **Read this first.** This is a demonstration prototype. It is **not** a medical device, does **not** diagnose or prescribe,
> and has **no clinical validation**. The urgency rules in `backend/careflow/rules/demo_ruleset.json` are engineering
> placeholders that **no clinician has reviewed** and must be replaced by a clinician-approved ruleset before any real use.
> The emergency warning wording is likewise unreviewed. Use **synthetic data only**.
> See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for details on current capabilities and limitations.

## Workflow

```
patient intake (text · voice · PDF/photo)
   -> local extraction (lexicon + Ollama qwen3:4b, schema-validated, every fact quotes its source)
   -> contradiction detection & evidence reconciliation (conflict register)
   -> missing-information checks -> targeted follow-up questions (each with a reason)
   -> deterministic rules engine -> proposed queue (never a diagnosis)
   -> staff queue -> human review: confirm / escalate / downgrade / override (reason required)
   -> editable draft clinical note -> clinician approval -> append-only audit trail
```

Key behaviours (all covered by tests): a present red-flag symptom always yields **Urgent** regardless of missing vitals or
conflicts; missing/contradictory/unreliable information can **never** yield Normal; an AI/DB/rules failure routes to
**Needs review**, never to Normal; the LLM extracts, it never assigns urgency; conflicts are preserved, not merged;
speech/OCR/translation output is shown to the patient for correction before use.

## Repository layout

| Path | What |
|---|---|
| `backend/careflow/` | FastAPI app: `routers/` (auth, patient, staff, admin), `services/` (lexicon, extraction, ocr, stt, reconcile, followup, rules_engine, pipeline, notes), `models.py`, `migrations/` (Alembic), `rules/demo_ruleset.json`, `scripts/`, `tests/` |
| `frontend/` | Next.js 14 · React · TypeScript · Tailwind (App Router). `/api/*` is proxied to the backend (same-origin cookies) |
| `docs/` | `API.md` (generated), `mimic_ed_demo_report.md` (generated dataset evaluation) |
| `backend/app`, `backend/tests`, `backend/eval` | Superseded prototype, left untouched and unused; see "Legacy code" below |

## Setup on macOS

Prerequisites (Homebrew): `brew install python@3.12 node tesseract ffmpeg ollama` (Python 3.11-3.13 work).

```bash
# 1) Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # review it; defaults are fine for local development

# 2) Local LLM (Ollama) - structured extraction. No cloud fallback exists.
ollama serve &                  # or open the Ollama app
ollama pull qwen3:4b

# 3) OCR language data (English ships with Homebrew; Hindi is a ~1 MB download)
mkdir -p data/tessdata && cp /opt/homebrew/share/tessdata/{eng,osd}.traineddata data/tessdata/
curl -L -o data/tessdata/hin.traineddata https://github.com/tesseract-ocr/tessdata_fast/raw/main/hin.traineddata

# 4) Speech model (faster-whisper "small", downloaded once into data/models/whisper on first use or now:)
python -c "from backend.careflow.services import stt; stt.get_model()"

# 5) Frontend
cd frontend && npm install && cd ..
```

Whisper note: `faster-whisper` 1.2 needs `av<16` (pinned in requirements). Larger Whisper models (`medium`, `large-v3`) improve
code-switched and regional speech; set `WHISPER_MODEL=large-v3` in `.env` (or `WHISPER_MODEL=large-v3` in your shell). The model is
downloaded into `WHISPER_MODEL_DIR` (default `data/models/whisper`) on first load unless `WHISPER_LOCAL_ONLY=true`; ordinary
transcription requests do not select or download a second model. Model size affects disk, memory and speed: `large-v3` can need
several GB of RAM/VRAM and may be slow or unavailable on CPU-only machines. `WHISPER_DEVICE` and `WHISPER_COMPUTE_TYPE` control
hardware and quantization. No model weights are included in the repository.
On Windows, `WHISPER_DEVICE=cpu` with `WHISPER_COMPUTE_TYPE=int8` is the default CPU path; a supported CUDA setup can be selected
explicitly when available. `WHISPER_MODEL` is read once when the backend process starts and that model instance is reused for
subsequent uploads in that process (each separate worker process has its own instance). The active `.env` settings in this checkout
currently select `large-v3`, `cpu`, and `int8`; `small` is a faster lower-memory alternative. Change configuration and restart the
backend to switch model sizes.
The repeatable local-only `large-v3` versus `small` timing procedure and this checkout's measured synthetic English result are in
[`docs/whisper_speed_benchmark.md`](docs/whisper_speed_benchmark.md).

For non-English speech, the current workflow provides an English rendering to staff using a second local Whisper inference pass.
`WHISPER_TRANSLATE=true` preserves that behavior. Set `WHISPER_TRANSLATE=false` to skip the pass for faster processing. The original
transcript is still retained; the result carries visible `translation_skipped` and `translation_unavailable` review flags, and no
English text is fabricated. Translation/negation/repetition checks and the existing confirmation workflow remain in effect.

Audio intake offers English (`en`), Hindi (`hi`), Marathi (`mr`), Bengali (`bn`), Hinglish, and automatic detection. The four
explicit languages are passed to faster-whisper; Hinglish uses automatic detection because Whisper has no Hinglish language code.
Language selection guides recognition and does not guarantee accuracy. The detected language and confidence are shown separately;
low confidence or a disagreement triggers review and allows retrying with another selection. The source-language transcript remains
the original; a separate English rendering may be provided and is marked for review when empty, repetitive, or suspect around negation.
All ASR runs locally through faster-whisper. No hosted speech API is configured or added. Recording remains record → preview → upload →
transcribe; it is not live streaming. A `Transcribing…` item stays pending while its local background task loads the model/runs inference.
Check that the backend process is running, `STT_ENABLED=true`, there is sufficient memory/disk, and the model is cached or network is
available for its first download; restart a task stuck after a backend restart and retry if it reaches failed.

### Database

SQLite by default (`data/careflow.db`). The schema is managed by **Alembic** (`backend/careflow/migrations`); the app applies
migrations on startup, or run them yourself:

```bash
python -c "from backend.careflow.main import init_database; init_database()"   # same thing the server does on startup
```

Upgrade path to PostgreSQL: set `DATABASE_URL=postgresql+psycopg2://…`, `pip install psycopg2-binary`, and create the equivalent
append-only triggers for `audit_events` and `decisions` (the SQLite triggers are in the baseline migration; PostgreSQL needs a
`BEFORE UPDATE OR DELETE … RAISE EXCEPTION` trigger on each).

### Create the first administrator (no default credentials exist)

```bash
python -m backend.careflow.scripts.create_admin --email you@org.example --name "Your Name"
```

Admins create staff accounts in the UI (Administration -> Accounts). Public sign-up only ever creates **patients**.

### Seed synthetic demo data (optional)

```bash
python -m backend.careflow.scripts.seed_demo            # users + ~10 scenarios through the real API and real local models
python -m backend.careflow.scripts.seed_demo --no-llm   # faster, lexicon-only (cases will route to Needs review)
```

Random passwords for the demo accounts are printed once and saved to `data/demo_credentials.txt` (git-ignored, mode 600).

## Run

```bash
source .venv/bin/activate
uvicorn backend.careflow.main:app --port 8000        # API  (docs: http://localhost:8000/docs)
cd frontend && npm run dev                           # UI   (http://localhost:3000)
```

Production-style: `cd frontend && npm run build && npm start`. Bind Ollama and the dev servers to localhost only (the default).

## Tests and checks

```bash
source .venv/bin/activate
python -m pytest                      # backend (current suite; optional model tests auto-skip if unavailable)
python -m pytest -m requires_models   # only the real-model integration tests
ruff check backend/careflow           # backend lint
cd frontend
npm run typecheck && npm run lint && npm test && npm run build
```

### Local speech benchmark

Create a JSON manifest and place synthetic audio files beside it. Use the same exact synthetic utterances for each language, with
known symptom terms and both positive and negative statements; avoid real patient data. Example manifest:

```json
[
  {"audio":"english.wav","selected_language":"en","reference":"I have fever but no cough","clinical_terms":["fever","cough"],"negations":["no cough"]},
  {"audio":"hindi.wav","selected_language":"hi","reference":"मुझे बुखार है लेकिन खांसी नहीं है","clinical_terms":["बुखार","खांसी"],"negations":["नहीं"]},
  {"audio":"marathi.wav","selected_language":"mr","reference":"मला ताप आहे पण खोकला नाही","clinical_terms":["ताप","खोकला"],"negations":["नाही"]},
  {"audio":"bengali.wav","selected_language":"bn","reference":"আমার জ্বর আছে কিন্তু কাশি নেই","clinical_terms":["জ্বর","কাশি"],"negations":["নেই"]},
  {"audio":"hinglish.wav","selected_language":"hinglish","reference":"Mujhe fever hai but cough nahi hai","clinical_terms":["fever","cough"],"negations":["nahi"]}
]
```

Run `python -m backend.careflow.scripts.benchmark_stt path/to/manifest.json` with the local model already available. It records
the selection, Whisper detection/confidence, untouched original transcript, reference, approximate whitespace-token WER, simple
term/negation checks, separate English rendering and review flags. WER is only approximate for Indic scripts and code-mixed
utterances; inspect each transcript manually. This repository includes no benchmark recordings or model-derived results; mocked
unit tests verify plumbing only and do not establish language accuracy.

The supplied `seed_cases_100.json` can be run with `python -m backend.careflow.scripts.seed_cases_eval`. This regression evaluator
uses raw symptom text and explicitly supplied age/vitals with the deterministic demonstration rules. It ignores the dataset's
`triage_result`, scores and `clinical_audit` claims; its output is not clinical validation or ground truth. It does not seed those
records into the live case queue. For queue demonstrations, use the curated `seed_demo` scenarios, which create clearly marked
synthetic persistent cases through the real API.

The 20-scenario synthetic acceptance matrix lives in `backend/careflow/demo/scenarios.py` and is run by
`tests/test_acceptance.py` (and by the seeding script, so what is demonstrated is what is tested).

## Configuration

All settings are environment variables (see `.env.example`, placeholders only). Highlights: `SECRET_KEY` (required, 32+ chars, in
production), `COOKIE_SECURE`, `OLLAMA_BASE_URL`/`OLLAMA_MODEL`, `WHISPER_MODEL`, `TESSERACT_LANGS`, upload/audio limits,
`EMERGENCY_NUMBER` (shown in the emergency warning; configure locally), `RULES_PATH` (the ruleset file), and `KNOWLEDGE_PATH`
(the versioned concept registry). The included registry contains internal demo concepts only; it has no SNOMED mappings or
clinically approved rules. Clinical source references and effective dates must be supplied and clinician-reviewed before approval.

**Email:** no email service is bundled. In development, verification and password-reset tokens are returned in the API
response (and surfaced in the UI) with an explicit "development mode" notice. `EXPOSE_DEV_TOKENS=true` is **refused** when
`APP_ENV=production`; wire an email provider before any shared deployment.

## Rules, safety and ordering

* Ruleset: `backend/careflow/rules/demo_ruleset.json` - versioned, validated at load, `clinically_approved: false`, every placeholder
  number is marked. A clinician-approved file replaces it (set `clinically_approved: true` only with a recorded sign-off).
* Precedence: extraction failure -> NEEDS_REVIEW; configured urgent rules -> URGENT; safety-critical conflict / uncertain red flag ->
  NEEDS_REVIEW (tier 1); other conflicts, data-quality problems, missing information -> NEEDS_REVIEW; otherwise NORMAL. There is no
  weighted score anywhere.
* Queue order (documented, deterministic): rule tier ascending -> worsening indicator -> longest waiting -> case id.
* A human decision is stored separately (`decisions`, append-only, with the original proposal). New information re-runs the rules and
  flags "changed since your decision"; it never overwrites the decision.

## Datasets

`python -m backend.careflow.scripts.mimic_ed_eval` evaluates the demonstration rules on the **MIMIC-IV-ED demo** (open access, 222
stays, https://physionet.org/content/mimic-iv-ed-demo/2.2/). Download `ed/edstays.csv.gz`, `ed/triage.csv.gz` and `SHA256SUMS.txt` into
`data/mimic-iv-ed-demo/` (the script verifies checksums and never downloads). Only arrival/triage-time fields are used
(no later vitals, diagnoses or disposition); splits are patient-level; results are descriptive with Wilson intervals and are **not**
validation - see `docs/mimic_ed_demo_report.md`. The full MIMIC-IV-ED requires PhysioNet credentialing and is not used. The
Triage-Transcriptions Hugging Face set was **not** integrated (licence/provenance not reviewed).

## Security notes

Argon2id passwords · opaque server-side sessions (HMAC-hashed at rest, HttpOnly SameSite=Lax cookie) · CSRF header + Origin checks ·
rate limiting and lockout on auth · server-side role and ownership checks on every route (other patients' cases return 404) ·
admins cannot read case content · uploads sniffed by content, size-capped, stored under opaque names outside any static path with
mode 600 and served only through an authorised route with `nosniff` and a sandbox CSP · hash-chained append-only audit log with
DB triggers · no secrets in git (`.gitignore` covers `.env`, `data/`, uploads, databases) · logs avoid clinical text.
Before any remote demonstration: terminate **HTTPS**, set `APP_ENV=production`, `COOKIE_SECURE=true`, a random `SECRET_KEY`, a
real email provider, restrict `CORS_ORIGINS`, put the API behind an authenticated reverse proxy, keep Ollama on localhost, and use
synthetic data unless a compliant data setup exists.

## Known limits

Keyword lexicon (not full NLP) covers ~40 concepts in English/Hindi/Hinglish; other languages rely on the LLM and are always
routed to staff review. Whisper `small` mis-transcribes regional and code-switched speech (flagged and patient-corrected, never
trusted silently). OCR is a text reader, not medical-image understanding. Single-process in-memory rate limiter and background
tasks (a restart marks in-flight cases as failed -> Needs review). See `IMPLEMENTATION_STATUS.md`.

## Legacy code

The earlier hackathon prototype (`backend/app`, `backend/tests`, `backend/eval`, mock/cloud provider adapters, hard-coded demo logins and an
additive weighted score) conflicted with the requirements (cloud providers, fixed credentials, weighted scores) and was replaced by
`backend/careflow`. It is left in place, unmodified and unused, for you to delete once you have confirmed you do not need it.
`PROVIDER_GUIDE.md` now documents the local-only model setup.
