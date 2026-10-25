"""Comprehensive automated test suite for the CareFlow AI Final Master Implementation:
- Two-stage local model integration (Qwen3:4B extraction + MedGemma 1.5 4B evidence analysis)
- Unified 11-section evidence-grounded medical summary
- Smart, adaptive hybrid follow-up questions
- Explainable per-category (#1, #2, #3...) priority ranking with hidden numerical score
- 100-case synthetic dataset evaluation (`synthetic_cases_100.py`)
"""
from __future__ import annotations

from datetime import timedelta

from . import media
from .helpers import Api, clear_profile_age, consent, make_user
from .synthetic_cases_100 import build_synthetic_100
from ..db import session_scope
from ..models import Assessment, Case, Conflict, Fact, PatientProfile, utcnow
from ..services import facts as F, followup, lexicon as L, llm, pipeline, priority_scorer as PS, reconcile, rules_engine as RE, summary as SM


# ---------------------------------------------------------------------------
# 1. Two-Stage Model Integration (Qwen3:4B + MedGemma 1.5 4B) & Unified Summary
# ---------------------------------------------------------------------------

def test_two_stage_qwen_and_medgemma_model_routing_and_unified_summary(app, monkeypatch):
    """Verify Stage 1 extraction calls `ollama_model` (qwen3:4b) and Stage 2 medical analysis calls `medgemma_model` (medgemma1.5)."""
    from ..config import get_settings
    s = get_settings()
    assert s.ollama_model == "qwen3:4b"
    assert s.medgemma_model == "medgemma1.5"

    models_called: list[tuple[str, str]] = []

    def tracking_chat_json(system: str, user: str, schema: dict, *, num_predict: int = 700, model: str | None = None, timeout: float | None = None):
        from ..services import prompts
        effective_model = model or s.ollama_model
        if schema == prompts.EXTRACT_SCHEMA:
            models_called.append(("extract", effective_model))
            return {"facts": [], "english_translation": user}, {"model": effective_model, "latency_ms": 5, "raw": "{}"}
        if schema == prompts.SUMMARY_SCHEMA:
            models_called.append(("summary", effective_model))
            out = {
                "presenting_concern_narrative": "58-year-old patient presenting with chest pain and shortness of breath for 2 days.",
                "preliminary_interpretation": "Reported chest pain and breathlessness alongside elevated Troponin I (0.85 ng/mL) and diabetes history warrant urgent cardiac evaluation.",
                "clinician_summary": "58-year-old patient with diabetes presenting with 2 days of chest pain and shortness of breath. Uploaded report shows elevated Troponin I at 0.85 ng/mL (reference 0.0 - 0.04) and low Hemoglobin at 9.4 g/dL. Routed to URGENT queue under rule DEMO-U1.",
                "clinical_relevance_note": "Elevated Troponin I and low Hemoglobin may relate to reported chest pain and breathlessness.",
            }
            return out, {"model": effective_model, "latency_ms": 12, "raw": "{}"}
        if schema == prompts.FOLLOWUP_SCHEMA:
            models_called.append(("followup", effective_model))
            return {"questions": []}, {"model": effective_model, "latency_ms": 4, "raw": "{}"}
        return {"classification": "genuine_contradiction", "rationale": "Test"}, {"model": effective_model, "latency_ms": 2, "raw": "{}"}

    monkeypatch.setattr(llm, "chat_json", tracking_chat_json)

    doc = Api(app)
    make_user("mg-doc@demo-clinic.org", role="doctor", name="Dr Rao")
    doc.login("mg-doc@demo-clinic.org")

    pat = Api(app)
    pat.signup("mg-pat@demo-clinic.org", name="Suresh Patel", age=58, mobile="+919800000101")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    pat.post(f"/api/cases/{cid}/messages", {"text": "I have chest pain and shortness of breath for 2 days. I have diabetes and take metformin 500 mg. No fever."})
    lab_lines = [
        "Troponin I: 0.85 ng/mL (Ref: 0.0 - 0.04) HIGH",
        "Hemoglobin: 9.4 g/dL (Ref: 13.0 - 17.0) LOW",
    ]
    up = pat.upload(f"/api/cases/{cid}/uploads", "cardiac_panel.pdf", media.digital_pdf(lab_lines), "application/pdf").json()["upload"]
    pat.post(f"/api/cases/{cid}/uploads/{up['id']}/confirm", {"text": "\n".join(lab_lines)})
    pat.post(f"/api/cases/{cid}/submit")

    # Stage 1 must use qwen3:4b, Stage 2 summary/followup must use medgemma1.5
    assert ("extract", "qwen3:4b") in models_called
    assert ("summary", "medgemma1.5") in models_called

    d = doc.get(f"/api/doctor/cases/{cid}").json()
    ps = d["patient_summary"]
    assert ps["analysis_status"] == "medgemma_complete"
    assert ps["model"] == "medgemma1.5"
    assert "Troponin I" in ps["clinician_summary"]
    assert "0.85" in ps["preliminary_interpretation"]
    assert ps["positive_and_negative_findings"]["important_positives"]
    assert any("fever" in x.lower() for x in ps["positive_and_negative_findings"]["important_negatives"])
    assert ps["important_findings"] and len(ps["important_findings"]) == 2
    assert ps["limitations_and_sources"]["model_used"] == "medgemma1.5"
    assert ps["patient_draft_summary"]


