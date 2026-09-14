"""PQI scoring rules the application applies itself — deterministic, no AI involved.

The evaluator reports what it saw (core level, lift, whether a decision was
made, whether a critical failure was advocated, AFI evidence). Everything that
turns those observations into numbers is here: the Excellence Lift rule, the
strict Decision and Critical Failure caps, AFI activation, competency, Overall,
Acumen and AFI scores, readiness bands, guardrails and second-review triggers.
All settings come from the master.
"""
from __future__ import annotations

BLANK_JUSTIFICATION = "No response was given."


def _severity_and_type(srt, confirmed: bool):
    if not confirmed:
        return "None", "None"
    return srt["critical_failure_severity"] or "None", srt.get("critical_failure_type") or "Unclassified"


def apply_rules(master, srt, ev: dict) -> dict:
    """One SRT's final result from a validated evaluator observation."""
    core = ev["core_response_level"]
    lift = ev["excellence_lift"] if core == 7 else 0      # only Core 7 can be lifted
    score = core + lift

    decision_applicable = srt["decision_applicable"] == "Yes"
    decision_made = ev["decision_made"] if decision_applicable else "Not Applicable"
    decision_cap_triggered = decision_applicable and decision_made == "No"
    caps = []
    if decision_cap_triggered and score > master["decision_cap"]:
        score = master["decision_cap"]
        caps.append("decision")

    confirmed = bool(ev["critical_failure"])
    if confirmed and score > master["critical_failure_cap"]:
        score = master["critical_failure_cap"]
        caps.append("critical_failure")
    severity, failure_type = _severity_and_type(srt, confirmed)

    applicability = srt["afi_applicability"]
    if applicability == "Yes":
        afi_activated, afi_level = True, ev["afi_evidence_level"]
    elif applicability == "Conditional" and ev["afi_activated"] == "Yes":
        afi_activated, afi_level = True, ev["afi_evidence_level"]
    else:
        afi_activated, afi_level = False, None

    return {
        "srt_id":                   srt["srt_id"],
        "primary_competency":       srt["primary_competency"],
        "status":                   "scored",
        "core_response_level":      core,
        "excellence_lift":          lift,
        "evaluator_excellence_lift": ev["excellence_lift"],
        "final_score":              score,
        "evaluator_final_score":    ev.get("final_srt_score"),
        "caps_applied":             caps,
        "decision_applicable":      decision_applicable,
        "decision_made":            decision_made,
        "decision_cap_triggered":   decision_cap_triggered,
        "critical_failure":         confirmed,
        "critical_failure_reason":  ev.get("critical_failure_reason", "") if confirmed else "",
        "critical_failure_severity": severity,
        "critical_failure_type":    failure_type,
        "unresolved_contradiction": bool(ev.get("unresolved_contradiction")),
        "afi_applicability":        applicability,
        "afi_activated":            afi_activated,
        "afi_evidence_level":       afi_level,
        "score_justification":      list(ev.get("score_justification") or []),
        "primary_gap":              ev.get("primary_gap", ""),
        "secondary_evidence":       ev.get("secondary_evidence", ""),
    }


def blank_result(master, srt) -> dict:
    """A blank or missing answer: score 0 without calling the evaluator.

    A blank AFI=Yes SRT scores AFI 0 and stays in the denominator; a blank
    Conditional SRT does not activate AFI (locked decision, 13 Sep 2026).
    """
    applicability = srt["afi_applicability"]
    decision_applicable = srt["decision_applicable"] == "Yes"
    return {
        "srt_id":                   srt["srt_id"],
        "primary_competency":       srt["primary_competency"],
        "status":                   "blank",
        "core_response_level":      0,
        "excellence_lift":          0,
        "evaluator_excellence_lift": None,
        "final_score":              0,
        "evaluator_final_score":    None,
        "caps_applied":             [],
        "decision_applicable":      decision_applicable,
        "decision_made":            "No" if decision_applicable else "Not Applicable",
        "decision_cap_triggered":   decision_applicable,
        "critical_failure":         False,
        "critical_failure_reason":  "",
        "critical_failure_severity": "None",
        "critical_failure_type":    "None",
        "unresolved_contradiction": False,
        "afi_applicability":        applicability,
        "afi_activated":            applicability == "Yes",
        "afi_evidence_level":       0 if applicability == "Yes" else None,
        "score_justification":      [BLANK_JUSTIFICATION],
        "primary_gap":              "No response.",
        "secondary_evidence":       "",
    }


def _band(bands, value):
    chosen = None
    for band in bands:                      # ascending by lower bound
        if value >= band["lower"]:
            chosen = band
    return chosen


