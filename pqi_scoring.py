"""PQI scoring rules the application applies itself — deterministic, no AI involved.

The evaluator reports what it saw (core level, lift, whether a decision was
made, whether a critical failure was advocated, AFI evidence). Everything that
turns those observations into numbers is here: the Excellence Lift rule, the
strict Decision and Critical Failure caps, AFI activation, competency, Overall,
Acumen and AFI scores, readiness bands, guardrails and second-review triggers.
All settings come from the master.
"""
from __future__ import annotations

import re

import pqi_rubric_v2 as v2

RUBRIC_V1 = "SRT_Scoring_Rubric_v1"
RUBRIC_V2 = v2.RUBRIC_ID

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


def _words(text) -> list:
    """Lower-case words, any script: punctuation, quotes and spacing don't matter."""
    return re.findall(r"\w+", str(text or "").lower())


def statement_in_response(statement: str, response: str) -> bool:
    """Are the words the evaluator quoted as a Critical Failure really the candidate's?

    A Critical Failure caps the SRT at 2 and can move readiness, so it must rest
    on what the candidate wrote, not on a paraphrase. The quote passes when it
    appears in the response once punctuation and spacing are ignored, or — for
    a quote of three or more words, to allow for a slip in copying a long or
    dictated answer — when at least 85% of its words are in the response.
    """
    quoted, said = _words(statement), _words(response)
    if not quoted:
        return False
    if " ".join(quoted) in " ".join(said):
        return True
    if len(quoted) < 3:
        return False
    present = set(said)
    return sum(1 for w in quoted if w in present) / len(quoted) >= 0.85


def apply_rules_v2(master, srt, ev: dict, response_text: str = "") -> dict:
    """One SRT's final result under Rubric v2, from a validated evaluator observation.

    The evaluator gives one 0–9 level. The application applies the decision cap
    and, for a Critical Failure whose quoted words are really in the response,
    the Critical Failure cap. A Critical Failure the quote does not support is
    NOT confirmed: the level stands, and the SRT goes to second review.
    """
    level = ev["raw_level"]
    score = level
    decision_applicable = srt["decision_applicable"] == "Yes"
    decision_made = ev["decision_made"] if decision_applicable else "Not Applicable"
    decision_cap_triggered = decision_applicable and decision_made == "No"
    caps = []
    if decision_cap_triggered and score > master["decision_cap"]:
        score = master["decision_cap"]
        caps.append("decision")

    claimed = bool(ev["critical_failure"])
    verified = claimed and statement_in_response(ev.get("critical_failure_statement", ""), response_text)
    if verified and score > master["critical_failure_cap"]:
        score = master["critical_failure_cap"]
        caps.append("critical_failure")
    severity, failure_type = _severity_and_type(srt, verified)

    applicability = srt["afi_applicability"]
    if applicability == "Yes":
        afi_activated, afi_level = True, ev["afi_evidence_level"]
    elif applicability == "Conditional" and ev["afi_activated"] == "Yes":
        afi_activated, afi_level = True, ev["afi_evidence_level"]
    else:
        afi_activated, afi_level = False, None

    evidence = list(ev.get("key_evidence") or [])
    return {
        "srt_id":                   srt["srt_id"],
        "primary_competency":       srt["primary_competency"],
        "status":                   "scored",
        "rubric":                   RUBRIC_V2,
        "raw_level":                level,
        "final_score":              score,
        "calibrated_percentage":    level_percent(score),
        "caps_applied":             caps,
        "decision_applicable":      decision_applicable,
        "decision_made":            decision_made,
        "decision_cap_triggered":   decision_cap_triggered,
        "essential_judgment":       ev.get("essential_judgment"),
        "key_evidence":             evidence,
        "material_gap":             ev.get("material_gap", ""),
        "low_score_basis":          ev.get("low_score_basis"),
        "serious_negative":         bool(ev.get("serious_negative")),
        "critical_failure":         verified,
        "critical_failure_statement": ev.get("critical_failure_statement", "") if verified else "",
        "critical_failure_reason":  ev.get("critical_failure_reason", "") if verified else "",
        "critical_failure_severity": severity,
        "critical_failure_type":    failure_type,
        # A Critical Failure the evaluator claimed but its quote did not support.
        "critical_failure_unverified": claimed and not verified,
        "critical_failure_claim":   ({"statement": ev.get("critical_failure_statement", ""),
                                      "reason": ev.get("critical_failure_reason", "")}
                                     if claimed and not verified else None),
        "unresolved_contradiction": bool(ev.get("unresolved_contradiction")),
        "afi_applicability":        applicability,
        "afi_activated":            afi_activated,
        "afi_evidence_level":       afi_level,
        "afi_evidence":             ev.get("afi_evidence", ""),
        "evaluation_confidence":    ev.get("evaluation_confidence"),
        # The names every report, console and narrative already read.
        "score_justification":      evidence,
        "primary_gap":              ev.get("material_gap", ""),
    }


