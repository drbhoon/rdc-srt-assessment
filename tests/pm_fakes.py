"""Deterministic stand-ins for Claude, used by the Plant Manager tests.

Every reply is a pure function of the request, so a pipeline run produces the
same scores, report and PDF every time. That is what lets the goldens notice a
change in what the app sends to Claude or does with the replies.
"""
import hashlib
import json
import random
from types import SimpleNamespace

import httpx

CANDIDATE = {
    "candidate_name":  "Test Candidate & Sons",
    "plant_location":  "Pune <Plant 2>",
    "assessment_date": "2026-09-13",
}

# Includes None (Claude omitted the field) and 0.0 (answered wholly in Hindi).
ENGLISH_FACTORS = (1.0, 0.8, None, 0.55, 0.0)

DEFAULT = object()  # in a script: "answer this call with the default handler"


def sha256(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _pick(*parts) -> int:
    return int(sha256("|".join(str(p) for p in parts)), 16)


def message(text, stop_reason="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], stop_reason=stop_reason, usage=None)


def api_error(cls, status, headers=None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls(f"fake {status}", response=response, body=None)


class FakeClaude:
    """Stands in for anthropic.Anthropic: records each request, answers per mode.

    A handler returns a dict (sent as JSON), a raw string starting with "{", a
    ready-made message (e.g. a truncated one) or an exception to raise.
    """

    def __init__(self, score=None, review=None, report=None):
        self.handlers = {"score_one": score, "review_pass": review, "final_report": report}
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        instruction, _, body = kwargs["messages"][0]["content"].partition("\n\n")
        payload = json.loads(body)
        mode = payload["mode"]
        self.calls.append({"mode": mode, "instruction": instruction, "payload": payload, "kwargs": kwargs})
        handler = self.handlers.get(mode)
        if handler is None:
            raise AssertionError(f"unexpected Claude call in mode {mode!r}")
        reply = handler(payload)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        assert text.startswith("{"), "replies continue the app's '{' prefill"
        return message(text[1:])

    def modes(self):
        return [c["mode"] for c in self.calls]

    def summary(self):
        """Everything the app sent, in a form a golden can hold."""
        rows = []
        for call in self.calls:
            kw = call["kwargs"]
            rows.append({
                "mode":         call["mode"],
                "srt_id":       call["payload"].get("srt_id"),
                "model":        kw["model"],
                "max_tokens":   kw["max_tokens"],
                "system":       [
                    {"type": b["type"], "text_sha256": sha256(b["text"]), "cache_control": b.get("cache_control")}
                    for b in kw["system"]
                ],
                "instruction":  call["instruction"],
                "user_sha256":  sha256(kw["messages"][0]["content"]),
                "later_messages": kw["messages"][1:],
                "other_arguments": sorted(set(kw) - {"model", "max_tokens", "system", "messages"}),
            })
        return rows


# ── Handlers ─────────────────────────────────────────────────────────────────
def scorer(low, high, factors=ENGLISH_FACTORS):
    """MODE 1 replies with a total in [low, high], chosen by hashing SRT + answer."""
    def handler(p):
        n = _pick(p["srt_id"], p["candidate_transcript"])
        reply = {
            "srt_id":                p["srt_id"],
            "primary_competency":    p["primary_competency"],
            "problem_understanding": n % 3,
            "primary_depth":         n % 4,
            "secondary_awareness":   n % 2,
            "structure_clarity":     (n >> 4) % 2,
            "total":                 low + n % (high - low + 1),
            "strengths":             [f"Named the {p['primary_competency']} risk in {p['srt_id']}"],
            "improvements":          [f"Close the loop on {p['srt_id']} <with> the team & plant"],
            "english_note":          f"note for {p['srt_id']}",
        }
        factor = factors[(n >> 16) % len(factors)]
        if factor is not None:
            reply["english_proficiency"] = factor
        return reply
    return handler


def scripted(default, script):
    """Per-SRT queues of replies; DEFAULT hands a call to `default`."""
    queues = {srt: list(replies) for srt, replies in script.items()}

    def handler(p):
        queue = queues.get(p["srt_id"])
        if queue:
            reply = queue.pop(0)
            return default(p) if reply is DEFAULT else reply
        return default(p)
    return handler


def reviewer(p):
    """Proposes valid and invalid revisions, so the app's filtering is exercised."""
    items = p["items"]
    raising = p["direction"] == "lenient"
    revisions = []
    for i, item in enumerate(items):
        current = item["current_score"]
        kind = i % 5
        if kind == 0:
            new = current + 2 if raising else current - 2      # allowed direction
        elif kind == 1:
            new = current - 1 if raising else current + 1      # wrong direction
        elif kind == 2:
            new = current                                      # no change
        elif kind == 3:
            new = 15 if raising else -4                        # out of range
        else:
            continue
        revisions.append({"srt_id": item["srt_id"], "new_score": new, "reason": f"review {i}"})
    revisions.append({"srt_id": "NOT-ADMINISTERED", "new_score": 9, "reason": "unknown id"})
    revisions.append({"srt_id": items[0]["srt_id"], "new_score": "high", "reason": "not a number"})
    return {"direction": p["direction"], "revisions": revisions}


def reporter(integrity_flag=None, broken_priority=False):
    """MODE 2 reply with every section the PDF renders, and wrong numbers the app must overwrite."""
    def handler(p):
        results = p["results"]
        answered = sum(1 for r in results if (r["transcript"] or "").strip())
        competencies = sorted({r["competency"] for r in results})
        return {
            "candidate_name":              p["candidate_name"],
            "overall_score_out_of_300":    999,
            "normalized_score_out_of_100": 99.9,
            "overall_readiness":           "Ready for Higher Responsibility",
            "competency_summary":          {c: 10 for c in competencies},
            "competency_narratives":       {c: f"Narrative for {c} & <evidence>." for c in competencies},
            "behavioral_profile": {
                "communication_style":      "Direct",
                "decision_making_approach": "Data first",
                "leadership_orientation":   "Hands-on",
                "stress_response_pattern":  "Calm",
                "accountability_stance":    "Owns outcomes",
            },
            "top_strengths": [
                {"strength": "Safety first", "evidence": f"answered {answered} of {len(results)}",
                 "rmc_relevance": "Plant uptime"},
                "Plain-string strength",
            ],
            "development_areas": [
                {"area": "Delegation", "evidence": "did every task personally", "rmc_context": "Shift handover",
                 "priority": None if broken_priority else "high"},
                "Plain-string area",
            ],
            "cross_competency_insights": [{"pattern": "P", "evidence": "E", "implication": "I"}],
            "development_actions":       ["Action one", "Action two"],
            "coaching_plan_30_60_90":    {"30_days": ["a"], "60_days": ["b"], "90_days": ["c"]},
            "integrity_red_flag":        integrity_flag or {"present": False},
        }
    return handler


# ── Candidate data ───────────────────────────────────────────────────────────
def questions(seed):
    import main
    from question_bank import get_session_questions

    random.seed(seed)
    return get_session_questions(main.questions_db, per_competency=3)


def answers(qs, blank=(3, 17), whitespace=(22,), missing=(25,)):
    """Hinglish answers with markup characters; some blank, some never sent."""
    out = {}
    for i, q in enumerate(qs):
        if i in missing:
            continue
        if i in blank:
            out[q["srt_id"]] = ""
        elif i in whitespace:
            out[q["srt_id"]] = "   \n "
        else:
            out[q["srt_id"]] = (
                f"Q{i + 1}: पहले safety check करूँगा, then batch records & <slump> test, "
                f"call the site engineer.\nFollow up next shift."
            )
    return out