# ---------------------------------------------------------------------------
# 2. Contradiction Detection Across Text, Reports, and Follow-ups (Both Sides)
# ---------------------------------------------------------------------------

def test_contradictions_medication_allergy_duration_and_both_sides_detail(app):
    doc = Api(app)
    make_user("con-doc@demo-clinic.org", role="doctor", name="Dr Iyer")
    doc.login("con-doc@demo-clinic.org")

    pat = Api(app)
    pat.signup("con-pat@demo-clinic.org", name="Anिल Kumar", age=54, mobile="+919800000102")
    consent(pat)
    cid = pat.post("/api/cases").json()["id"]

    # Message 1: headache for 2 days, denies metformin, denies allergies
    pat.post(f"/api/cases/{cid}/messages", {"text": "I have headache for 2 days. I do not take metformin. No known drug allergies."})
    # Message 2: headache for 2 weeks, allergic to penicillin
    pat.post(f"/api/cases/{cid}/messages", {"text": "I have headache for 2 weeks. I am allergic to penicillin."})
    # Uploaded report lists Metformin 500 mg
    lab_lines = ["Current Medications: Metformin 500 mg", "Glucose: 185 mg/dL (Ref: 70 - 110) HIGH"]
    up = pat.upload(f"/api/cases/{cid}/uploads", "rx_report.pdf", media.digital_pdf(lab_lines), "application/pdf").json()["upload"]
    pat.post(f"/api/cases/{cid}/uploads/{up['id']}/confirm", {"text": "\n".join(lab_lines)})
    pat.post(f"/api/cases/{cid}/submit")

    d = doc.get(f"/api/doctor/cases/{cid}").json()
    conflicts = d["conflicts"]
    concepts = {c["concept"] for c in conflicts}
    # Must detect medication:metformin conflict, allergy:penicillin conflict, and headache duration conflict
    assert "medication:metformin" in concepts
    assert "allergy:penicillin" in concepts
    assert "headache" in concepts

    for c in conflicts:
        assert c.get("statement_a")
        assert c.get("source_a")
        assert c.get("statement_b")
        assert c.get("source_b")
        assert c.get("why_they_disagree")
        assert c.get("clarification_needed")


# ---------------------------------------------------------------------------
# 3. Smart, Adaptive Hybrid Follow-up Questions & Answer Integration
# ---------------------------------------------------------------------------

