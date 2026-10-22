# MIMIC-IV-ED demo: descriptive evaluation of the DEMONSTRATION rules

> **Not clinical validation.** Open demo subset (~200 stays). Historical ESI acuity is an imperfect label. The rules under test are engineering placeholders (`demo-0.1.0-UNREVIEWED`), not clinician-approved. Metrics below are descriptive, have wide uncertainty, and must not be quoted as performance.

Dataset checksums verified: **{'checked': 4, 'ok': True, 'mismatches': []}**. Features used: arrival/triage-time fields only (no `vitalsign`, disposition, diagnoses, medications).

## Data description
```json
{
 "n_stays": 222,
 "n_patients": 64,
 "acuity_distribution": {
  "1": 18,
  "2": 97,
  "3": 90,
  "4": 2,
  "missing": 15
 },
 "missing_or_invalid_by_field": {
  "temperature": 27,
  "heartrate": 24,
  "resprate": 23,
  "o2sat": 24,
  "bp": 24,
  "pain": 40,
  "chiefcomplaint": 2
 },
 "invalid_values_dropped": {
  "bp": 1,
  "temperature": 1
 },
 "chief_complaints_mapped_to_a_known_symptom": 118,
 "chief_complaints_unmapped": 104
}
```

## Split: `dev` (88 stays, 32 patients)

Confusion matrix (rows = historical acuity group, columns = proposed queue):

| acuity group | URGENT | NEEDS_REVIEW | NORMAL |
|---|---:|---:|---:|
| ESI 1-2 | 14 | 29 | 0 |
| ESI 3 | 2 | 32 | 0 |
| ESI 4-5 | 0 | 1 | 0 |
| missing | 4 | 6 | 0 |

Binary view (positive = historical ESI 1-2; predicted positive = proposed queue URGENT (NEEDS_REVIEW counts as not-URGENT)). Positives = 43.

| class | precision | recall | F1 |
|---|---:|---:|---:|
| URGENT (vs ESI 1-2) | 0.88 | 0.33 (95% CI (0.205, 0.475)) | 0.47 |
| not URGENT (vs ESI 3-5) | 0.53 | 0.94 | 0.68 |

Macro-F1: **0.577**. At least 30 positives (minimum for even a rough estimate)? **True** - note the wide confidence intervals. Share of non-URGENT proposals that are NEEDS_REVIEW: 1.0.

## Split: `test` (134 stays, 32 patients)

Confusion matrix (rows = historical acuity group, columns = proposed queue):

| acuity group | URGENT | NEEDS_REVIEW | NORMAL |
|---|---:|---:|---:|
| ESI 1-2 | 21 | 51 | 0 |
| ESI 3 | 9 | 47 | 0 |
| ESI 4-5 | 0 | 1 | 0 |
| missing | 3 | 2 | 0 |

Binary view (positive = historical ESI 1-2; predicted positive = proposed queue URGENT (NEEDS_REVIEW counts as not-URGENT)). Positives = 72.

| class | precision | recall | F1 |
|---|---:|---:|---:|
| URGENT (vs ESI 1-2) | 0.70 | 0.29 (95% CI (0.199, 0.405)) | 0.41 |
| not URGENT (vs ESI 3-5) | 0.48 | 0.84 | 0.62 |

Macro-F1: **0.514**. At least 30 positives (minimum for even a rough estimate)? **True** - note the wide confidence intervals. Share of non-URGENT proposals that are NEEDS_REVIEW: 1.0.

## Split: `all` (222 stays, 64 patients)

Confusion matrix (rows = historical acuity group, columns = proposed queue):

| acuity group | URGENT | NEEDS_REVIEW | NORMAL |
|---|---:|---:|---:|
| ESI 1-2 | 35 | 80 | 0 |
| ESI 3 | 11 | 79 | 0 |
| ESI 4-5 | 0 | 2 | 0 |
| missing | 7 | 8 | 0 |

Binary view (positive = historical ESI 1-2; predicted positive = proposed queue URGENT (NEEDS_REVIEW counts as not-URGENT)). Positives = 115.

| class | precision | recall | F1 |
|---|---:|---:|---:|
| URGENT (vs ESI 1-2) | 0.76 | 0.30 (95% CI (0.228, 0.394)) | 0.43 |
| not URGENT (vs ESI 3-5) | 0.50 | 0.88 | 0.64 |

Macro-F1: **0.538**. At least 30 positives (minimum for even a rough estimate)? **True** - note the wide confidence intervals. Share of non-URGENT proposals that are NEEDS_REVIEW: 1.0.

## Reading this honestly

* The open triage tables have **no age**, and the demo ruleset requires age before proposing NORMAL, so almost every non-urgent case is NEEDS_REVIEW by design (missing information never implies low risk). NORMAL is therefore not evaluable here.
* Chief complaints are short ED abbreviations; only those matching the small symptom lexicon (plus a documented abbreviation table) produce symptom facts. Many high-acuity stays have complaints the lexicon does not know, so recall is expected to be low.
* Triage vitals use placeholder rule thresholds; implausible values are dropped, not guessed.
* The full MIMIC-IV-ED needs PhysioNet credentialing; it was not used. No model was trained.