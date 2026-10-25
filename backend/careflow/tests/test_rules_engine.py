"""Pure-function tests of the deterministic rules engine (no DB, no LLM)."""
import copy
import json

import pytest

from ..config import get_settings
from ..services import rules_engine as RE
from ..services.rules_engine import CaseContext, ConflictView, FactView


@pytest.fixture(scope="module")
def rs():
    return json.loads(get_settings().rules_path.read_text(encoding="utf-8"))


_n = 0


def F(concept, polarity="present", temporal="current", category="symptom", **kw):
    global _n
    _n += 1
    return FactView(id=f"f{_n}", category=category, concept=concept, polarity=polarity, temporal=temporal, **kw)


def complete(extra=()):
    return [F("age", category="demographic", value_num=40, unit="years"), F("headache", duration="2 days"), *extra]


def run(rs, facts, **ctx):
    return RE.evaluate(CaseContext(facts=facts, conflicts=ctx.pop("conflicts", []), **ctx), rs)


def test_complete_benign_case_is_normal(rs):
    r = run(rs, complete())
    assert r.queue == "NORMAL" and not r.triggered_rules


def test_red_flag_is_urgent_even_when_everything_else_missing(rs):
    r = run(rs, [F("chest_pain")])
    assert r.queue == "URGENT"
    assert {m["key"] for m in r.missing_information} >= {"age"}  # missing info listed, did not lower the proposal


def test_red_flag_not_cancelled_by_many_benign_facts(rs):
    benign = [F("sore_throat", duration="1 day"), F("cough", polarity="denied"), F("fever", polarity="denied"), F("rash", polarity="denied")]
    r = run(rs, complete(benign) + [F("shortness_of_breath")])
    assert r.queue == "URGENT" and any(t["rule_id"] == "DEMO-U5" for t in r.triggered_rules)


def test_missing_vital_never_lowers_urgency_or_makes_normal(rs):
    urgent = run(rs, [F("chest_pain"), F("shortness_of_breath")])
    assert urgent.queue == "URGENT" and urgent.sort_tier == 1
    # fever without a temperature: listed as missing (non-blocking in the demo ruleset) but never treated as 'normal temperature'
    r = run(rs, complete([F("fever", duration="1 day")]))
    assert any(m["key"] == "vital:temperature" for m in r.missing_information)


def test_unreliable_blocking_missing_vital_would_block_normal(rs):
    rs2 = copy.deepcopy(rs)
    rs2["vitals_expected"][0]["blocking"] = True
    r = run(rs2, complete([F("fever", duration="1 day")]))
    assert r.queue == "NEEDS_REVIEW"


def test_unresolved_conflict_never_normal(rs):
    r = run(rs, complete(), conflicts=[ConflictView("c1", "headache", "presence", False, "unresolved")])
    assert r.queue == "NEEDS_REVIEW" and r.open_conflict_ids == ["c1"]


def test_safety_critical_conflict_is_review_tier1_and_stays_until_reviewer_even_if_patient_clarified(rs):
    c = ConflictView("c1", "chest_pain", "presence", True, "unresolved")
    r = run(rs, complete(), conflicts=[c])
    assert r.queue == "NEEDS_REVIEW" and r.sort_tier == 1
    c2 = ConflictView("c1", "chest_pain", "presence", True, "clarified")
    assert run(rs, complete(), conflicts=[c2]).queue == "NEEDS_REVIEW"
    c3 = ConflictView("c1", "chest_pain", "presence", True, "reviewer_resolved")
    assert run(rs, complete(), conflicts=[c3]).queue == "NORMAL"


def test_urgent_rule_wins_over_open_conflict(rs):
    r = run(rs, [F("chest_pain")], conflicts=[ConflictView("c1", "chest_pain", "presence", True, "unresolved")])
    assert r.queue == "URGENT" and "unresolved" in r.summary


def test_extraction_failure_never_normal(rs):
    r = run(rs, [], extraction_ok=False)
    assert r.queue == "NEEDS_REVIEW" and r.failure == "extraction_failed"
    assert run(rs, [], has_any_input=False).queue == "NEEDS_REVIEW"


def test_degraded_extraction_never_normal(rs):
    assert run(rs, complete(), extraction_degraded=True).queue == "NEEDS_REVIEW"


def test_missing_age_or_duration_needs_review(rs):
    assert run(rs, [F("headache", duration="2 days")]).queue == "NEEDS_REVIEW"
    r = run(rs, [F("age", category="demographic", value_num=30), F("headache")])
    assert r.queue == "NEEDS_REVIEW" and any(m["key"] == "duration:headache" for m in r.missing_information)


def test_uncertain_red_flag_blocks_normal(rs):
    r = run(rs, complete([F("chest_pain", polarity="uncertain", temporal="unknown")]))
    assert r.queue == "NEEDS_REVIEW" and r.sort_tier == 2


def test_resolved_or_denied_red_flag_does_not_trigger(rs):
    assert run(rs, complete([F("chest_pain", temporal="resolved")])).queue == "NORMAL"
    assert run(rs, complete([F("chest_pain", polarity="denied")])).queue == "NORMAL"