def test_adaptive_followup_examples_chest_allergy_headache_and_answer_reranking(app):
    doc = Api(app)
    make_user("fu2-doc@demo-clinic.org", role="doctor", name="Dr Nair")
    doc.login("fu2-doc@demo-clinic.org")

    # Example A: "I feel uncomfortable in my chest when I walk"
    pat1 = Api(app)
    pat1.signup("fu2-p1@demo-clinic.org", name="Case Chest", age=60, mobile="+919800000103")
    consent(pat1)
    c1 = pat1.post("/api/cases").json()["id"]
    r_msg = pat1.post(f"/api/cases/{c1}/messages", {"text": "I feel uncomfortable in my chest when I walk."}).json()
    assert r_msg["emergency"]["triggered"] is True
    pat1.post(f"/api/cases/{c1}/submit")
    v1 = pat1.get(f"/api/cases/{c1}").json()
    assert v1["emergency_flagged"] is True
    q_keys_1 = [q["key"] for q in v1["questions"]]
    # Must ask about chest pain duration/progression/associated red flags, not age (known=60)
    assert any("chest_pain" in k for k in q_keys_1)
    assert "age" not in q_keys_1

    # Example B: "I am allergic to penicillin" -> does NOT ask "allergies", asks "allergy_reaction:penicillin"
    pat2 = Api(app)
    pat2.signup("fu2-p2@demo-clinic.org", name="Case Allergy", age=35, mobile="+919800000104")
    consent(pat2)
    c2 = pat2.post("/api/cases").json()["id"]
    pat2.post(f"/api/cases/{c2}/messages", {"text": "I have a skin rash for 3 days, stable. I am allergic to penicillin."})
    pat2.post(f"/api/cases/{c2}/submit")
    v2 = pat2.get(f"/api/cases/{c2}").json()
    q_keys_2 = [q["key"] for q in v2["questions"]]
    assert "allergies" not in q_keys_2
    assert "allergy_reaction:penicillin" in q_keys_2

    # Example C: "I have had a headache since yesterday" -> duration is known, asks progression/severity/associated
    pat3 = Api(app)
    pat3.signup("fu2-p3@demo-clinic.org", name="Case Headache", age=40, mobile="+919800000105")
    consent(pat3)
    c3 = pat3.post("/api/cases").json()["id"]
    pat3.post(f"/api/cases/{c3}/messages", {"text": "I have had a headache since yesterday."})
    pat3.post(f"/api/cases/{c3}/submit")
    v3 = pat3.get(f"/api/cases/{c3}").json()
    q_keys_3 = [q["key"] for q in v3["questions"]]
    assert "duration:headache" not in q_keys_3
    assert "progression:headache" in q_keys_3 or "severity:headache" in q_keys_3

    # Example D: Answering a missing duration question moves a case from NEEDS_REVIEW to NORMAL and updates summary
    pat4 = Api(app)
    pat4.signup("fu2-p4@demo-clinic.org", name="Case Answer", age=32, mobile="+919800000106")
    consent(pat4)
    c4 = pat4.post("/api/cases").json()["id"]
    pat4.post(f"/api/cases/{c4}/messages", {"text": "I have back pain, 3/10, staying the same."})
    pat4.post(f"/api/cases/{c4}/submit")

    d_before = doc.get(f"/api/doctor/cases/{c4}").json()
    assert d_before["case"]["proposed_queue"] == "NEEDS_REVIEW"
    dur_q = next(q for q in d_before["questions"] if q["key"] == "duration:back_pain")

    ans_res = pat4.post(f"/api/cases/{c4}/questions/{dur_q['id']}/answer", {"text": "for 4 days, started gradually"})
    assert ans_res.status_code == 200

    d_after = doc.get(f"/api/doctor/cases/{c4}").json()
    assert d_after["case"]["proposed_queue"] == "NORMAL"
    assert d_after["patient_summary"]["answered_followups"]
    assert any(q["key"] == "duration:back_pain" for q in d_after["patient_summary"]["answered_followups"])


# ---------------------------------------------------------------------------
# 4. Per-Category (#1, #2, #3...) Ranking, Hidden Score, and Workspace Views
# ---------------------------------------------------------------------------

