"""The Plant Manager scoring pipeline, end to end, with Claude replaced by a fake.

Each scenario locks the full outcome — every per-SRT score, the stored report,
the PDF bytes and every request sent to Claude — against a golden.
"""
import asyncio
from types import SimpleNamespace

import anthropic
import pytest

import database
import main
import pm_fakes as fk
import report_generator
import scorer

SESSION_ID = "pm-characterisation-session"


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    fake_time = SimpleNamespace(sleep=recorded.append)
    monkeypatch.setattr(scorer, "time", fake_time)
    monkeypatch.setattr(report_generator, "time", fake_time)
    return recorded


def run(monkeypatch, fake, qs, answers, prior_scores=None):
    monkeypatch.setattr(main, "client", fake)
    database.create_session(SESSION_ID, dict(fk.CANDIDATE), qs)
    fields = {"collected_answers": answers, "status": "processing", "progress": 0}
    if prior_scores is not None:
        fields["scores"] = prior_scores
    database.update_session(SESSION_ID, **fields)
    main._cache.clear()
    asyncio.run(main.process_assessment_async(SESSION_ID))
    main._cache.clear()
    return database.get_session(SESSION_ID)


def outcome(session, fake, sleeps):
    pdf = session.get("pdf_bytes")
    return {
        "status":       session.get("status"),
        "progress":     session.get("progress"),
        "error":        session.get("error"),
        "pdf_error":    session.get("pdf_error"),
        "scores":       session.get("scores"),
        "report":       session.get("report"),
        "pdf_sha256":   fk.sha256(pdf) if pdf else None,
        "claude_calls": fake.summary(),
        "retry_sleeps": sleeps,
    }


def test_mid_range_candidate(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=101)
    fake = fk.FakeClaude(score=fk.scorer(5, 8), report=fk.reporter())
    session = run(monkeypatch, fake, qs, fk.answers(qs))

    assert session["status"] == "completed"
    assert fake.modes().count("score_one") == 26          # 4 blank or missing answers never reach Claude
    assert "review_pass" not in fake.modes()
    total = session["report"]["overall_score_out_of_300"]
    assert 100 <= total <= 250
    assert session["pdf_bytes"].startswith(b"%PDF")
    golden("pm_pipeline_mid_range", outcome(session, fake, sleeps))


def test_low_total_triggers_lenient_review(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=202)
    fake = fk.FakeClaude(score=fk.scorer(0, 2), review=fk.reviewer, report=fk.reporter())
    session = run(monkeypatch, fake, qs, fk.answers(qs))

    assert fake.modes().count("review_pass") == 1
    assert session["status"] == "completed"
    golden("pm_pipeline_low_lenient_review", outcome(session, fake, sleeps))


def test_high_total_triggers_strict_review(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=303)
    fake = fk.FakeClaude(score=fk.scorer(9, 10, factors=(1.0, None)), review=fk.reviewer, report=fk.reporter())
    session = run(monkeypatch, fake, qs, fk.answers(qs, blank=(), whitespace=(), missing=()))

    assert fake.modes().count("review_pass") == 1
    golden("pm_pipeline_high_strict_review", outcome(session, fake, sleeps))


def test_integrity_red_flag_caps_readiness(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=404)
    flag = {"present": True, "evidence": "Agreed to 'adjust' the challan & stay quiet"}
    fake = fk.FakeClaude(score=fk.scorer(8, 8, factors=(1.0,)), report=fk.reporter(integrity_flag=flag))
    session = run(monkeypatch, fake, qs, fk.answers(qs, blank=(), whitespace=(), missing=()))

    report = session["report"]
    assert report["overall_score_out_of_300"] == 240.0
    assert report["overall_readiness"] == "Ready with Structured Support"
    assert report["readiness_demoted_from"] == "Ready to be Plant Manager"
    golden("pm_pipeline_integrity_override", outcome(session, fake, sleeps))