def test_unknown_timing_red_flag_is_treated_as_potentially_current(rs):
    assert run(rs, [F("chest_pain", temporal="unknown")]).queue == "URGENT"


def test_rejected_fact_is_ignored(rs):
    r = run(rs, [F("chest_pain", verification="rejected")])
    assert r.queue != "URGENT"


def test_vital_rule_uses_only_reliable_measurements(rs):
    low = F("spo2", category="vital", value_num=88, unit="%")
    assert run(rs, complete([low])).queue == "URGENT"
    typo = F("spo2", category="vital", value_num=88, unit="%", flags=["implausible_value"])
    r = run(rs, complete([typo]))
    assert r.queue == "NEEDS_REVIEW" and any(d["code"] == "unreliable_measurement" for d in r.data_quality)
    assert run(rs, complete([F("spo2", category="vital", value_num=97, unit="%")])).queue == "NORMAL"


def test_worsening_orders_within_tier_via_escalation_indicator(rs):
    stable = run(rs, [F("chest_pain")])
    worse = run(rs, [F("chest_pain", progression="worsening")])
    rapid = run(rs, [F("chest_pain", progression="rapidly_worsening")])
    assert (stable.sort_escalation, worse.sort_escalation, rapid.sort_escalation) == (0, 1, 2)


def test_limited_language_and_translation_flags_block_normal(rs):
    assert run(rs, complete(), limited_language=True).queue == "NEEDS_REVIEW"
    assert run(rs, complete(), translation_flag_count=1).queue == "NEEDS_REVIEW"


def test_failed_or_unconfirmed_upload_blocks_normal(rs):
    assert run(rs, complete(), failed_uploads=1).queue == "NEEDS_REVIEW"
    assert run(rs, complete(), unconfirmed_uploads=1).queue == "NEEDS_REVIEW"


def test_result_is_deterministic(rs):
    facts = complete([F("chest_pain"), F("sweating")])
    a, b = run(rs, facts), run(rs, facts)
    assert json.dumps(a.__dict__, sort_keys=True, default=str) == json.dumps(b.__dict__, sort_keys=True, default=str)


def test_known_cancer_history_does_not_itself_escalate(rs):
    history = F("cancer", category="history")
    result = run(rs, complete([history]))
    assert result.queue == "NORMAL" and not result.triggered_rules


def test_uncertain_malignancy_language_is_not_a_confirmed_cancer_fact(rs):
    from ..services import lexicon
    drafts = lexicon.extract_all("My report says suspicious for malignancy; no diagnosis has been confirmed.")
    assert not [f for f in drafts if f.concept == "cancer" and f.polarity == "present"]
    result = run(rs, complete())
    assert result.queue == "NORMAL" and "no configured urgent rule matched" in result.summary.lower()


def test_acute_warning_rule_does_not_require_a_known_disease(rs):
    result = run(rs, [F("shortness_of_breath")])
    assert result.queue == "URGENT" and "DEMO-U5" in {x["rule_id"] for x in result.triggered_rules}


def test_demo_ruleset_is_labelled_unapproved(rs):
    assert rs["clinically_approved"] is False and "DEMONSTRATION" in rs["approval_note"]
    assert run(rs, [F("chest_pain")]).demonstration_only is True


def test_demo_rules_and_clinical_knowledge_have_explicit_unreviewed_provenance(rs):
    from ..services import knowledge
    catalog = knowledge.active_catalog()
    assert catalog["protocol_version"] == rs["version"]
    assert catalog["review"]["clinically_approved"] is False
    assert catalog["terminology"]["snomed_ct"]["status"] == "not_integrated"
    assert catalog["concepts"] and all(c["terminology_mapping_status"] == "not_mapped" for c in catalog["concepts"])
    assert all(not rule["clinical_metadata"]["clinically_approved"] and rule["clinical_metadata"]["source_reference"] is None for rule in rs["urgent_rules"])


def test_ruleset_cannot_be_marked_approved_without_review_source(rs):
    bad = copy.deepcopy(rs)
    bad["urgent_rules"][0]["clinical_metadata"]["clinically_approved"] = True
    bad["urgent_rules"][0]["clinical_metadata"]["review_status"] = "approved"
    with pytest.raises(ValueError, match="source_organisation"):
        RE.validate_ruleset(bad)


def test_ruleset_validation_rejects_bad_config(rs):
    bad = copy.deepcopy(rs)
    bad["urgent_rules"][0]["tier"] = 0
    with pytest.raises(ValueError):
        RE.validate_ruleset(bad)
    bad = copy.deepcopy(rs)
    bad["urgent_rules"].append(copy.deepcopy(bad["urgent_rules"][0]))
    with pytest.raises(ValueError):
        RE.validate_ruleset(bad)
    bad = copy.deepcopy(rs)
    bad["urgent_rules"][0]["when"] = {"weighted_score": 5}
    with pytest.raises(ValueError):
        RE.validate_ruleset(bad)