def test_per_category_ranking_hidden_numerical_score_and_workspace_visibility(app):
    staff = Api(app)
    make_user("rk-staff@demo-clinic.org", role="staff", name="Staff Priya")
    staff.login("rk-staff@demo-clinic.org")

    doc = Api(app)
    make_user("rk-doc@demo-clinic.org", role="doctor", name="Dr Bose")
    doc.login("rk-doc@demo-clinic.org")

    # Create 2 URGENT cases, 2 NEEDS_REVIEW cases, 2 NORMAL cases
    def submit_case(email: str, age: int, text: str, second_text: str | None = None):
        p = Api(app)
        p.signup(email, name=email.split("@")[0], age=age, mobile="+919800000199")
        consent(p)
        cid = p.post("/api/cases").json()["id"]
        p.post(f"/api/cases/{cid}/messages", {"text": text})
        if second_text:
            p.post(f"/api/cases/{cid}/messages", {"text": second_text})
        p.post(f"/api/cases/{cid}/submit")
        return p, cid

    # Urgent 1 (higher risk: Tier 1 chest pain + breathlessness + elderly + diabetes) vs Urgent 2 (Tier 2 mild chest pain in 28yo)
    _, u_high = submit_case("u-high@demo-clinic.org", 76, "I have severe chest pain and shortness of breath for 2 hours, getting worse quickly. I have diabetes and heart disease.")
    _, u_low = submit_case("u-low@demo-clinic.org", 28, "I have mild chest pain for 1 day, stable.")

    # Needs Review 1 (safety-critical conflict on confusion in 72yo) vs Needs Review 2 (missing duration on sore throat in 29yo)
    _, nr_high = submit_case("nr-high@demo-clinic.org", 72, "I have headache for 2 days and feel confused.", "I am not confused.")
    _, nr_low = submit_case("nr-low@demo-clinic.org", 29, "I have a mild sore throat.")

    # Normal 1 (74yo worsening fever + cough + diabetes) vs Normal 2 (23yo mild sore throat for 2 days, improving)
    _, norm_high = submit_case("n-high@demo-clinic.org", 74, "I have fever of 101.5 F and cough for 4 days, getting worse. SpO2 is 96%. I have diabetes.")
    p_norm_low, norm_low = submit_case("n-low@demo-clinic.org", 23, "I have a mild sore throat for 2 days, getting better. No fever.")

    res = staff.get("/api/staff/cases?page_size=50").json()
    cat_q = res["category_queues"]

    # Verify each category has independent #1, #2... ranks and expected relative order
    u_ids = [r["id"] for r in cat_q["URGENT"]]
    assert u_high in u_ids and u_low in u_ids
    assert u_ids.index(u_high) < u_ids.index(u_low)
    assert [r["category_rank"] for r in cat_q["URGENT"]] == list(range(1, len(cat_q["URGENT"]) + 1))

    nr_ids = [r["id"] for r in cat_q["NEEDS_REVIEW"]]
    assert nr_high in nr_ids and nr_low in nr_ids
    assert nr_ids.index(nr_high) < nr_ids.index(nr_low)
    assert [r["category_rank"] for r in cat_q["NEEDS_REVIEW"]] == list(range(1, len(cat_q["NEEDS_REVIEW"]) + 1))

    n_ids = [r["id"] for r in cat_q["NORMAL"]]
    assert norm_high in n_ids and norm_low in n_ids
    assert n_ids.index(norm_high) < n_ids.index(norm_low)
    assert [r["category_rank"] for r in cat_q["NORMAL"]] == list(range(1, len(cat_q["NORMAL"]) + 1))

    # Verify filtered queue endpoint returns queue_rank = 1, 2... for that category
    res_norm = doc.get("/api/doctor/cases?queue=NORMAL").json()["cases"]
    assert [r["queue_rank"] for r in res_norm] == list(range(1, len(res_norm) + 1))

    # Verify numerical score (`internal_score`) is NEVER exposed in patient, staff, or doctor responses
    for row in res["cases"]:
        assert "internal_score" not in row
        assert "priority_score" not in row
        assert row.get("ranking_explanation")

    pat_view = p_norm_low.get(f"/api/cases/{norm_low}").json()
    assert "internal_score" not in pat_view
    assert "queue_rank" not in pat_view
    assert pat_view.get("draft_summary")


# ---------------------------------------------------------------------------
# 5. 100-Case Synthetic Benchmark Evaluation (Categorisation & Within-Category Ranking)
# ---------------------------------------------------------------------------

