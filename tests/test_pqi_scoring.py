"""PQI rules applied in code: lift, strict caps, AFI, headline scores, bands, guardrails, review triggers."""
import copy

import pytest

import pqi_scoring as sc
from test_pqi_master import master  # noqa: F401


def srt(master, srt_id, **changes):
    record = copy.deepcopy(next(s for s in master["srts"] if s["srt_id"] == srt_id))
    record.update(changes)
    return record


def observed(**fields):
    base = {"core_response_level": 5, "excellence_lift": 0, "decision_made": "Yes", "critical_failure": False,
            "critical_failure_reason": "", "unresolved_contradiction": False, "afi_activated": "Yes",
            "afi_evidence_level": 3, "final_srt_score": 5, "score_justification": ["x"], "primary_gap": "g",
            "secondary_evidence": ""}
    base.update(fields)
    return base


def test_only_core_7_can_be_lifted(master):
    s = srt(master, "C2-01", decision_applicable="Yes")
    assert sc.apply_rules(master, s, observed(core_response_level=7, excellence_lift=2))["final_score"] == 9
    assert sc.apply_rules(master, s, observed(core_response_level=7, excellence_lift=1))["final_score"] == 8
    six = sc.apply_rules(master, s, observed(core_response_level=6, excellence_lift=2))
    assert (six["final_score"], six["excellence_lift"], six["evaluator_excellence_lift"]) == (6, 0, 2)


def test_decision_cap_is_strict(master):
    s = srt(master, "C2-01", decision_applicable="Yes")
    capped = sc.apply_rules(master, s, observed(core_response_level=7, excellence_lift=2, decision_made="No"))
    assert (capped["final_score"], capped["caps_applied"], capped["decision_cap_triggered"]) == (6, ["decision"], True)
    low = sc.apply_rules(master, s, observed(core_response_level=4, decision_made="No"))
    assert (low["final_score"], low["caps_applied"]) == (4, [])
    free = sc.apply_rules(master, srt(master, "C2-01", decision_applicable="No"),
                          observed(core_response_level=7, excellence_lift=2, decision_made="No"))
    assert (free["final_score"], free["decision_made"]) == (9, "Not Applicable")


def test_critical_failure_cap_and_master_severity(master):
    result = sc.apply_rules(master, srt(master, "C1-01"), observed(core_response_level=6, critical_failure=True,
                                                                    critical_failure_reason="Accepted the gift"))
    assert (result["final_score"], result["caps_applied"]) == (2, ["critical_failure"])
    assert (result["critical_failure_severity"], result["critical_failure_type"]) == ("Severe", "Unclassified")
    typed = sc.apply_rules(master, srt(master, "C4-10", critical_failure_type="Technical-Safety"),
                           observed(critical_failure=True))
    assert (typed["critical_failure_severity"], typed["critical_failure_type"]) == ("Severe", "Technical-Safety")
    both = sc.apply_rules(master, srt(master, "C2-01", decision_applicable="Yes"),
                          observed(core_response_level=7, excellence_lift=2, decision_made="No", critical_failure=True))
    assert both["final_score"] == 2 and both["caps_applied"] == ["decision", "critical_failure"]
    none = sc.apply_rules(master, srt(master, "C1-01"), observed(critical_failure=False, critical_failure_reason="x"))
    assert (none["critical_failure_severity"], none["critical_failure_type"], none["critical_failure_reason"]) == ("None", "None", "")


def test_afi_activation(master):
    assert sc.apply_rules(master, srt(master, "C1-01", afi_applicability="Yes"),
                          observed(afi_activated="No", afi_evidence_level=4))["afi_activated"] is True
    conditional = srt(master, "C1-01", afi_applicability="Conditional")
    on = sc.apply_rules(master, conditional, observed(afi_activated="Yes", afi_evidence_level=2))
    off = sc.apply_rules(master, conditional, observed(afi_activated="No", afi_evidence_level=None))
    assert (on["afi_activated"], on["afi_evidence_level"]) == (True, 2)
    assert (off["afi_activated"], off["afi_evidence_level"]) == (False, None)
    never = sc.apply_rules(master, srt(master, "C1-01", afi_applicability="No"), observed(afi_evidence_level=5))
    assert (never["afi_activated"], never["afi_evidence_level"]) == (False, None)


