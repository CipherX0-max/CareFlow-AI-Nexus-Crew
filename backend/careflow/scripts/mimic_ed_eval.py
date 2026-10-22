"""MIMIC-IV-ED *demo* importer + descriptive evaluation of the demonstration rules engine.

    python -m backend.careflow.scripts.mimic_ed_eval [--data data/mimic-iv-ed-demo] [--report docs/mimic_ed_demo_report.md]

Dataset: https://physionet.org/content/mimic-iv-ed-demo/2.2/ (open access; ODbL). The FULL MIMIC-IV-ED requires PhysioNet
credentialing + a data-use agreement and is NOT accessed here. This script never downloads anything.

Responsible-use design
* Historical triage `acuity` (ESI 1-5) is treated as an existing LABEL of uncertain quality, not ground truth.
* Temporal-leakage guard: ONLY arrival/triage-time fields are used (edstays: gender, arrival_transport; triage: chief
  complaint + triage vitals). `vitalsign.csv` (charted later), disposition, outtime, diagnoses and medication tables are
  never read for features. A test asserts this.
* Patient-level split (hash of subject_id): all stays of one patient land in the same split. The rules are hand-written
  (not fitted), but the split still protects against us tuning rules on the reported subset: we inspect `dev` and report `test`.
* Records are evaluated in memory and are NOT merged into the application database (real vs synthetic stay separate).
* The demo set is tiny. Metrics are descriptive, come with Wilson intervals, and are flagged unreliable below n=30 positives.
  Nothing here is clinical validation."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import ROOT, get_settings
from ..services import lexicon as L
from ..services import rules_engine as RE

FEATURE_FILES = {"edstays": ["subject_id", "stay_id", "gender", "arrival_transport"],
                 "triage": ["subject_id", "stay_id", "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint"]}
FORBIDDEN_FILES = ("vitalsign", "diagnosis", "medrecon", "pyxis")  # post-triage information: never used as features
# conservative abbreviation expansion applied to ED chief-complaint text before the shared lexicon runs
ABBREV = [(r"\bsob\b", "shortness of breath"), (r"\bdyspnea\b", "shortness of breath"), (r"\bcp\b", "chest pain"), (r"\babd\b", "abdominal"),
          (r"\bn/v/d\b", "nausea vomiting diarrhea"), (r"\bn/v\b", "nausea vomiting"), (r"\bha\b", "headache"), (r"\bloc\b", "lost consciousness"),
          (r"\bsyncope\b", "fainted"), (r"\bseizure\b", "seizure"), (r"\bfacial droop\b", "facial droop"), (r"\bslurred speech\b", "slurred speech"),
          (r"\bs/p arrest\b", "arrest"), (r"\bhematemesis\b", "vomiting blood"), (r"\bbrbpr\b", "heavy bleeding"), (r"\bhead bleed\b", "severe bleeding")]
PLAUSIBLE = {"temperature": (80, 115), "heartrate": (10, 300), "resprate": (2, 80), "o2sat": (20, 100), "sbp": (30, 300), "dbp": (10, 200)}


@dataclass
class Stay:
    subject_id: str
    stay_id: str
    acuity: Optional[int]
    chief: str
    raw: dict
    facts: list = field(default_factory=list)
    invalid_fields: list = field(default_factory=list)


def _rows(path: Path):
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.DictReader(f)


def verify_checksums(root: Path) -> dict:
    sums = {}
    p = root / "SHA256SUMS.txt"
    if not p.exists():
        return {"checked": 0, "ok": None, "note": "SHA256SUMS.txt not found"}
    for line in p.read_text().splitlines():
        if line.strip():
            h, f = line.split()
            sums[f] = h
    bad = [f for f, h in sums.items() if (root / f).exists() and hashlib.sha256((root / f).read_bytes()).hexdigest() != h]
    return {"checked": sum(1 for f in sums if (root / f).exists()), "ok": not bad, "mismatches": bad}


def num(v, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if lo is not None and not (lo <= x <= hi):
        return None
    return x


def load(root: Path) -> list[Stay]:
    ed = {r["stay_id"]: r for r in _rows(root / "ed" / "edstays.csv.gz")}
    stays = []
    for t in _rows(root / "ed" / "triage.csv.gz"):
        e = ed.get(t["stay_id"], {})
        acu = num(t["acuity"], 1, 5)
        s = Stay(t["subject_id"], t["stay_id"], int(acu) if acu else None, t["chiefcomplaint"].strip(),
                 {"gender": e.get("gender"), "arrival_transport": e.get("arrival_transport"), "pain": t["pain"]})
        s.facts, s.invalid_fields = to_facts(t, s)
        stays.append(s)
    return stays


def to_facts(t: dict, s: Stay):
    """Map triage-time fields to the app's FactView schema with units (MIMIC temperature is degrees Fahrenheit)."""
    facts, invalid, n = [], [], 0

    def add(**kw):
        nonlocal n
        n += 1
        facts.append(RE.FactView(id=f"{s.stay_id}-{n}", **kw))

    text = " " + s.chief.lower() + " "
    for rx, rep in ABBREV:
        text = re.sub(rx, rep, text)
    for d in L.extract_symptoms(text):
        add(category=d.category, concept=d.concept, polarity=d.polarity, temporal="current" if d.temporal == "unknown" else d.temporal, duration=d.duration,
            progression=d.progression, source_type="triage")
    for col, concept, unit in (("temperature", "temperature", "F"), ("heartrate", "heart_rate", "bpm"), ("resprate", "resp_rate", "/min"), ("o2sat", "spo2", "%")):
        raw = t[col]
        if raw == "":
            continue
        v = num(raw, *PLAUSIBLE[col])
        if v is None:
            invalid.append(f"{col}={raw}")
            continue
        add(category="vital", concept=concept, polarity="present", temporal="current", value_num=v, unit=unit, source_type="triage")
    if t["sbp"] != "" and t["dbp"] != "":
        sb, db = num(t["sbp"], *PLAUSIBLE["sbp"]), num(t["dbp"], *PLAUSIBLE["dbp"])
        if sb is None or db is None or db >= sb:
            invalid.append(f"bp={t['sbp']}/{t['dbp']}")
        else:
            add(category="vital", concept="blood_pressure", polarity="present", temporal="current", value_num=sb, value_num2=db, unit="mmHg", source_type="triage")
    return facts, invalid