def test_synthetic_100_cases_categorisation_and_within_category_ranking(app):
    """Run all 100 synthetic cases through the deterministic extraction, reconciliation, rules engine,
    and priority scorer, verifying 100% category accuracy and expected relative rankings."""
    specs = build_synthetic_100()
    assert len(specs) == 100

    ruleset = pipeline.active_ruleset()
    now_dt = utcnow()
    evals: list[PS.PriorityEvaluation] = []
    cases_by_id: dict[str, Case] = {}
    code_to_id: dict[str, str] = {}
    syn_uid = make_user("syn-bench-patient@demo-clinic.org", role="patient", name="Synthetic Benchmark Patient")

    with session_scope() as db:
        for idx, sp in enumerate(specs):
            submitted_time = now_dt - timedelta(minutes=sp.waiting_minutes)
            c = Case(
                case_number=sp.case_code,
                patient_id=syn_uid,
                status="submitted",
                language="en",
                chief_complaint=sp.text[:200],
                submitted_at=submitted_time,
                created_at=submitted_time,
                is_synthetic=True,
            )
            db.add(c)
            db.flush()
            cases_by_id[c.id] = c
            code_to_id[sp.case_code] = c.id

            # Extract facts from primary text
            drafts1 = L.extract_all(sp.text, locator="message #1")
            F.persist_drafts(db, c.id, drafts1, source_type="message", source_id=f"m1-{c.id}", speaker="patient", stated_at=submitted_time)

            # Extract facts from optional second message
            if sp.second_text:
                drafts2 = L.extract_all(sp.second_text, locator="message #2")
                F.persist_drafts(db, c.id, drafts2, source_type="message", source_id=f"m2-{c.id}", speaker="patient", stated_at=submitted_time)

            # Profile age if provided and not already extracted
            eff_facts = F.effective_facts(db, c.id)
            if sp.age is not None and not any(f.concept == "age" for f in eff_facts):
                db.add(Fact(
                    case_id=c.id, category="demographic", concept="age", label="Age",
                    polarity="present", temporal="current", value_num=float(sp.age), unit="years",
                    source_type="profile", source_id=c.patient_id, locator="profile", speaker="patient",
                    excerpt=f"age {sp.age}", method="profile", confidence=1.0, flags=[],
                ))
                db.flush()

            # Lab / report lines
            imp_labs = []
            if sp.report_lines:
                rep_text = "\n".join(sp.report_lines)
                rep_drafts = L.extract_all(rep_text, locator="uploaded report")
                F.persist_drafts(db, c.id, rep_drafts, source_type="document", source_id=f"doc-{c.id}", speaker="document", stated_at=submitted_time)
                from ..services import lab_extractor as LE
                for r in LE.extract_lab_results(rep_text, locator_prefix="page 1"):
                    rd = LE.to_dict(r)
                    imp_labs.append({
                        "test": rd["test_name"],
                        "value": str(rd.get("raw_value", "")),
                        "unit": rd.get("unit"),
                        "reference_range": rd.get("ref_text"),
                        "flag": rd.get("computed_flag") or rd.get("printed_flag"),
                        "interpretation": rd.get("interpretation"),
                    })

            # Emergency red-flag check
            from ..services import safety
            em = safety.screen(sp.text, ruleset)
            if em["triggered"]:
                c.emergency_flagged = True
                c.escalation_level = "emergency"

            # Reconcile contradictions
            reconcile.detect(db, c.id, ruleset)

            # Evaluate deterministic rules engine
            eff_facts = F.effective_facts(db, c.id)
            conflicts = list(db.query(Conflict).filter(Conflict.case_id == c.id).all())
            ctx = RE.CaseContext(
                facts=[F.to_view(f) for f in eff_facts],
                conflicts=[RE.ConflictView(cf.id, cf.concept, cf.kind, cf.safety_critical, cf.status) for cf in conflicts],
                extraction_ok=True,
                has_any_input=True,
            )
            res = RE.evaluate(ctx, ruleset)
            c.proposed_queue = res.queue
            c.sort_tier = res.sort_tier
            c.sort_escalation = res.sort_escalation

            asm = Assessment(
                case_id=c.id,
                ruleset_version=res.ruleset_version,
                demonstration_only=res.demonstration_only,
                trigger="submission",
                proposed_queue=res.queue,
                summary=res.summary,
                triggered_rules=res.triggered_rules,
                supporting_fact_ids=res.supporting_fact_ids,
                missing_information=res.missing_information,
                open_conflict_ids=res.open_conflict_ids,
                data_quality=res.data_quality,
                sort_tier=res.sort_tier,
                sort_escalation=res.sort_escalation,
            )
            db.add(asm)
            db.flush()
            c.current_assessment_id = asm.id

            # Verify expected category matches
            assert res.queue == sp.expected_category, f"{sp.case_code} ({sp.title}): expected {sp.expected_category}, got {res.queue} ({res.summary})"
            if sp.expected_emergency:
                assert c.emergency_flagged is True, f"{sp.case_code}: expected emergency_flagged=True"

            ev = PS.score_case(
                c, eff_facts, conflicts, asm, ruleset,
                summary_payload={"report_findings": {"important_findings": imp_labs}},
                now=now_dt,
            )
            evals.append(ev)

        ranked = PS.rank_cases_by_category(evals, cases_by_id=cases_by_id)

        # Verify category counts: 25 URGENT, 35 NEEDS_REVIEW, 40 NORMAL
        u_evals = [e for e in ranked.values() if e.category == "URGENT"]
        nr_evals = [e for e in ranked.values() if e.category == "NEEDS_REVIEW"]
        n_evals = [e for e in ranked.values() if e.category == "NORMAL"]
        assert len(u_evals) == 25
        assert len(nr_evals) == 35
        assert len(n_evals) == 40

        # Verify sequential 1..K ranks inside every category
        assert sorted(e.category_rank for e in u_evals) == list(range(1, 26))
        assert sorted(e.category_rank for e in nr_evals) == list(range(1, 36))
        assert sorted(e.category_rank for e in n_evals) == list(range(1, 41))

        # Verify relative ranking invariants inside URGENT:
        # SYN-001 (78yo crushing chest pain + breathlessness + sweating + SpO2 89% + high Troponin) ranks #1 in URGENT,
        # ahead of SYN-016 (26yo mild chest tightness) and SYN-017 (24yo mild shortness of breath)
        assert ranked[code_to_id["SYN-001"]].category_rank == 1
        assert ranked[code_to_id["SYN-001"]].category_rank < ranked[code_to_id["SYN-002"]].category_rank
        assert ranked[code_to_id["SYN-002"]].category_rank < ranked[code_to_id["SYN-016"]].category_rank
        assert ranked[code_to_id["SYN-016"]].category_rank <= ranked[code_to_id["SYN-017"]].category_rank

        # Verify relative ranking invariants inside NEEDS_REVIEW:
        # Safety-critical conflicts (SYN-026..SYN-029) rank ahead of non-safety missing duration on sore throat (SYN-049)
        assert ranked[code_to_id["SYN-026"]].category_rank < ranked[code_to_id["SYN-032"]].category_rank
        assert ranked[code_to_id["SYN-032"]].category_rank < ranked[code_to_id["SYN-049"]].category_rank

        # Verify relative ranking invariants inside NORMAL:
        # SYN-061 (78yo severe worsening abdominal pain + fever + high WBC + comorbidities) ranks #1 in NORMAL,
        # ahead of SYN-073 (45yo moderate headache), which ranks ahead of SYN-100 (20yo mild sore throat, 1 min wait)
        assert ranked[code_to_id["SYN-061"]].category_rank == 1
        assert ranked[code_to_id["SYN-061"]].category_rank < ranked[code_to_id["SYN-073"]].category_rank
        assert ranked[code_to_id["SYN-073"]].category_rank < ranked[code_to_id["SYN-100"]].category_rank

        # Waiting-time fairness tie-breaker: SYN-087 (180 min wait) ranks ahead of identical SYN-088 (5 min wait)
        assert ranked[code_to_id["SYN-087"]].category_rank < ranked[code_to_id["SYN-088"]].category_rank