def blank_result(master, srt, rubric: str = RUBRIC_V1) -> dict:
    """A blank or missing answer: score 0 without calling the evaluator.

    A blank AFI=Yes SRT scores AFI 0 and stays in the denominator; a blank
    Conditional SRT does not activate AFI (locked decision, 13 Sep 2026).
    """
    applicability = srt["afi_applicability"]
    decision_applicable = srt["decision_applicable"] == "Yes"
    if rubric == RUBRIC_V2:
        return {
            "srt_id":                   srt["srt_id"],
            "primary_competency":       srt["primary_competency"],
            "status":                   "blank",
            "rubric":                   RUBRIC_V2,
            "raw_level":                0,
            "final_score":              0,
            "calibrated_percentage":    level_percent(0),
            "caps_applied":             [],
            "decision_applicable":      decision_applicable,
            "decision_made":            "No" if decision_applicable else "Not Applicable",
            "decision_cap_triggered":   decision_applicable,
            "essential_judgment":       "No",
            "key_evidence":             [BLANK_JUSTIFICATION],
            "material_gap":             "No response.",
            "low_score_basis":          "No usable answer",
            "serious_negative":         False,
            "critical_failure":         False,
            "critical_failure_statement": "",
            "critical_failure_reason":  "",
            "critical_failure_severity": "None",
            "critical_failure_type":    "None",
            "critical_failure_unverified": False,
            "critical_failure_claim":   None,
            "unresolved_contradiction": False,
            "afi_applicability":        applicability,
            "afi_activated":            applicability == "Yes",
            "afi_evidence_level":       0 if applicability == "Yes" else None,
            "afi_evidence":             "",
            "evaluation_confidence":    "High",
            "score_justification":      [BLANK_JUSTIFICATION],
            "primary_gap":              "No response.",
        }
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


def basis(master, rubric: str, results: list, head: dict | None = None) -> dict:
    """The overall figure and the bands that readiness and the second review use.

    Rubric v1 decides on the master's own scale (level x 10) with the master's
    bands, exactly as before. Rubric v2 decides on scale C1 — the figure the
    report prints — with HR's pilot bands.
    """
    if rubric == RUBRIC_V2:
        return {"overall": reported_headline(master, results)["overall"], "bands": v2.PILOT_BANDS,
                "label": v2.PILOT_BANDS_LABEL, "note": v2.PILOT_NOTE, "scale": CURRENT_SCALE}
    head = head or headline(master, results)
    return {"overall": head["overall"], "bands": master["readiness_bands"],
            "label": "Master readiness bands on the native scale (level x 10)",
            "note": "Readiness bands are provisional until pilot calibration (Candidate_Output_Spec).",
            "scale": "native"}


def near_boundary(master, overall: float, bands: list | None = None) -> bool:
    proximity = master["second_review_proximity"]
    bands = bands if bands is not None else master["readiness_bands"]
    return any(abs(overall - band["lower"]) <= proximity for band in bands if band["lower"] > 0)


def review_plan(master, head, results: list, contradictions: list, decided: dict | None = None) -> dict:
    """SRTs that need a second review, each with the neutral reason given to the reviewer.

    The reviewer is never told the total or where it sits against a band.
    `decided` is basis(); without it, the v1 native figure and master bands.
    """
    plan = {}
    scored = [r for r in results if r["status"] == "scored"]
    for r in scored:
        if r["critical_failure"]:
            plan.setdefault(r["srt_id"], "A Critical Failure was recorded in the earlier evaluation.")
    for r in scored:
        if r.get("critical_failure_unverified"):
            plan.setdefault(r["srt_id"], "The earlier evaluation recorded a possible Critical Failure, but the "
                                         "words it quoted could not be found in the candidate's response.")
    for r in scored:
        if r["unresolved_contradiction"]:
            plan.setdefault(r["srt_id"], "The earlier evaluation noted an unresolved contradiction within this response.")
    for r in scored:
        if r.get("evaluation_confidence") == "Low":
            plan.setdefault(r["srt_id"], "The earlier evaluation recorded low confidence in its reading of this "
                                         "response.")
    ids = {r["srt_id"] for r in scored}
    for item in contradictions:
        for srt_id in item.get("srt_ids", []):
            if srt_id in ids:
                plan.setdefault(srt_id, "A possible contradiction with the candidate's other answers was noted: "
                                        + str(item.get("description", ""))[:400])
    triggers = []
    if any(r["critical_failure"] for r in scored):
        triggers.append("confirmed_critical_failure")
    if any(r.get("critical_failure_unverified") for r in scored):
        triggers.append("unverified_critical_failure")
    if any(r["unresolved_contradiction"] for r in scored) or contradictions:
        triggers.append("material_contradiction")
    if any(r.get("evaluation_confidence") == "Low" for r in scored):
        triggers.append("low_confidence")
    overall = decided["overall"] if decided else head["overall"]
    if near_boundary(master, overall, decided["bands"] if decided else None):
        triggers.append("near_readiness_boundary")
        for r in scored:
            plan.setdefault(r["srt_id"], "Routine second review of this candidate's evaluations.")
    return {"triggers": triggers, "srts": plan}