def headline(master, results: list) -> dict:
    """Competency, Overall, Acumen and AFI scores from all administered SRT results."""
    by_code = {}
    for r in results:
        by_code.setdefault(r["primary_competency"], []).append(r)

    competencies = []
    for c in master["competencies"]:
        items = by_code.get(c["code"], [])
        score = sum(r["final_score"] for r in items) / len(items) if items else 0.0
        competencies.append({
            "code": c["code"], "name": c["name"], "lens": c["lens"], "weight": c["weight"],
            "score": score, "srt_ids": [r["srt_id"] for r in items],
        })

    overall = sum(c["score"] for c in competencies) / len(competencies) * 10 if competencies else 0.0

    def acumen(lens):
        chosen = [c for c in competencies if c["lens"] == lens]
        total_weight = sum(c["weight"] for c in chosen)
        return sum(c["score"] * c["weight"] for c in chosen) / total_weight * 10 if total_weight else 0.0

    afi_scored = [r for r in results if r["afi_activated"]]
    afi = (100 * sum(r["afi_evidence_level"] or 0 for r in afi_scored) / (5 * len(afi_scored))) if afi_scored else None
    afi_band = _band(master["afi_bands"], afi) if afi is not None else None

    return {
        "competencies":      competencies,
        "overall":           overall,
        "technical_acumen":  acumen("Technical"),
        "business_acumen":   acumen("Business"),
        "afi":               afi,
        "afi_scored_srts":   len(afi_scored),
        "afi_band":          afi_band["label"] if afi_band else None,
    }


def near_boundary(master, overall: float) -> bool:
    proximity = master["second_review_proximity"]
    return any(abs(overall - band["lower"]) <= proximity for band in master["readiness_bands"] if band["lower"] > 0)


def review_plan(master, head, results: list, contradictions: list) -> dict:
    """SRTs that need a second review, each with the neutral reason given to the reviewer.

    The reviewer is never told the total or where it sits against a band.
    """
    plan = {}
    scored = [r for r in results if r["status"] == "scored"]
    for r in scored:
        if r["critical_failure"]:
            plan.setdefault(r["srt_id"], "A Critical Failure was recorded in the earlier evaluation.")
    for r in scored:
        if r["unresolved_contradiction"]:
            plan.setdefault(r["srt_id"], "The earlier evaluation noted an unresolved contradiction within this response.")
    ids = {r["srt_id"] for r in scored}
    for item in contradictions:
        for srt_id in item.get("srt_ids", []):
            if srt_id in ids:
                plan.setdefault(srt_id, "A possible contradiction with the candidate's other answers was noted: "
                                        + str(item.get("description", ""))[:400])
    triggers = []
    if any(r["critical_failure"] for r in scored):
        triggers.append("confirmed_critical_failure")
    if any(r["unresolved_contradiction"] for r in scored) or contradictions:
        triggers.append("material_contradiction")
    if near_boundary(master, head["overall"]):
        triggers.append("near_readiness_boundary")
        for r in scored:
            plan.setdefault(r["srt_id"], "Routine second review of this candidate's evaluations.")
    return {"triggers": triggers, "srts": plan}


def readiness(master, head, results: list, review_failures: list | None = None) -> dict:
    bands = master["readiness_bands"]
    order = [b["label"] for b in bands]
    score_band = _band(bands, head["overall"])["label"]
    final = score_band
    caps, manual_reasons = [], []

    def cap_at(label, why):
        nonlocal final
        if order.index(final) > order.index(label):
            final = label
        caps.append({"cap": label, "reason": why})

    confirmed = [r for r in results if r["critical_failure"]]
    severe = [r for r in confirmed if r["critical_failure_severity"] == "Severe"]
    integrity = [r["srt_id"] for r in severe if r["critical_failure_type"] == "Integrity"]
    technical = [r["srt_id"] for r in severe if r["critical_failure_type"] == "Technical-Safety"]
    unclassified = [r["srt_id"] for r in severe if r["critical_failure_type"] == "Unclassified"]

    if integrity:
        cap_at("Developing", "Confirmed Severe Integrity Critical Failure (" + ", ".join(integrity)
               + ") blocks Ready and High Readiness.")
    if len(technical) >= 2:
        cap_at("Ready", "Two or more confirmed Severe Technical-Safety Critical Failures ("
               + ", ".join(technical) + ") block High Readiness.")
        manual_reasons.append("Two or more confirmed Severe Technical-Safety Critical Failures require manual review.")
    if unclassified:
        manual_reasons.append(
            "Confirmed Severe Critical Failure(s) " + ", ".join(unclassified) + " cannot be assigned to a guardrail: "
            f"master {master['version']} does not classify Critical_Failure_Type. Review manually before relying "
            "on the readiness classification."
        )
    if review_failures:
        manual_reasons.append("Second review could not be completed for " + ", ".join(review_failures) + ".")

    return {
        "band":                    final,
        "score_band":              score_band,
        "caps":                    caps,
        "manual_review_required":  bool(manual_reasons),
        "manual_review_reasons":   manual_reasons,
        "provisional_note":        "Readiness bands are provisional until pilot calibration (Candidate_Output_Spec).",
    }
