"""Strict validation of what Claude returns for PQI, before any of it is scored."""
import pytest

from pqi_evaluator import ReplyFormatError, parse_consistency, parse_evaluation, parse_report
from test_pqi_master import master  # noqa: F401


def srt(master, srt_id, **changes):
    record = dict(next(s for s in master["srts"] if s["srt_id"] == srt_id))
    record.update(changes)
    return record


def reply(**changes):
    base = {"srt_id": "C1-01", "core_response_level": 6, "excellence_lift": 0, "decision_made": "Yes",
            "critical_failure": "No", "critical_failure_reason": "", "unresolved_contradiction": False,
            "afi_activated": "Yes", "afi_evidence_level": 3, "final_srt_score": 6,
            "score_justification": ["Refused the inducement."], "primary_gap": "No prevention step."}
    base.update(changes)
    return base


def test_a_valid_evaluation_is_normalised(master):
    parsed = parse_evaluation(srt(master, "C1-01"), reply(decision_made="yes", core_response_level="6"))
    assert (parsed["decision_made"], parsed["core_response_level"], parsed["critical_failure"]) == ("Yes", 6, False)


@pytest.mark.parametrize("changes,message", [
    ({"srt_id": "C1-02"}, "does not match"),
    ({"core_response_level": 8}, "core_response_level must be 0–7"),
    ({"core_response_level": 6.5}, "whole number"),
    ({"excellence_lift": 3}, "excellence_lift must be 0–2"),
    ({"decision_made": "Maybe"}, "decision_made must be one of"),
    ({"decision_made": "Not Applicable"}, "requires a decision"),
    ({"afi_evidence_level": None}, "afi_evidence_level is required"),
    ({"afi_evidence_level": 6}, "afi_evidence_level must be 0–5"),
    ({"score_justification": []}, "score_justification"),
])
def test_malformed_evaluations_are_rejected(master, changes, message):
    with pytest.raises(ReplyFormatError, match=message):
        parse_evaluation(srt(master, "C1-01", decision_applicable="Yes", afi_applicability="Yes"), reply(**changes))


def test_conditional_afi_needs_a_level_only_when_activated(master):
    conditional = srt(master, "C1-01", afi_applicability="Conditional")
    assert parse_evaluation(conditional, reply(afi_activated="No", afi_evidence_level=None))["afi_evidence_level"] is None
    with pytest.raises(ReplyFormatError):
        parse_evaluation(conditional, reply(afi_activated="Yes", afi_evidence_level=None))


def test_a_contradiction_needs_two_administered_srts():
    valid = {"C1-01", "C4-03", "C9-05"}
    parsed = parse_consistency(valid, {"contradictions": [
        {"srt_ids": ["C1-08"], "description": "template answer reused"},            # one SRT, not administered
        {"srt_ids": ["C1-01"], "description": "only one SRT"},                      # one SRT
        {"srt_ids": ["C1-01", "C1-01"], "description": "same SRT twice"},           # still one SRT
        {"srt_ids": ["C1-01", "NOPE"], "description": "second id unknown"},         # one valid SRT
        {"srt_ids": ["C1-01", "C4-03"], "description": ""},                         # no description
        {"srt_ids": ["C4-03", "C9-05"], "description": "Refuses water addition, then allows it"},
    ]})
    assert parsed == [{"srt_ids": ["C4-03", "C9-05"], "description": "Refuses water addition, then allows it"}]
    with pytest.raises(ReplyFormatError):
        parse_consistency(valid, {"contradictions": "none"})


def test_report_is_trimmed_to_the_spec():
    items = [{"title": f"T{i}", "evidence": "e", "srt_ids": ["C1-01", "X"], "gap_type": "judgment"} for i in range(5)]
    parsed = parse_report({"C1-01"}, {"top_strengths": items, "development_priorities": items,
                                      "development_narrative": " Evidence-based narrative. "})
    assert len(parsed["top_strengths"]) == 3 and len(parsed["development_priorities"]) == 3
    assert parsed["top_strengths"][0]["srt_ids"] == ["C1-01"]
    assert parsed["development_narrative"] == "Evidence-based narrative."
    with pytest.raises(ReplyFormatError):
        parse_report({"C1-01"}, {"top_strengths": [], "development_priorities": [], "development_narrative": ""})