def test_nothing_answered(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=505)
    fake = fk.FakeClaude(report=fk.reporter())
    session = run(monkeypatch, fake, qs, {})

    assert fake.modes() == ["final_report"]              # review had no answered items to send
    assert session["report"]["overall_score_out_of_300"] == 0
    golden("pm_pipeline_nothing_answered", outcome(session, fake, sleeps))


def test_scoring_errors_are_recorded_per_question(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=606)
    ids = [q["srt_id"] for q in qs]
    truncated = fk.message('"total": 5', stop_reason="max_tokens")
    fake = fk.FakeClaude(
        score=fk.scripted(fk.scorer(4, 7), {
            ids[0]: [RuntimeError("boom")],
            ids[1]: ["{not json", fk.DEFAULT],
            ids[2]: ["{bad", "{bad", "{bad"],
            ids[3]: [truncated, truncated, truncated],
            ids[4]: [fk.api_error(anthropic.BadRequestError, 400)],
            ids[5]: [fk.api_error(anthropic.RateLimitError, 429, {"retry-after": "7"}), fk.DEFAULT],
        }),
        report=fk.reporter(),
    )
    session = run(monkeypatch, fake, qs, fk.answers(qs, blank=(), whitespace=(), missing=()))

    assert session["status"] == "completed"
    assert [session["scores"][i]["score"] for i in ids[:5] if i != ids[1]] == [0, 0, 0, 0]
    golden("pm_pipeline_scoring_errors", outcome(session, fake, sleeps))


def test_report_failure_fails_the_session(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=707)
    fake = fk.FakeClaude(score=fk.scorer(4, 7), report=lambda p: RuntimeError("report generation exploded"))
    session = run(monkeypatch, fake, qs, fk.answers(qs))

    assert session["status"] == "failed"
    assert session["error"] == "report generation exploded"
    assert not session.get("report") and not session.get("pdf_bytes")
    golden("pm_pipeline_report_failure", outcome(session, fake, sleeps))


def test_pdf_failure_still_completes(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=808)
    fake = fk.FakeClaude(score=fk.scorer(4, 7), report=fk.reporter(broken_priority=True))
    session = run(monkeypatch, fake, qs, fk.answers(qs))

    assert session["status"] == "completed"
    assert session["pdf_error"] and not session.get("pdf_bytes")
    golden("pm_pipeline_pdf_failure", outcome(session, fake, sleeps))


def test_rescore_resumes_from_valid_prior_scores(store, monkeypatch, golden, sleeps):
    qs = fk.questions(seed=909)
    ids = [q["srt_id"] for q in qs]

    def prior(i, score, improvement):
        return {"competency": qs[i]["primary_competency"], "score": score, "base_score": 7,
                "english_proficiency": 1.0, "english_note": "", "strengths": ["kept"],
                "improvements": [improvement], "details": {}}

    prior_scores = {
        ids[0]: prior(0, 6.5, "valid earlier score"),
        ids[1]: prior(1, 9, "valid earlier score"),
        ids[2]: prior(2, 0, "Question not answered — counted as zero."),
        ids[3]: prior(3, 0, "Scoring error after 3 attempt(s) [RateLimitError]: x"),
        ids[4]: {"score": 0, "improvements": []},
        ids[5]: prior(5, 0.85, "below 1 after the English factor"),   # int() makes this 0, so it is re-scored
    }
    fake = fk.FakeClaude(score=fk.scorer(4, 7), report=fk.reporter())
    session = run(monkeypatch, fake, qs, fk.answers(qs, blank=(), whitespace=(), missing=()), prior_scores)

    scored = [c["payload"]["srt_id"] for c in fake.calls if c["mode"] == "score_one"]
    assert set(scored) == set(ids) - {ids[0], ids[1], ids[2]}
    assert session["scores"][ids[0]]["strengths"] == ["kept"]
    golden("pm_pipeline_resume", outcome(session, fake, sleeps))
