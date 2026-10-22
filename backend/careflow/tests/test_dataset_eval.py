import json
from pathlib import Path

import pytest

from ..config import ROOT, get_settings
from ..scripts import mimic_ed_eval as M
from ..services import rules_engine as RE

DATA = ROOT / "data" / "mimic-iv-ed-demo"
needs_data = pytest.mark.skipif(not (DATA / "ed" / "triage.csv.gz").exists(), reason="MIMIC-IV-ED demo not downloaded (see README: Datasets)")


def test_leakage_guard_lists_only_triage_time_files():
    assert "vitalsign" in M.FORBIDDEN_FILES and "diagnosis" in M.FORBIDDEN_FILES
    src = Path(M.__file__).read_text()
    for name in M.FORBIDDEN_FILES:
        assert f'"{name}.csv.gz"' not in src and f"'{name}.csv.gz'" not in src  # never opened
    assert "disposition" not in M.FEATURE_FILES["edstays"]


def test_split_is_patient_level_and_stable():
    ids = [str(i) for i in range(500)]
    assert {M.patient_split(i) for i in ids} == {"dev", "test"}
    assert [M.patient_split(i) for i in ids] == [M.patient_split(i) for i in ids]


def test_wilson_interval_and_prf():
    lo, hi = M.wilson(21, 72)
    assert 0.19 < lo < 0.30 < hi < 0.42
    assert M.wilson(0, 0) == (None, None)
    assert M.prf(0, 0, 0) == (None, None, None)


def test_invalid_vitals_are_dropped_not_guessed():
    s = M.Stay("1", "1", 2, "SOB", {})
    t = {"temperature": "985", "heartrate": "abc", "resprate": "20", "o2sat": "250", "sbp": "90", "dbp": "120", "pain": "13", "acuity": "2", "chiefcomplaint": "SOB"}
    facts, invalid = M.to_facts(t, s)
    assert {f.concept for f in facts if f.category == "vital"} == {"resp_rate"}
    assert {"temperature=985", "heartrate=abc", "o2sat=250", "bp=90/120"} <= set(invalid)
    assert any(f.concept == "shortness_of_breath" for f in facts)


@needs_data
def test_end_to_end_eval_on_open_demo():
    rs, _ = RE.load_ruleset(get_settings().rules_path)
    stays = M.load(DATA)
    assert len(stays) == 222 and M.verify_checksums(DATA)["ok"]
    ev = M.evaluate(stays, rs)
    assert ev["dev"]["n_stays"] + ev["test"]["n_stays"] == ev["all"]["n_stays"]
    dev_p = {s.subject_id for s in stays if M.patient_split(s.subject_id) == "dev"}
    assert not dev_p & {s.subject_id for s in stays if M.patient_split(s.subject_id) == "test"}
    for split in ev.values():  # no stay can be proposed NORMAL: the open tables have no age
        assert all(c.get("NORMAL", 0) == 0 for c in split["confusion_matrix"].values())
