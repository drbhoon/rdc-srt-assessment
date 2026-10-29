"""AI-account problems (credit exhausted, usage limit, key rejected) are named in plain words.

Production, October 2026: the Anthropic credit ran out and both Plant Manager
and PQI showed "Failed" with raw API dumps, read as a scoring bug and
rescored again and again.
"""
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

import ai_account
import main
import pm_fakes as fk
import pqi_evaluator as ev
import pqi_fakes as pf
import pqi_service
from test_pm_pipeline import run, sleeps  # noqa: F401  (fixture)
from test_pqi_flow import answer_all, form_with, new_code, session, start, use_claude

# The text the API returns, as raised by the SDK.
CREDIT_TEXT = ("Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to "
               "upgrade or purchase credits.")


def credit_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": CREDIT_TEXT}}
    return anthropic.BadRequestError(f"Error code: 400 - {body}", response=httpx.Response(400, request=request),
                                     body=body)


@pytest.mark.parametrize("error,message", [
    (credit_error(), ai_account.CREDIT),
    ("BadRequestError: Error code: 400 - {'message': 'You have reached your specified API usage limits.'}",
     ai_account.USAGE_LIMIT),
    ("AuthenticationError: Error code: 401 - {'type': 'authentication_error', 'message': 'invalid x-api-key'}",
     ai_account.BAD_KEY),
    (["C1-01: ReplyFormatError after 3 attempts: afi", "C2-02: BadRequestError: " + CREDIT_TEXT], ai_account.CREDIT),
    ("ReplyFormatError after 3 attempts: afi_evidence_level is required", None),
    (RuntimeError("report generation exploded"), None),
])
def test_account_problems_are_recognised(error, message):
    assert ai_account.account_problem(error) == message


def test_every_message_says_what_to_do():
    for message in (ai_account.CREDIT, ai_account.USAGE_LIMIT, ai_account.BAD_KEY):
        assert message.startswith("Anthropic ") and "Rescore" in message


def test_plant_manager_names_exhausted_credit(store, monkeypatch, sleeps):
    qs = fk.questions(seed=707)
    fake = fk.FakeClaude(score=fk.scorer(4, 7), report=lambda p: credit_error())
    stored = run(monkeypatch, fake, qs, fk.answers(qs))
    assert stored["status"] == "failed"
    assert stored["error"] == ai_account.CREDIT


@pytest.fixture
def pqi2(store, monkeypatch):
    monkeypatch.setattr(pqi_service, "CURRENT_RUBRIC", ev.RUBRIC_V2)
    monkeypatch.setattr(main, "client", pf.FakePqiClaude())
    monkeypatch.setattr(ev, "time", SimpleNamespace(sleep=lambda s: None))
    with TestClient(main.app) as client:
        client.store = store
        yield client


def test_pqi_names_exhausted_credit_and_keeps_what_was_done(pqi2, monkeypatch):
    form_with(monkeypatch, "C1-01", "C2-01")

    def rule(srt_id, payload):
        return credit_error() if srt_id in ("C1-01", "C2-01") else {"level": 5, "decision": "Yes"}
    use_claude(pqi2, monkeypatch, pf.FakePqiClaude(evaluate_v2=pf.evaluator_v2(rule)))
    sid = start(pqi2, new_code(pqi2)).json()["session_id"]
    answer_all(pqi2, sid)
    stored = session(sid)
    assert stored["status"] == "failed"
    assert stored["error"] == f"{ai_account.CREDIT} (28 of 30 evaluations completed.)"
    assert len(stored["scores"]) == 28

    # Credit restored: Rescore finishes the two missing ones only.
    use_claude(pqi2, monkeypatch, pf.FakePqiClaude())
    assert pqi2.post(f"/api/admin/rescore/{sid}", headers={"X-Admin-Token": main.ADMIN_PASSWORD}).status_code == 200
    stored = session(sid)
    assert stored["status"] == "completed" and stored["report"]["scoring_rubric"] == ev.RUBRIC_V2


def test_pqi_narrative_failure_on_credit(pqi2, monkeypatch):
    use_claude(pqi2, monkeypatch, pf.FakePqiClaude(report=lambda p: credit_error()))
    sid = start(pqi2, new_code(pqi2)).json()["session_id"]
    answer_all(pqi2, sid)
    assert session(sid)["error"] == ai_account.CREDIT