def patient_split(subject_id: str) -> str:
    return "test" if int(hashlib.sha256(f"careflow-split-1:{subject_id}".encode()).hexdigest(), 16) % 2 else "dev"


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h), 3), round(min(1, c + h), 3))


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return p, r, f


def evaluate(stays: list[Stay], rs: dict) -> dict:
    out = {}
    for split in ("dev", "test", "all"):
        subset = [s for s in stays if split == "all" or patient_split(s.subject_id) == split]
        cm: dict[str, Counter] = defaultdict(Counter)
        for s in subset:
            ctx = RE.CaseContext(facts=s.facts, conflicts=[], has_any_input=bool(s.chief))
            res = RE.evaluate(ctx, rs)
            group = "missing" if s.acuity is None else "ESI 1-2" if s.acuity <= 2 else "ESI 3" if s.acuity == 3 else "ESI 4-5"
            cm[group][res.queue] += 1
        labelled = [g for g in cm if g != "missing"]
        tp = cm["ESI 1-2"]["URGENT"]
        fn = cm["ESI 1-2"]["NORMAL"] + cm["ESI 1-2"]["NEEDS_REVIEW"]
        fp = sum(cm[g]["URGENT"] for g in labelled if g != "ESI 1-2")
        tn = sum(sum(cm[g].values()) - cm[g]["URGENT"] for g in labelled if g != "ESI 1-2")
        p, r, f1 = prf(tp, fp, fn)
        p2, r2, f2 = prf(tn, fn, fp)  # class 'not ESI 1-2' vs predicted-not-URGENT
        pos = tp + fn
        out[split] = {"n_stays": len(subset), "n_patients": len({s.subject_id for s in subset}), "confusion_matrix": {g: dict(c) for g, c in sorted(cm.items())},
                      "binary_high_acuity": {"definition": "positive = historical ESI 1-2; predicted positive = proposed queue URGENT (NEEDS_REVIEW counts as not-URGENT)",
                                              "positives": pos, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                                              "urgent": {"precision": p, "recall": r, "f1": f1, "recall_ci95": wilson(tp, pos), "precision_ci95": wilson(tp, tp + fp)},
                                              "not_urgent": {"precision": p2, "recall": r2, "f1": f2},
                                              "macro_f1": round((f1 + f2) / 2, 3) if f1 is not None and f2 is not None else None,
                                              "n_positives_at_least_30": pos >= 30},
                      "needs_review_share_of_non_urgent": round(sum(c["NEEDS_REVIEW"] for c in cm.values()) / max(1, sum(sum(c.values()) - c["URGENT"] for c in cm.values())), 3)}
    return out


def describe(stays: list[Stay]) -> dict:
    cols = ["temperature", "heartrate", "resprate", "o2sat", "bp", "pain", "chiefcomplaint"]
    miss = {c: 0 for c in cols}
    for s in stays:
        have = {f.concept for f in s.facts if f.category == "vital"}
        for c, k in (("temperature", "temperature"), ("heartrate", "heart_rate"), ("resprate", "resp_rate"), ("o2sat", "spo2"), ("bp", "blood_pressure")):
            miss[c] += k not in have
        miss["chiefcomplaint"] += s.chief.upper() in ("", "UNKNOWN-CC")
        miss["pain"] += num(s.raw.get("pain"), 0, 10) is None
    mapped = sum(1 for s in stays if any(f.category == "symptom" for f in s.facts))
    return {"n_stays": len(stays), "n_patients": len({s.subject_id for s in stays}), "acuity_distribution": dict(sorted(Counter(str(s.acuity) if s.acuity else "missing" for s in stays).items())),
            "missing_or_invalid_by_field": miss, "invalid_values_dropped": Counter(i.split("=")[0] for s in stays for i in s.invalid_fields),
            "chief_complaints_mapped_to_a_known_symptom": mapped, "chief_complaints_unmapped": len(stays) - mapped}


