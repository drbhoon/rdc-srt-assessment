"""What Plant Manager sends to Claude, and how it handles each kind of reply."""
import json
from types import SimpleNamespace

import anthropic
import pytest

import pm_fakes as fk
import report_generator
import scorer
from conftest import ROOT

PM_SKILL = ROOT / "skill" / "RDC-Plant-Incharge-SRT-Assessment.md"
PM_SKILL_SHA256 = "b4c646fbe780e6dce86e152087d97add935c371169595390ce91b4acdba6e01a"

SCORE_INSTRUCTION = (
    "Score this SRT response. Respond with ONLY a valid JSON object — no prose, no markdown fences. "
    "Start with '{' and end with '}'."
)

QUESTION = dict(
    srt_id="SRT-X", situation="A transit mixer is waiting & the slump is <low>.",
    primary_competency="Integrity & Trust", secondary_competency="Cost & Resource Responsibility",
)


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    fake_time = SimpleNamespace(sleep=recorded.append)
    monkeypatch.setattr(scorer, "time", fake_time)
    monkeypatch.setattr(report_generator, "time", fake_time)
    return recorded


def valid_score(p):
    return {"srt_id": p["srt_id"], "total": "6", "strengths": ["s"], "improvements": ["i"], "english_proficiency": 0.8}


def test_pm_skill_prompt_is_the_deployed_file():
    assert fk.sha256(PM_SKILL.read_bytes()) == PM_SKILL_SHA256
    assert scorer.get_system_prompt() == report_generator.get_system_prompt() == PM_SKILL.read_text(encoding="utf-8")


@pytest.mark.parametrize("transcript", ["", "  \n "])
def test_blank_answer_scores_zero_without_calling_claude(transcript):
    fake = fk.FakeClaude()
    result = scorer.score_question(client=fake, candidate_transcript=transcript, **QUESTION)
    assert fake.calls == []
    assert result == {
        "srt_id": "SRT-X", "primary_competency": "Integrity & Trust",
        "problem_understanding": 0, "primary_depth": 0, "secondary_awareness": 0, "structure_clarity": 0,
        "total": 0, "strengths": [], "improvements": ["Question not answered — counted as zero."],
    }


def test_score_request_shape(sleeps):
    fake = fk.FakeClaude(score=valid_score)
    result = scorer.score_question(client=fake, candidate_transcript="मैं check करूँगा", **QUESTION)

    assert result["total"] == 6  # string total coerced to int
    (call,) = fake.calls
    kw = call["kwargs"]
    assert set(kw) == {"model", "max_tokens", "system", "messages"}
    assert kw["model"] == "claude-haiku-4-5" and kw["max_tokens"] == 2048
    assert kw["system"] == [{"type": "text", "text": PM_SKILL.read_text(encoding="utf-8"),
                             "cache_control": {"type": "ephemeral"}}]
    assert call["instruction"] == SCORE_INSTRUCTION
    assert kw["messages"][0]["content"] == SCORE_INSTRUCTION + "\n\n" + json.dumps({
        "mode": "score_one", "srt_id": "SRT-X", "situation": QUESTION["situation"],
        "primary_competency": "Integrity & Trust", "secondary_competency": "Cost & Resource Responsibility",
        "candidate_transcript": "मैं check करूँगा",
    }, indent=2, ensure_ascii=False)
    assert kw["messages"][1] == {"role": "assistant", "content": "{"}
    assert sleeps == []


def run_script(replies):
    fake = fk.FakeClaude(score=fk.scripted(valid_score, {"SRT-X": replies}))
    result = scorer.score_question(client=fake, candidate_transcript="an answer", **QUESTION)
    return result, fake


def test_invalid_json_is_retried(sleeps):
    result, fake = run_script(["{not json", fk.DEFAULT])
    assert result["total"] == 6 and len(fake.calls) == 2 and sleeps == [1]


def test_scoring_gives_up_after_three_bad_replies(sleeps):
    result, fake = run_script(["{bad", "{bad", "{bad"])
    assert len(fake.calls) == 3 and sleeps == [1, 2]
    assert result["total"] == 0 and result["strengths"] == []
    assert result["improvements"][0].startswith("Scoring error after 3 attempt(s) [JSONDecodeError]: ")


def test_truncated_reply_is_retried(sleeps):
    truncated = fk.message('"total": 5', stop_reason="max_tokens")
    result, fake = run_script([truncated, truncated, truncated])
    assert result["improvements"][0].startswith("Scoring error after 3 attempt(s) [TruncatedResponseError]: ")


