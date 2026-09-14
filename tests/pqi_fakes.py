"""Deterministic stand-in for Claude's PQI tool calls.

Every PQI call forces one tool. The fake answers each with a tool_use block
whose input is a pure function of the request, so tests can dictate exactly
what the evaluator "observed" and check what the application does with it.
"""
import hashlib
import json
from types import SimpleNamespace

MASTER_FILE = "RDC_PQI_Master_100_AI_Scoring_v1.0_PILOT.xlsx"


def _pick(*parts) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest(), 16)


def tool_message(name, data, stop_reason="tool_use"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="toolu_fake", name=name, input=data)],
        stop_reason=stop_reason, usage=None,
    )


class FakePqiClaude:
    def __init__(self, evaluate=None, review=None, consistency=None, report=None):
        self.handlers = {
            "evaluate": evaluate or evaluator(),
            "review": review or evaluate or evaluator(),
            "consistency": consistency or (lambda p: {"contradictions": []}),
            "report": report or reporter,
        }
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        tool = kwargs["tools"][0]["name"]
        assert kwargs["tool_choice"] == {"type": "tool", "name": tool}
        instruction, _, body = kwargs["messages"][0]["content"].partition("\n\n")
        payload = json.loads(body)
        if tool == "record_srt_evaluation":
            mode = "review" if "Earlier_Evaluation" in payload else "evaluate"
        elif tool == "record_consistency_check":
            mode = "consistency"
        else:
            mode = "report"
        self.calls.append({"mode": mode, "instruction": instruction, "payload": payload, "kwargs": kwargs})
        reply = self.handlers[mode](payload)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        return tool_message(tool, reply)

    def modes(self):
        return [c["mode"] for c in self.calls]

    def srt_ids(self, mode):
        return [c["payload"]["SRT"]["SRT_ID"] for c in self.calls if c["mode"] == mode]


def evaluation(payload, core=5, lift=0, decision="Yes", critical="No", reason="", contradiction=False,
               afi_activated="Yes", afi_level=3, final=None):
    srt = payload["SRT"]
    applicability = srt["Anti_Firefighting_Applicability"]
    if applicability == "Yes":
        activated, level = "Yes", afi_level
    elif applicability == "Conditional":
        activated = afi_activated
        level = afi_level if activated == "Yes" else None
    else:
        activated, level = "Not Applicable", None
    return {
        "srt_id": srt["SRT_ID"],
        "core_response_level": core,
        "excellence_lift": lift,
        "decision_made": decision if srt["Decision_Applicable"] == "Yes" else "Not Applicable",
        "critical_failure": critical,
        "critical_failure_reason": reason,
        "unresolved_contradiction": contradiction,
        "afi_activated": activated,
        "afi_evidence_level": level,
        "final_srt_score": core + lift if final is None else final,
        "score_justification": [f"Evidence for {srt['SRT_ID']}", "Second statement"],
        "primary_gap": f"Gap for {srt['SRT_ID']}",
        "secondary_evidence": "",
    }


def evaluator(rule=None, **fixed):
    """Deterministic evaluations; `fixed` pins fields for every SRT, `rule(srt_id, payload)` per SRT."""
    def handler(payload):
        srt_id = payload["SRT"]["SRT_ID"]
        n = _pick(srt_id, payload["Candidate"]["Candidate_Response"])
        fields = {"core": 3 + n % 5, "lift": n % 3, "afi_level": (n >> 5) % 6,
                  "decision": "No" if (n >> 7) % 5 == 0 else "Yes",
                  "afi_activated": "Yes" if (n >> 9) % 2 else "No"}
        fields.update(fixed)
        if rule:
            extra = rule(srt_id, payload)
            if isinstance(extra, (BaseException, SimpleNamespace)):
                return extra
            fields.update(extra or {})
        return evaluation(payload, **fields)
    return handler


def reporter(payload):
    ids = [e["SRT_ID"] for e in payload["SRT_Evaluations"]]
    return {
        "top_strengths": [
            {"title": "Evidence-led quality decisions", "evidence": "Held material pending tests.", "srt_ids": ids[:2]},
            {"title": "Clear escalation", "evidence": "Informed sales & <customer>.", "srt_ids": ids[2:3]},
        ],
        "development_priorities": [
            {"title": "Close the prevention loop", "gap_type": "prevention", "evidence": "Rarely monitored recurrence.",
             "srt_ids": ids[3:4]},
            {"title": "Delegate testing", "gap_type": "delegation", "evidence": "Did every check personally.",
             "srt_ids": ["NOT-AN-SRT"]},
        ],
        "development_narrative": "The candidate protects quality evidence.\nNext: build the team's capability.",
    }