def render(report: dict) -> str:
    L_ = ["# MIMIC-IV-ED demo: descriptive evaluation of the DEMONSTRATION rules", "",
          "> **Not clinical validation.** Open demo subset (~200 stays). Historical ESI acuity is an imperfect label. The rules under test are engineering "
          "placeholders (`" + report["ruleset_version"] + "`), not clinician-approved. Metrics below are descriptive, have wide uncertainty, and must not be quoted as performance.", "",
          f"Dataset checksums verified: **{report['checksums']}**. Features used: arrival/triage-time fields only (no `vitalsign`, disposition, diagnoses, medications).", "",
          "## Data description", "```json", json.dumps(report["description"], indent=1, default=str), "```", ""]
    for split in ("dev", "test", "all"):
        e = report["evaluation"][split]
        b = e["binary_high_acuity"]
        L_ += [f"## Split: `{split}` ({e['n_stays']} stays, {e['n_patients']} patients)", "", "Confusion matrix (rows = historical acuity group, columns = proposed queue):", "",
               "| acuity group | URGENT | NEEDS_REVIEW | NORMAL |", "|---|---:|---:|---:|"]
        for g, c in e["confusion_matrix"].items():
            L_.append(f"| {g} | {c.get('URGENT', 0)} | {c.get('NEEDS_REVIEW', 0)} | {c.get('NORMAL', 0)} |")
        u, nu = b["urgent"], b["not_urgent"]
        f = lambda x: "n/a" if x is None else f"{x:.2f}"
        L_ += ["", f"Binary view ({b['definition']}). Positives = {b['positives']}.", "",
               "| class | precision | recall | F1 |", "|---|---:|---:|---:|",
               f"| URGENT (vs ESI 1-2) | {f(u['precision'])} | {f(u['recall'])} (95% CI {u['recall_ci95']}) | {f(u['f1'])} |",
               f"| not URGENT (vs ESI 3-5) | {f(nu['precision'])} | {f(nu['recall'])} | {f(nu['f1'])} |", "",
               f"Macro-F1: **{b['macro_f1']}**. At least 30 positives (minimum for even a rough estimate)? **{b['n_positives_at_least_30']}** - note the wide confidence intervals. Share of non-URGENT proposals that are NEEDS_REVIEW: {e['needs_review_share_of_non_urgent']}.", ""]
    L_ += ["## Reading this honestly", "",
           "* The open triage tables have **no age**, and the demo ruleset requires age before proposing NORMAL, so almost every non-urgent case is NEEDS_REVIEW by design (missing information never implies low risk). NORMAL is therefore not evaluable here.",
           "* Chief complaints are short ED abbreviations; only those matching the small symptom lexicon (plus a documented abbreviation table) produce symptom facts. Many high-acuity stays have complaints the lexicon does not know, so recall is expected to be low.",
           "* Triage vitals use placeholder rule thresholds; implausible values are dropped, not guessed.",
           "* The full MIMIC-IV-ED needs PhysioNet credentialing; it was not used. No model was trained."]
    return "\n".join(L_)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "mimic-iv-ed-demo"))
    ap.add_argument("--report", default=str(ROOT / "docs" / "mimic_ed_demo_report.md"))
    a = ap.parse_args()
    root = Path(a.data)
    if not (root / "ed" / "triage.csv.gz").exists():
        raise SystemExit(f"Dataset not found at {root}. See README 'Datasets': download ed/edstays.csv.gz, ed/triage.csv.gz and SHA256SUMS.txt from "
                         "https://physionet.org/files/mimic-iv-ed-demo/2.2/ (open access) into that folder.")
    chk = verify_checksums(root)
    rs, _ = RE.load_ruleset(get_settings().rules_path)
    stays = load(root)
    report = {"ruleset_version": rs["version"], "checksums": chk, "description": describe(stays), "evaluation": evaluate(stays, rs)}
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(render(report))
    Path(a.report).with_suffix(".json").write_text(json.dumps(report, indent=1, default=str))
    print(f"wrote {a.report}")
    t = report["evaluation"]["test"]["binary_high_acuity"]
    print("test split:", {k: t[k] for k in ("positives", "tp", "fp", "fn", "tn", "macro_f1", "n_positives_at_least_30")})


if __name__ == "__main__":
    main()