def test_rate_limit_honours_retry_after(sleeps):
    limited = fk.api_error(anthropic.RateLimitError, 429, {"retry-after": "7"})
    result, fake = run_script([limited, fk.DEFAULT])
    assert result["total"] == 6 and sleeps == [7]


def test_bad_request_is_not_retried(sleeps):
    result, fake = run_script([fk.api_error(anthropic.BadRequestError, 400)])
    assert len(fake.calls) == 1 and sleeps == []
    assert result["improvements"] == ["Scoring error after 1 attempt(s) [BadRequestError]: fake 400"]


def test_unexpected_error_is_not_retried(sleeps):
    result, fake = run_script([RuntimeError("boom")])
    assert len(fake.calls) == 1
    assert result["improvements"] == ["Scoring error after 1 attempt(s) [RuntimeError]: boom"]


# ── Double-pass review ───────────────────────────────────────────────────────
ITEMS = [
    {"srt_id": f"S{i}", "competency": "C", "situation": "s", "transcript": "t", "current_score": score}
    for i, score in enumerate([2, 3, 4, 5, 6, 7])
]


def test_review_lenient_only_raises(sleeps):
    fake = fk.FakeClaude(review=fk.reviewer)
    revisions = scorer.review_pass(client=fake, items=ITEMS, preliminary_total=80.44, direction="lenient")
    # S0 +2, S1 lower (dropped), S2 same (dropped), S3 15 → clamped 9, S4 not revised,
    # S5 +2; unknown id and non-numeric score dropped.
    assert revisions == {"S0": 4, "S3": 9, "S5": 9}
    (call,) = fake.calls
    assert call["kwargs"]["max_tokens"] == 3000
    assert call["payload"]["preliminary_total"] == 80.4
    assert call["instruction"].startswith("Run the MODE 1.5 review_pass.")


def test_review_strict_only_lowers(sleeps):
    fake = fk.FakeClaude(review=fk.reviewer)
    revisions = scorer.review_pass(client=fake, items=ITEMS, preliminary_total=260, direction="strict")
    assert revisions == {"S0": 0, "S3": 0, "S5": 5}


def test_review_is_never_a_hard_dependency(sleeps):
    assert scorer.review_pass(client=fk.FakeClaude(), items=[], preliminary_total=50, direction="lenient") == {}
    assert scorer.review_pass(client=fk.FakeClaude(), items=ITEMS, preliminary_total=50, direction="sideways") == {}
    broken = fk.FakeClaude(review=lambda p: "{nope")
    assert scorer.review_pass(client=broken, items=ITEMS, preliminary_total=50, direction="lenient") == {}
    assert len(broken.calls) == 3


# ── Final report ─────────────────────────────────────────────────────────────
def test_report_request_shape(sleeps):
    fake = fk.FakeClaude(report=lambda p: {"ok": True})
    results = [{"competency": "C", "transcript": "t", "score": 5}]
    assert report_generator.generate_final_report(
        client=fake, candidate_name="N", plant_location="P", assessment_date="D", results=results,
    ) == {"ok": True}
    (call,) = fake.calls
    kw = call["kwargs"]
    assert kw["model"] == "claude-haiku-4-5" and kw["max_tokens"] == 16000
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["instruction"].startswith("Generate the final assessment report. All 30 candidate transcripts")
    assert call["payload"] == {"mode": "final_report", "candidate_name": "N", "plant_location": "P",
                               "assessment_date": "D", "results": results}
    assert kw["messages"][1] == {"role": "assistant", "content": "{"}


def test_report_strips_trailing_fence(sleeps):
    fake = fk.FakeClaude(report=lambda p: '{"a": 1}\n```')
    assert report_generator.generate_final_report(fake, "N", "P", "D", []) == {"a": 1}


def test_report_failure_propagates_after_retries(sleeps):
    fake = fk.FakeClaude(report=lambda p: "{broken")
    with pytest.raises(json.JSONDecodeError):
        report_generator.generate_final_report(fake, "N", "P", "D", [])
    assert len(fake.calls) == 3 and sleeps == [2, 5]


def test_report_unexpected_error_propagates_at_once(sleeps):
    fake = fk.FakeClaude(report=lambda p: RuntimeError("down"))
    with pytest.raises(RuntimeError, match="down"):
        report_generator.generate_final_report(fake, "N", "P", "D", [])
    assert len(fake.calls) == 1


def test_json_extraction():
    assert scorer._extract_json('```json\n{"a": {"b": 1}}\n```') == '{"a": {"b": 1}}'
    assert scorer._extract_json('noise {"a": {"b": 1}} trailing }') == '{"a": {"b": 1}}'
    assert scorer._extract_json("no braces") == "no braces"
    assert scorer._extract_json('{"open": ') == '{"open": '