def test_blank_answers(master):
    yes = sc.blank_result(master, srt(master, "C1-01", afi_applicability="Yes"))
    conditional = sc.blank_result(master, srt(master, "C1-01", afi_applicability="Conditional"))
    assert (yes["final_score"], yes["status"], yes["afi_activated"], yes["afi_evidence_level"]) == (0, "blank", True, 0)
    assert (conditional["afi_activated"], conditional["afi_evidence_level"]) == (False, None)


def result(srt_id, competency, score, afi=None, cf=False, severity="None", ftype="None", status="scored",
           contradiction=False):
    return {"srt_id": srt_id, "primary_competency": competency, "final_score": score, "status": status,
            "afi_activated": afi is not None, "afi_evidence_level": afi, "critical_failure": cf,
            "critical_failure_severity": severity, "critical_failure_type": ftype,
            "unresolved_contradiction": contradiction}


def profile(scores):
    """30 results; `scores` maps competency code → the three SRT scores."""
    return [result(f"{code}-{i}", code, s) for code, trio in scores.items() for i, s in enumerate(trio)]


def test_headline_formulae(master):
    scores = {f"C{i}": [i - 1, i - 1, i - 1] for i in range(1, 11)}      # C1=0 … C10=9
    head = sc.headline(master, profile(scores))
    assert [c["score"] for c in head["competencies"]] == [float(i) for i in range(10)]
    assert head["overall"] == pytest.approx(45.0)                        # mean 4.5 × 10
    assert head["business_acumen"] == pytest.approx((0 * 1 + 1 * 1.5 + 2 * 1.5 + 3 * 1 + 4 * 1.5) / 6.5 * 10)
    assert head["technical_acumen"] == pytest.approx((5 * 1 + 6 * 1.5 + 7 * 1 + 8 * 1.5 + 9 * 1.5) / 6.5 * 10)
    assert head["afi"] is None and head["afi_scored_srts"] == 0

    mixed = profile({f"C{i}": [5, 5, 5] for i in range(1, 11)})
    mixed[0]["afi_activated"], mixed[0]["afi_evidence_level"] = True, 5
    mixed[1]["afi_activated"], mixed[1]["afi_evidence_level"] = True, 0
    mixed[2]["afi_activated"], mixed[2]["afi_evidence_level"] = True, 3
    afi = sc.headline(master, mixed)
    assert afi["afi"] == pytest.approx(100 * 8 / 15) and afi["afi_band"] == "Developing prevention"


@pytest.mark.parametrize("total_points,band", [(149, "Not Yet Ready"), (150, "Developing"), (188, "Developing"),
                                               (189, "Ready"), (228, "High Readiness"), (270, "High Readiness")])
def test_readiness_bands(master, total_points, band):
    # Overall = sum of the 30 SRT scores / 3, so points 149 → 49.67 and 189 → 63.0.
    results = profile({f"C{i}": [0, 0, 0] for i in range(1, 11)})
    remaining = total_points
    for r in results:
        r["final_score"] = min(9, remaining)
        remaining -= r["final_score"]
    head = sc.headline(master, results)
    assert head["overall"] == pytest.approx(total_points / 3)
    assert sc.readiness(master, head, results)["band"] == band


def high_profile():
    return profile({f"C{i}": [8, 8, 8] for i in range(1, 11)})       # Overall 80: High Readiness


def test_integrity_guardrail_caps_at_developing(master):
    results = high_profile()
    results[0].update(critical_failure=True, critical_failure_severity="Severe", critical_failure_type="Integrity")
    ready = sc.readiness(master, sc.headline(master, results), results)
    assert (ready["score_band"], ready["band"], ready["manual_review_required"]) == ("High Readiness", "Developing", False)
    major = high_profile()
    major[0].update(critical_failure=True, critical_failure_severity="Major", critical_failure_type="Integrity")
    assert sc.readiness(master, sc.headline(master, major), major)["band"] == "High Readiness"


def test_technical_guardrail_counts_type_not_competency(master):
    results = high_profile()
    results[9].update(critical_failure=True, critical_failure_severity="Severe", critical_failure_type="Technical-Safety")
    assert sc.readiness(master, sc.headline(master, results), results)["band"] == "High Readiness"
    # A second one in a Business competency (C4-03 / C4-10 style) still counts.
    results[10].update(primary_competency="C4", critical_failure=True, critical_failure_severity="Severe",
                       critical_failure_type="Technical-Safety")
    ready = sc.readiness(master, sc.headline(master, results), results)
    assert (ready["band"], ready["manual_review_required"]) == ("Ready", True)


