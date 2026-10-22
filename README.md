# CareFlow AI

A **local-first, human-in-the-loop** healthcare **intake and urgency-prioritisation** MVP.

Patients describe symptoms by text, voice and uploaded reports (English, Hindi, Hinglish). Local models turn that into
structured, source-traceable facts; a deterministic engine flags missing and contradictory information; follow-up
questions are asked; a transparent **rules engine** proposes a queue (**Urgent / Normal / Needs review**); staff inspect
the evidence and confirm or override the proposal with a reason. Everything is audited.

> **Read this first.** This is a demonstration prototype. It is **not** a medical device, does **not** diagnose or prescribe,
> and has **no clinical validation**. The urgency rules in `backend/careflow/rules/demo_ruleset.json` are engineering
> placeholders that **no clinician has reviewed** and must be replaced by a clinician-approved ruleset before any real use.
> The emergency warning wording is likewise unreviewed. Use **synthetic data only**.
> See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for exactly what works, what is partial and what is unavailable.

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

The current tests cover key safety behaviours: configured red-flag rules take precedence, missing or conflicting information does not silently become Normal, and extraction or rules failures route to **Needs review**. The LLM extracts facts but does not assign urgency. Conflicts are preserved, and processed speech/document text can be reviewed and corrected before use.

## Repository layout

| Path | What |
|---|---|
| `backend/careflow/` | FastAPI app: `routers/` (auth, patient, staff, admin), `services/` (lexicon, extraction, ocr, stt, reconcile, followup, rules_engine, pipeline, notes), `models.py`, `migrations/` (Alembic), `rules/demo_ruleset.json`, `scripts/`, `tests/` |
| `frontend/` | Next.js 14 · React · TypeScript · Tailwind (App Router). `/api/*` is proxied to the backend (same-origin cookies) |
| `docs/` | `API.md` (generated), `mimic_ed_demo_report.md` (generated dataset evaluation) |

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

# 4) Speech model (faster-whisper "small", ~460 MB, downloaded once into data/models/whisper on first use or now:)
python -c "from backend.careflow.services import stt; stt.get_model()"

# 5) Frontend
cd frontend && npm install && cd ..
```

Whisper note: `faster-whisper` 1.2 needs `av<16` (pinned in requirements). Larger Whisper models (`medium`, `large-v3`) improve
code-switched and regional speech; set `WHISPER_MODEL` in `.env`. Set `WHISPER_LOCAL_ONLY=true` to forbid any download.

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

The supplied `seed_cases_100.json` can be run with `python -m backend.careflow.scripts.seed_cases_eval`. This regression evaluator
uses raw symptom text and explicitly supplied age/vitals with the deterministic demonstration rules. It ignores the dataset's
`triage_result`, scores and `clinical_audit` claims; its output is not clinical validation or ground truth. It does not seed those
records into the live case queue. For queue demonstrations, use the curated `seed_demo` scenarios, which create clearly marked
synthetic persistent cases through the real API.

The 20-scenario synthetic acceptance matrix lives in `backend/careflow/demo/scenarios.py` and is run by
`backend/careflow/tests/test_acceptance.py` (and by the seeding script, so what is demonstrated is what is tested).

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