def readiness(master, head, results: list, review_failures: list | None = None, decided: dict | None = None) -> dict:
    bands = decided["bands"] if decided else master["readiness_bands"]
    order = [b["label"] for b in bands]
    score_band = _band(bands, decided["overall"] if decided else head["overall"])["label"]
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
    unverified = [r["srt_id"] for r in results if r.get("critical_failure_unverified")]
    if unverified:
        manual_reasons.append(
            "A possible Critical Failure in " + ", ".join(unverified) + " was not confirmed: the evaluator's quote "
            "could not be found in the response, so no cap was applied. Read the answer before relying on the result.")

    out = {
        "band":                    final,
        "score_band":              score_band,
        "caps":                    caps,
        "manual_review_required":  bool(manual_reasons),
        "manual_review_reasons":   manual_reasons,
        "provisional_note":        decided["note"] if decided else
                                   "Readiness bands are provisional until pilot calibration (Candidate_Output_Spec).",
    }
    if decided:
        out.update(basis=decided["label"], decided_on=round(decided["overall"], 1),
                   bands=[{k: b[k] for k in ("lower", "upper", "label")} for b in bands])
    return out


def completion(master, results: list, name: str = None) -> dict:
    """How much of the assessment was answered, and how the answers that were given scored.

    The official result counts a blank as zero and that does not change. This
    separates "low demonstrated competence" from "incomplete assessment": the
    answered-question figure is the same C1 calculation over answered SRTs only
    (competencies with no answer left out). Diagnostic — never the score.
    """
    name = name or CURRENT_SCALE
    answered = [r for r in results if r["status"] == "scored"]
    presented = len(results)
    by_code = {}
    for r in answered:
        by_code.setdefault(r["primary_competency"], []).append(level_percent(r["final_score"], name))
    means = [sum(v) / len(v) for v in by_code.values()]
    return {
        "questions_presented":  presented,
        "questions_answered":   len(answered),
        "blank_count":          presented - len(answered),
        "completion_percent":   round(100 * len(answered) / presented, 1) if presented else 0.0,
        "answered_only_overall": round(sum(means) / len(means), 1) if means else None,
        "answered_only_note":   "Diagnostic only: the same calculation over answered questions. The official score "
                                "counts unanswered questions as zero.",
    }


# ─── Reported score scale ────────────────────────────────────────────────────
# What a level is worth as a percentage in REPORTS. HR adopted scale C1 on
# 2026-09-27 after the seven-candidate pilot: under level x 10 a level-6 answer
# ("solid, sound, missing only the finishing touches") read as 60%, so the
# strongest manager in the pilot scored 58-64 while HR rated him around 80.
#
# Only the reported percentages use this. The master's own rules — the second
# review near a readiness boundary and the readiness bands themselves — keep
# working on the master's native scale (headline() above, level x 10), so no
# evaluation and no readiness decision changes with the scale. The bands are
# to be recalibrated on the new scale once the next six pilot results are in.
#
# A new scale is a new entry here, never an edit to an existing one: every
# report records the scale it was issued under, so old reports stay
# explainable.
SCORE_SCALES = {
    "C1": {
        "label": "Calibrated scale C1 (HR, 2026-09-27)",
        "points": [0, 20, 35, 50, 60, 70, 80, 88, 94, 100],
    },
}
CURRENT_SCALE = "C1"


def scale_info(name: str = CURRENT_SCALE) -> dict:
    scale = SCORE_SCALES[name]
    return {"name": name, "label": scale["label"], "points": list(scale["points"]), "max": scale["points"][-1]}


def level_percent(level, name: str = CURRENT_SCALE) -> float:
    """A 0-9 SRT level as a percentage on the named scale."""
    points = SCORE_SCALES[name]["points"]
    return float(points[max(0, min(len(points) - 1, int(level or 0)))])


def reported_headline(master, results: list, name: str = CURRENT_SCALE) -> dict:
    """The numbers a report shows: each SRT level converted on the scale, then
    averaged exactly as headline() averages levels — competency = mean of its
    SRTs, overall = mean of the competencies, acumen = lens-weighted mean.

    Competency scores are percentages here (0-100), not levels out of 10.
    AFI is scored separately and is not affected by the scale.
    """
    by_code = {}
    for r in results:
        by_code.setdefault(r["primary_competency"], []).append(level_percent(r["final_score"], name))

    competencies = []
    for c in master["competencies"]:
        values = by_code.get(c["code"], [])
        competencies.append({"code": c["code"], "lens": c["lens"], "weight": c["weight"],
                             "score": sum(values) / len(values) if values else 0.0})
    overall = sum(c["score"] for c in competencies) / len(competencies) if competencies else 0.0

    def acumen(lens):
        chosen = [c for c in competencies if c["lens"] == lens]
        total_weight = sum(c["weight"] for c in chosen)
        return sum(c["score"] * c["weight"] for c in chosen) / total_weight if total_weight else 0.0

    return {
        "scale":            scale_info(name),
        "competencies":     {c["code"]: c["score"] for c in competencies},
        "overall":          overall,
        "technical_acumen": acumen("Technical"),
        "business_acumen":  acumen("Business"),
    }