def test_unclassified_severe_failure_goes_to_manual_review_without_a_cap(master):
    results = high_profile()
    results[0].update(critical_failure=True, critical_failure_severity="Severe", critical_failure_type="Unclassified")
    ready = sc.readiness(master, sc.headline(master, results), results)
    assert (ready["band"], ready["manual_review_required"]) == ("High Readiness", True)
    assert "does not classify Critical_Failure_Type" in ready["manual_review_reasons"][0]
    other = high_profile()
    other[0].update(critical_failure=True, critical_failure_severity="Severe", critical_failure_type="Other")
    assert sc.readiness(master, sc.headline(master, other), other)["manual_review_required"] is False


@pytest.mark.parametrize("overall,near", [(47.9, False), (48.0, True), (52.0, True), (52.1, False), (60.9, False),
                                          (61.0, True), (65.0, True), (74.0, True), (78.0, True), (78.1, False)])
def test_second_review_proximity(master, overall, near):
    assert sc.near_boundary(master, overall) is near


def test_review_plan_reasons_never_reveal_the_score_or_band(master):
    results = profile({f"C{i}": [5, 5, 5] for i in range(1, 10)} | {"C10": [5, 5, 3]})   # Overall 49.3
    results[4].update(critical_failure=True)
    results[7].update(unresolved_contradiction=True)
    results[8]["status"] = "blank"
    head = sc.headline(master, results)
    plan = sc.review_plan(master, head, results, [{"srt_ids": ["C2-0", "C9-9-missing"], "description": "Refuses then allows"}])
    assert plan["triggers"] == ["confirmed_critical_failure", "material_contradiction", "near_readiness_boundary"]
    assert "C3-2" not in plan["srts"]                                   # blank answers are not reviewed
    assert len(plan["srts"]) == 29
    assert plan["srts"]["C2-1"].startswith("A Critical Failure")
    assert plan["srts"]["C3-1"].startswith("The earlier evaluation noted")
    assert plan["srts"]["C2-0"].startswith("A possible contradiction")
    for reason in plan["srts"].values():
        assert "boundary" not in reason.lower() and "readiness" not in reason.lower() and "49" not in reason


# ── Reported score scale (HR, 2026-09-27) ────────────────────────────────────
def level_rows(levels: dict) -> list:
    """Minimal SRT results: competency code -> the levels of its SRTs."""
    return [{"srt_id": f"{code}-{i + 1:02d}", "primary_competency": code, "final_score": level,
             "afi_activated": False, "afi_evidence_level": None}
            for code, values in levels.items() for i, level in enumerate(values)]


def test_scale_c1_is_what_hr_adopted():
    assert sc.CURRENT_SCALE == "C1"
    assert sc.SCORE_SCALES["C1"]["points"] == [0, 20, 35, 50, 60, 70, 80, 88, 94, 100]
    points = sc.SCORE_SCALES["C1"]["points"]
    assert all(a < b for a, b in zip(points, points[1:]))               # a better answer always earns more
    assert [sc.level_percent(level) for level in (0, 6, 9)] == [0, 80, 100]


def test_reported_headline_converts_levels_before_averaging(master):
    # Every competency answered at level 6 on all three SRTs reads 80, not 60.
    results = level_rows({c["code"]: [6, 6, 6] for c in master["competencies"]})
    shown = sc.reported_headline(master, results)
    assert shown["overall"] == pytest.approx(80.0)
    assert shown["technical_acumen"] == pytest.approx(80.0) and shown["business_acumen"] == pytest.approx(80.0)
    assert sc.headline(master, results)["overall"] == pytest.approx(60.0)          # native scale unchanged


def test_reported_headline_reproduces_the_pilot(master):
    # Haseeb Khan's calibrated levels from the pilot (report 177a28b6, 2026-09-26):
    # 64.0 native, 81.6 on C1 — the figures HR reviewed.
    levels = {"C1": [6, 4, 8], "C2": [7, 7, 2], "C3": [3, 6, 5], "C4": [8, 7, 4], "C5": [6, 8, 7],
              "C6": [8, 6, 7], "C7": [6, 6, 8], "C8": [8, 5, 8], "C9": [8, 6, 6], "C10": [7, 7, 8]}
    results = level_rows(levels)
    assert round(sc.headline(master, results)["overall"], 1) == 64.0
    shown = sc.reported_headline(master, results)
    assert (round(shown["overall"], 1), round(shown["technical_acumen"], 1), round(shown["business_acumen"], 1)) == (
        81.6, 86.5, 76.2)
