# CareFlow AI - implementation status

Last updated: 2026-10-22. Everything marked **verified** was run in this environment (macOS, Python 3.13, Node 26, Ollama 0.40 with
`qwen3:4b`, faster-whisper `small`, Tesseract 5.5 with eng+hin). Nothing here is clinically validated.

## Commands

| Purpose | Command |
|---|---|
| Backend tests | `source .venv/bin/activate && python -m pytest` |
| Real-model tests only | `python -m pytest -m requires_models` |
| Backend lint | `ruff check backend/careflow` |
| Frontend checks | `cd frontend && npm run typecheck && npm run lint && npm test && npm run build` |
| Run API / UI | `uvicorn backend.careflow.main:app --port 8000` / `cd frontend && npm run dev` |
| First admin | `python -m backend.careflow.scripts.create_admin --email … --name …` |
| Synthetic demo data | `python -m backend.careflow.scripts.seed_demo` |
| Dataset evaluation | `python -m backend.careflow.scripts.mimic_ed_eval` |
| Supplied 100-case regression exercise | `python -m backend.careflow.scripts.seed_cases_eval` |
| API docs | `python -m backend.careflow.scripts.export_api_docs` -> `docs/API.md` |

## Test results (this session)

| Check | Result |
|---|---|
| Backend pytest | **173 passed, 2 skipped** (40.93 s; 2 optional model integration checks skipped) |
| `ruff check backend/careflow` | clean |
| Frontend `tsc --noEmit` | clean (final run after landing typography update) |
| Frontend `next lint` | no warnings or errors (final run) |
| Frontend vitest | **30 passed** in 3 files (final run; includes local recording, pause/preview/manual upload, UI states and API client) |
| Frontend `next build` | succeeds (11 routes; final run) |
| Existing pre-change tests | 10 passing legacy tests existed; that prototype is superseded (see Legacy) and is not part of the current suite |

Test coverage by area: auth/RBAC/ownership/CSRF/lockout/rate-limit (`test_auth_access.py`), lexicon incl. negation scope in 3 languages
(`test_lexicon.py`), rules precedence and the "missing data never lowers urgency / failure never Normal" properties
(`test_rules_engine.py`), documents and OCR failures (`test_documents.py`), LLM-output validation and failure modes
(`test_safety_failures.py`), the patient-to-staff workflow incl. conflicts, reassessment, overrides, audit append-only, ordering,
idempotency and persistence across restart (`test_workflow.py`), the 20-scenario acceptance matrix + the addendum-G end-to-end test
(`test_acceptance.py`), dataset tooling (`test_dataset_eval.py`).

## Working (verified)

* **Auth & permissions**: sign-up (patients only), sign-in/out, Argon2 hashing, server-side sessions, CSRF, lockout, rate limit,
  password reset/change, email verification (dev-token mode, see limits), admin-created staff accounts, role checks on every route,
  patient ownership (404 for others' cases), admins cannot open case contents, consent gate, deletion request -> admin purge with
  retention hold when an approved note exists.
* **Intake**: typed text (en/hi/hi-Latn; other languages accepted and flagged "limited support"), voice recording/upload, PDF
  (digital via PyMuPDF; scanned via Tesseract), photos (Pillow preprocessing + Tesseract, OSD rotation), multiple items per case,
  patient review/correction of every transcript/extraction before use, idempotent message retries, content-sniffed uploads.
* **Extraction**: deterministic lexicon (~40 concepts, negation/temporal/uncertainty, vitals with units + plausibility, age,
  medicines/allergies) + Ollama `qwen3:4b` with strict schema validation; every LLM fact must quote the source; disagreements become
  `uncertain`; ungrounded modifiers dropped; provenance (source, locator = message #/audio mm:ss/page, speaker, method, confidence
  separate from risk) stored per fact.
* **Reconciliation**: presence/absence, cross-source (text/audio/report), medication/allergy "none vs named", age, repeated vital
  values (series vs conflict by time), unit/implausible values, possible missed negation (extractor/translation/ASR disagreement).
  Conflict register with unresolved -> clarified -> reviewer_resolved, who/when/evidence retained, originals never deleted.
* **Follow-up**: concise, prioritised (conflicts preempt routine questions), each with a reason, never repeated, skip != "no", en/hi/hinglish
  templates; answers create facts and trigger reassessment.
* **Rules engine**: deterministic, config-driven, versioned, no weighted score; demonstration ruleset clearly labelled; red flags win;
  failures route to NEEDS_REVIEW; documented ordering.
* **Staff UI/API**: queues with DB-computed counts, search/filters/server-side pagination, 5 s change polling with reconnecting state,
  review page (rationale, matched rules, evidence, facts, vitals, conflicts, questions, audit), decision/override with required reason
  and stale-assessment protection, source filters, patient search, actual workflow counts, append-only fact corrections with
  reassessment, assignment, status workflow, staff-initiated questions, reassess.
* **Clinical note**: deterministic structured draft labelled "AI-assisted DRAFT"; edit (diffs recorded), reject (reason), approve
  (needs prior human decision; blocked if stale); approved note immutable; text export.
* **Admin**: accounts, audit log + hash-chain verification, system health (LLM/STT/OCR/DB), ruleset view, deletion requests.
* **Dataset tooling**: MIMIC-IV-ED demo importer + leakage-guarded, patient-split, descriptive evaluation (`docs/mimic_ed_demo_report.md`).
  The supplied 100-row dataset has a schema-checked regression evaluator that ignores triage labels, scores and clinical-audit claims.
* **Knowledge registry**: versioned internal concept catalogue (`demo_knowledge.json`) with no SNOMED mapping or approved sources;
  ruleset metadata requires source organisation, reference, effective date and review status before clinical approval. The demo remains
  unapproved; `KNOWLEDGE_PATH` selects the registry.
* **Public landing**: screenshot-inspired forest-green/ivory hero, workflow illustration, patient/staff routing and safety notice;
  authenticated navigation wraps on narrow screens.
* **Browser spot-checks** (built-in browser): welcome, sign-up, consent, empty state, intake with emergency banner while typing,
  follow-up questions, mobile layout (375 px), staff sign-in/dashboard/case review/conflicts, admin accounts and system pages.
* Migrations (Alembic) verified to produce the same schema as the models; append-only triggers verified to block UPDATE/DELETE.

## Partially working / caveats

* **Speech**: Whisper `small` works for English and Hindi and is slow on CPU (~5-14 s per short clip). In real runs it garbled Tamil
  ("fever" -> a non-word) and dropped the Hindi half of a Hindi/English code-switched clip in the English rendering. The system flags
  such cases (`language_outside_primary_support`, `asr_translation_concepts_differ`) and requires patient confirmation, but accuracy is
  **not** claimed for regional or code-switched speech. Larger models are untested.
* **Browser microphone recording** (MediaRecorder) captures locally, offers playback and discard/re-record before upload, then uses the
  existing transcription and patient-correction workflow. It remains **not exercised** (no microphone in this environment); file upload of
  audio is tested.
* **Upload, decision, note-editing and deletion flows in the browser** were verified through API/acceptance tests and component tests, not
  clicked through end-to-end in the browser.
* **Translation** is machine translation (Whisper translate / `qwen3:4b`) for staff convenience; negation-count and concept-diff checks flag
  likely errors but cannot guarantee correctness.
* **Regional-language text** is accepted and routed to staff review; the lexicon does not cover it and `qwen3:4b` quality was not evaluated.
* **Email**: no email service. Verification/reset tokens are returned in the API response in development only (refused in production).
* **Live updates** use polling (5 s summary + silent refetch on change), not push.
* **Rate limiter and background processing** are in-process (single worker). A restart marks in-flight cases `failed` -> Needs review;
  use a queue/worker and shared limiter for multi-process deployments.
* **PostgreSQL** is supported by SQLAlchemy but untested; append-only triggers must be added by hand (documented).
* **Accessibility** has semantic markup, labels, focus styles and a skip link but no formal WCAG audit or screen-reader testing.
* **Dataset evaluation** is descriptive on 222 stays: the rules flag ~29% of ESI 1-2 stays as Urgent on the held-out split (precision ~0.70,
  macro-F1 ~0.51, wide intervals). The open tables have no age, so NORMAL is never proposed. This says the placeholder rules and small lexicon
  are weak, not that anything is validated.

The supplied 100-case run returned 21 URGENT demonstration proposals, 79 NEEDS_REVIEW and 0 NORMAL because extraction was deliberately
marked degraded. These are software outputs, not accuracy or clinical performance measures. The records are not inserted into the live
queue: embedded review labels and clinical-audit claims are untrusted, while `seed_demo` creates clearly labelled synthetic cases in the
persistent queue through the real workflow.

## Unavailable / not done

* **Clinician-reviewed rules, thresholds, emergency wording and escalation times** - none exist. All urgency output is demonstration-only.
* **Clinical validation**, regulatory approval, security penetration test, load testing, formal privacy/DPIA review.
* **Medical-image understanding** (only document OCR; no model is claimed for imaging).
* **Full MIMIC-IV-ED** (needs PhysioNet credentialing) and the Triage-Transcriptions Hugging Face dataset (not reviewed or integrated).
* Email delivery, SSO/MFA, push notifications, scheduled retention jobs, HTTPS termination (documented, not shipped).

## Decisions and findings worth knowing

* The previous prototype (in-memory DB, hard-coded demo logins/tokens, additive weighted score, cloud STT/OCR adapters, mock extraction) conflicted
  with the requirements, so the backend was rebuilt as `backend/careflow`. Reused: the Next/Tailwind stack and the overall concept.
* Sandbox policy blocked deleting/moving the old code, so it was left untouched at `backend/app`, `backend/tests`, `backend/eval` (and
  `PROVIDER_GUIDE.md`/`README.md` were rewritten in place). **Please delete those three folders when you are satisfied**; nothing imports them.
* `faster-whisper` 1.2.1 fails with PyAV >= 16; `av>=14,<16` is pinned.
* `qwen3:4b` quirks handled in code: it sometimes omits a Hindi negation entirely (the lexicon catches it), invents `progression: improving`
  and emits the string "unknown" for modifiers (grounding checks drop those).
* An early lexicon bug let a Hindi negation leak across a sentence ("`dard hai. bukhar nahi hai`" denied the chest pain); fixed with clause/danda
  splitting and scope rules, with regression tests. The cross-check against the English rendering caught it first.
* A conflicting low SpO2 reading still triggers the (placeholder) vital rule: contradictions never lower urgency.
* Seeding created some duplicate demo cases (the first run was interrupted and re-run); harmless synthetic data.

## Suggested next steps

1. Have a clinician review/replace `demo_ruleset.json` and the emergency wording; record sign-off in the ruleset file.
2. Evaluate larger Whisper models and `qwen3` alternatives on consented, de-identified Indian-language audio; expand the lexicon.
3. Add an email provider, worker queue and PostgreSQL deployment profile; run an accessibility and security review.
4. Browser end-to-end tests (Playwright) for the upload/decision/note flows.
