"""PQI end to end through the HTTP API, with Claude replaced by a deterministic fake.

Covers the locked decisions: codes bound to a type, a frozen balanced form,
per-answer saves, resume on the original deadline, blank and Conditional AFI
handling, strict caps, guardrails by failure type, neutral second review,
evaluation errors and resumable rescoring, the report and PDF — and that a
broken PQI master leaves Plant Manager untouched.
"""
import copy
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import database
import main
import pqi_evaluator
import pqi_fakes as pf
import pqi_scoring
import pqi_service
from pqi_generator import generate_form
from test_pm_api import ADMIN, EMPLOYEE, EXTERNAL

TYPE_LABEL = "Plant Quality Incharge (PQI)"


@pytest.fixture
def pqi(store, monkeypatch):
    fake = pf.FakePqiClaude()
    monkeypatch.setattr(main, "client", fake)
    monkeypatch.setattr(pqi_evaluator, "time", SimpleNamespace(sleep=lambda s: None))
    with TestClient(main.app) as client:
        client.fake = fake
        client.store = store
        yield client


def use_claude(api, monkeypatch, fake):
    monkeypatch.setattr(main, "client", fake)
    api.fake = fake


def new_code(api, assessment_type="pqi", max_uses=10):
    r = api.post("/api/admin/generate-code", headers=ADMIN,
                 json={"label": "batch", "max_uses": max_uses, "assessment_type": assessment_type})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def start(api, code, body=EXTERNAL):
    return api.post("/api/start-session", json={**body, "access_code": code})


def used(api, code):
    return next(c["used_count"] for c in api.get("/api/admin/access-codes", headers=ADMIN).json() if c["code"] == code)


def session(sid):
    main._cache.clear()
    return database.get_session(sid)


def set_deadline(api, sid, minutes_from_now):
    deadline = pqi_service.utcnow() + timedelta(minutes=minutes_from_now)
    if api.store == "postgres":
        conn = database._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE sessions SET deadline_at = %s WHERE session_id = %s", (deadline, sid))
            conn.commit()
        finally:
            conn.close()
    else:
        database._memory_store[sid]["deadline_at"] = pqi_service.iso(deadline)
    main._cache.clear()


def form_with(monkeypatch, *srt_ids):
    """Make the next attempt's form one that contains `srt_ids`."""
    master = pqi_service.current_master()
    for seed in range(20000):
        if set(srt_ids) <= set(generate_form(master, seed=seed)["srt_ids"]):
            monkeypatch.setattr(pqi_service, "generate_form", lambda m, seed=seed: generate_form(m, seed=seed))
            return
    raise AssertionError("no seed found")


def answer_all(api, sid, blank=()):
    questions = session(sid)["questions"]
    answers = {q["srt_id"]: f"Answer to {q['srt_id']}: hold the load, test, record, inform, prevent recurrence."
               for i, q in enumerate(questions) if i not in blank}
    r = api.post("/api/submit-all", json={"session_id": sid, "answers": answers})
    assert r.status_code == 200, r.text
    return answers


# ── Codes and starting ───────────────────────────────────────────────────────
def test_each_code_starts_only_its_own_assessment(pqi):
    pqi_code, pm_code = new_code(pqi, "pqi"), new_code(pqi, "plant_manager")
    assert pqi.post("/api/validate-code", json={"code": pqi_code}).json()["assessment_type"] == "pqi"
    assert pqi.post("/api/validate-code", json={"code": pqi_code}).json()["assessment_label"] == TYPE_LABEL
    assert pqi.post("/api/validate-code", json={"code": pm_code}).json()["assessment_type"] == "plant_manager"
    listed = {c["code"]: c["assessment_type"] for c in pqi.get("/api/admin/access-codes", headers=ADMIN).json()}
    assert listed == {pqi_code: "pqi", pm_code: "plant_manager"}
    bad = pqi.post("/api/admin/generate-code", headers=ADMIN, json={"assessment_type": "plant_director"})
    assert (bad.status_code, bad.json()["detail"]) == (400, "Unknown assessment type.")

    pm = start(pqi, pm_code, EMPLOYEE).json()
    assert set(pm) == {"session_id", "questions", "total_questions"}          # Plant Manager response unchanged
    assert (session(pm["session_id"]).get("assessment_type") or "plant_manager") == "plant_manager"
    assert start(pqi, pqi_code).json()["assessment_type"] == "pqi"


def test_start_freezes_a_balanced_form_and_never_sends_guidance(pqi):
    code = new_code(pqi)
    r = start(pqi, code)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"session_id", "assessment_type", "questions", "total_questions", "answers", "answer_meta",
                         "remaining_seconds", "deadline_at", "status", "resumed"}
    assert (body["total_questions"], body["resumed"], body["answers"], body["status"]) == (30, False, {}, "in_progress")
    assert 90 * 60 - 10 <= body["remaining_seconds"] <= 90 * 60
    assert all(set(q) == {"question_number", "srt_id", "situation"} for q in body["questions"])
    assert used(pqi, code) == 1

    stored = session(body["session_id"])
    master = pqi_service.current_master()
    assert (stored["assessment_type"], stored["master_version"], stored["rubric_version"]) == (
        "pqi", "1.0-PILOT", "SRT_Scoring_Rubric_v1")
    assert stored["master_sha256"] == master["sha256"] and stored["prompt_version"].startswith("pqi-evaluator-2:")
    snapshot = stored["srt_snapshot"]
    assert [s["srt_id"] for s in snapshot] == [q["srt_id"] for q in body["questions"]]
    assert generate_form(master, seed=stored["generation_seed"])["srt_ids"] == [s["srt_id"] for s in snapshot]
    assert Counter(s["primary_competency"] for s in snapshot) == {f"C{i}": 3 for i in range(1, 11)}
    assert 18 <= sum(s["afi_applicability"] == "Yes" for s in snapshot) <= 22

    text = r.text
    for srt in snapshot:
        for secret in ("model_response", "evaluation_anchors", "serious_negatives", "primary_discriminator"):
            assert srt[secret] not in text


def test_answers_are_saved_and_resume_keeps_the_same_attempt(pqi):
    code = new_code(pqi)
    first = start(pqi, code).json()
    sid, ids = first["session_id"], [q["srt_id"] for q in first["questions"]]

    save = lambda **kw: pqi.post("/api/save-answer", json={"session_id": sid, **kw})
    assert save(srt_id=ids[0], text="Typed answer").json() == {"saved": True}
    assert save(srt_id=ids[1], text="बोलकर जवाब", input="voice", language="hi-IN").json() == {"saved": True}
    assert save(srt_id=ids[2], text="temporary").json() == {"saved": True}
    assert save(srt_id=ids[2], text="  ").json() == {"saved": True}                   # cleared
    r = save(srt_id="C1-99", text="x")
    assert (r.status_code, r.json()["detail"]) == (400, "That question is not part of this assessment.")

    again = start(pqi, code).json()
    assert (again["session_id"], again["resumed"]) == (sid, True)
    assert again["questions"] == first["questions"]
    assert again["answers"] == {ids[0]: "Typed answer", ids[1]: "बोलकर जवाब"}
    assert again["answer_meta"][ids[1]] == {"input": "voice", "language": "hi-IN"}
    assert again["remaining_seconds"] <= first["remaining_seconds"]
    assert used(pqi, code) == 1

    state = pqi.get(f"/api/session-state/{sid}").json()
    assert state["answers"] == again["answers"] and state["resumed"] is False

    other = start(pqi, code, {**EXTERNAL, "email": "someone.else@example.com"}).json()
    assert other["session_id"] != sid and used(pqi, code) == 2


def test_deadline_is_never_extended(pqi):
    code = new_code(pqi)
    first = start(pqi, code).json()
    sid = first["session_id"]
    pqi.post("/api/save-answer", json={"session_id": sid, "srt_id": first["questions"][0]["srt_id"], "text": "Saved in time"})

    set_deadline(pqi, sid, -30)
    late = pqi.post("/api/save-answer", json={"session_id": sid, "srt_id": first["questions"][1]["srt_id"], "text": "late"})
    assert (late.status_code, late.json()["detail"]) == (410, "The assessment time has ended.")

    expired = start(pqi, code)
    assert expired.status_code == 409 and "time has ended" in expired.json()["detail"]
    done = session(sid)
    assert done["status"] == "completed" and done["collected_answers"] == {first["questions"][0]["srt_id"]: "Saved in time"}
    assert used(pqi, code) == 1


def test_abandoned_attempt_is_submitted_by_the_admin_sweep(pqi):
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    set_deadline(pqi, sid, -5)                      # still inside the grace period
    pqi.get("/api/admin/sessions", headers=ADMIN)
    assert session(sid)["status"] == "in_progress"
    set_deadline(pqi, sid, -(pqi_service.EXPIRED_SUBMIT_AFTER_MINUTES + 1))
    pqi.get("/api/admin/sessions", headers=ADMIN)
    assert session(sid)["status"] == "completed"


# ── Scoring ──────────────────────────────────────────────────────────────────
def test_full_submission_produces_the_report(pqi):
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answers = answer_all(pqi, sid, blank=(2, 9))
    assert pqi.get(f"/api/submission-status/{sid}").json() == {"status": "completed", "progress": 30, "total": 30,
                                                                  "error": None}
    stored = session(sid)
    master = pqi_service.current_master()
    report, scores, snapshot = stored["report"], stored["scores"], stored["srt_snapshot"]

    assert pqi.fake.srt_ids("evaluate") and set(pqi.fake.srt_ids("evaluate")) == set(answers)
    assert pqi.fake.modes().count("consistency") == 1 and pqi.fake.modes().count("report") == 1

    blank_ids = [snapshot[2]["srt_id"], snapshot[9]["srt_id"]]
    for srt_id in blank_ids:
        blank = scores[srt_id]
        srt = next(s for s in snapshot if s["srt_id"] == srt_id)
        assert (blank["status"], blank["final_score"]) == ("blank", 0)
        assert blank["afi_activated"] is (srt["afi_applicability"] == "Yes")

    ordered = [scores[s["srt_id"]] for s in snapshot]
    head = pqi_scoring.headline(master, ordered)
    shown = pqi_scoring.reported_headline(master, ordered)
    # Printed on the current scale; the master's native figures are kept beside
    # them, and readiness is still decided on the native ones.
    assert report["score_scale"]["name"] == pqi_scoring.CURRENT_SCALE and report["overall_attainable_max"] == 100
    assert report["overall_pqi_score"] == round(shown["overall"], 1)
    assert report["technical_acumen"] == round(shown["technical_acumen"], 1)
    assert report["business_acumen"] == round(shown["business_acumen"], 1)
    assert report["native_scores"]["overall"] == round(head["overall"], 1)
    assert [c["score"] for c in report["competency_scores"]] == [round(shown["competencies"][c["code"]], 1)
                                                                 for c in master["competencies"]]
    assert report["anti_firefighting_index"] == round(head["afi"], 1)      # AFI is not on the scale
    assert report["afi_scored_srts"] == sum(1 for r in ordered if r["afi_activated"])
    assert report["overall_readiness"] == pqi_scoring.readiness(master, head, ordered)["band"]
    assert [r["srt_id"] for r in report["srt_results"]] == [s["srt_id"] for s in snapshot]
    assert len(report["top_strengths"]) == 2
    assert report["development_priorities"][1]["srt_ids"] == []            # unknown SRT id dropped
    assert (report["master"]["version"], report["master"]["sha256"]) == ("1.0-PILOT", master["sha256"])

    evaluations = pqi.get(f"/api/admin/session-evaluations/{sid}", headers=ADMIN).json()
    first = [e for e in evaluations["evaluations"] if e["pass"] == "first"]
    assert len(first) == 30 and Counter(e["status"] for e in first) == {"scored": 28, "blank": 2}
    assert all(e["prompt_version"] == stored["prompt_version"] for e in evaluations["evaluations"])

    row = next(r for r in pqi.get("/api/admin/sessions", headers=ADMIN).json() if r["session_id"] == sid)
    assert row["assessment_type"] == "pqi" and row["pqi_headline"]["overall"] == report["overall_pqi_score"]
    assert row["pqi_headline"]["scale"] == pqi_scoring.CURRENT_SCALE and row["progress"] == 30
    assert row["readiness"] == report["overall_readiness"]
    detail = pqi.get(f"/api/admin/report/{sid}", headers=ADMIN).json()
    assert detail["assessment_type"] == "pqi"
    diagnosis = pqi.get(f"/api/admin/diagnose/{sid}", headers=ADMIN).json()
    assert diagnosis["pqi"]["evaluated"] == 28 and diagnosis["pqi"]["blank"] == 2

    pdf = pqi.get(f"/api/admin/download-pdf/{sid}", headers=ADMIN)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-disposition"] == 'attachment; filename="RDC_PQI_Outside_Applicant.pdf"'

    # What Claude was given: the master rubric verbatim, the SRT guidance, a forced tool.
    call = next(c for c in pqi.fake.calls if c["mode"] == "evaluate")
    system = call["kwargs"]["system"][0]["text"]
    assert "Judge only what the candidate actually says." in system and "## Sheet: AI_Evaluation_Sequence" in system
    assert call["payload"]["SRT"]["Model_Response"]
    assert call["kwargs"]["model"] == pqi_evaluator.PQI_EVALUATOR_MODEL

    # Request shapes Claude Sonnet 5 rejects or truncates: sampling parameters
    # (400), an assistant prefill (400), and a max_tokens with no room for
    # its default adaptive thinking.
    for c in pqi.fake.calls:
        kw = c["kwargs"]
        assert set(kw) == {"model", "max_tokens", "system", "tools", "tool_choice", "messages"}
        assert [m["role"] for m in kw["messages"]] == ["user"]
        assert kw["max_tokens"] >= 16000


def test_caps_are_strict_and_lift_needs_core_7(pqi, monkeypatch):
    def rule(srt_id, payload):
        return {"core": 7, "lift": 2, "decision": "No"}
    use_claude(pqi, monkeypatch, pf.FakePqiClaude(evaluate=pf.evaluator(rule)))
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answer_all(pqi, sid)
    stored = session(sid)
    for srt in stored["srt_snapshot"]:
        result = stored["scores"][srt["srt_id"]]
        assert result["final_score"] == (6 if srt["decision_applicable"] == "Yes" else 9)


def test_severe_failure_without_a_type_goes_to_manual_review(pqi, monkeypatch):
    form_with(monkeypatch, "C1-01")

    def rule(srt_id, payload):
        if srt_id == "C1-01":
            return {"core": 6, "critical": "Yes", "reason": "Accepted the supplier's benefit"}
        return {"core": 7, "lift": 1, "decision": "Yes", "afi_activated": "Yes", "afi_level": 4}
    use_claude(pqi, monkeypatch, pf.FakePqiClaude(evaluate=pf.evaluator(rule)))
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answer_all(pqi, sid)
    report = session(sid)["report"]

    assert "C1-01" in pqi.fake.srt_ids("review")
    flag = next(f for f in report["critical_flags"] if f["srt_id"] == "C1-01")
    assert (flag["severity"], flag["type"], flag["reason"]) == ("Severe", "Unclassified", "Accepted the supplier's benefit")
    assert session(sid)["scores"]["C1-01"]["final_score"] <= 2
    assert report["readiness"]["manual_review_required"] is True
    assert "does not classify Critical_Failure_Type" in report["readiness"]["manual_review_reasons"][0]


def typed_master(monkeypatch, types, severe=()):
    """A future master that classifies Critical_Failure_Type (and, for tests, extra Severe items)."""
    master = copy.deepcopy(pqi_service.current_master())
    master["sha256"] = "typed-" + master["sha256"][6:]
    master["has_failure_type"] = True
    for srt in master["srts"]:
        srt["critical_failure_type"] = types.get(srt["srt_id"], "Other")
        if srt["srt_id"] in severe:
            srt["critical_failure_severity"] = "Severe"
    monkeypatch.setitem(pqi_service._state, "master", master)
    monkeypatch.setitem(pqi_service._masters_by_sha, master["sha256"], master)


def test_guardrails_follow_the_failure_type(pqi, monkeypatch):
    # C4-03 sits in a Business competency; as Technical-Safety + Severe it still
    # counts toward the technical guardrail. (C4-03 and C4-10 never share a
    # balanced form, so the second technical failure here is C9-05, made Severe.)
    typed_master(monkeypatch, {"C1-01": "Integrity", "C4-03": "Technical-Safety", "C9-05": "Technical-Safety"},
                 severe={"C9-05"})
    form_with(monkeypatch, "C1-01", "C4-03", "C9-05")

    def high_with(failing):
        def rule(srt_id, payload):
            if srt_id in failing:
                return {"core": 5, "critical": "Yes", "reason": "Advocated it"}
            return {"core": 7, "lift": 2, "decision": "Yes", "afi_activated": "Yes", "afi_level": 5}
        return pf.FakePqiClaude(evaluate=pf.evaluator(rule))

    code = new_code(pqi)
    use_claude(pqi, monkeypatch, high_with({"C4-03", "C9-05"}))
    sid = start(pqi, code).json()["session_id"]
    answer_all(pqi, sid)
    technical = session(sid)["report"]["readiness"]
    assert (technical["score_band"], technical["band"], technical["manual_review_required"]) == (
        "High Readiness", "Ready", True)

    use_claude(pqi, monkeypatch, high_with({"C1-01"}))
    sid = start(pqi, code, {**EXTERNAL, "email": "second@example.com"}).json()["session_id"]
    answer_all(pqi, sid)
    integrity = session(sid)["report"]["readiness"]
    assert (integrity["score_band"], integrity["band"]) == ("High Readiness", "Developing")


def test_second_review_near_a_boundary_is_neutral(pqi, monkeypatch):
    def review(payload):
        srt_id = payload["SRT"]["SRT_ID"]
        return pf.evaluation(payload, core=6 if srt_id.endswith("1") else 5, afi_level=2, afi_activated="No")
    use_claude(pqi, monkeypatch, pf.FakePqiClaude(evaluate=pf.evaluator(core=5, lift=0, decision="Yes", afi_level=2,
                                                                          afi_activated="No"), review=review))
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answer_all(pqi, sid, blank=(0,))
    report = session(sid)["report"]

    assert "near_readiness_boundary" in report["second_review"]["triggers"]
    reviewed = pqi.fake.srt_ids("review")
    assert len(reviewed) == 29 and session(sid)["srt_snapshot"][0]["srt_id"] not in reviewed
    for call in (c for c in pqi.fake.calls if c["mode"] == "review"):
        told = (call["instruction"] + str(call["payload"]["Earlier_Evaluation"])).lower()
        assert "boundary" not in told and "readiness" not in told and "overall" not in told
    changed = [c["srt_id"] for c in report["second_review"]["changes"]]
    assert changed and all(s.endswith("1") for s in changed)
    for srt_id in changed:
        assert session(sid)["scores"][srt_id]["first_pass"]["final_score"] == 5


def test_evaluation_errors_fail_the_session_and_rescore_resumes(pqi, monkeypatch):
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    broken_id = session(sid)["srt_snapshot"][4]["srt_id"]

    def rule(srt_id, payload):
        return {"core": 9} if srt_id == broken_id else {}         # core 9 is out of range: rejected every time
    use_claude(pqi, monkeypatch, pf.FakePqiClaude(evaluate=pf.evaluator(rule)))
    answer_all(pqi, sid)
    failed = session(sid)
    assert failed["status"] == "failed" and broken_id in failed["error"] and not failed.get("report")
    assert len(failed["scores"]) == 29
    errors = [e for e in database.list_evaluations(sid) if e["status"] == "error"]
    assert [e["srt_id"] for e in errors] == [broken_id]

    use_claude(pqi, monkeypatch, pf.FakePqiClaude())
    r = pqi.post(f"/api/admin/rescore/{sid}", headers=ADMIN)
    assert r.status_code == 200
    assert pqi.fake.srt_ids("evaluate") == [broken_id]
    assert session(sid)["status"] == "completed"


# ── Re-issuing a report on the current scale ─────────────────────────────────
def test_reissue_rebuilds_the_report_without_evaluating_again(pqi, monkeypatch):
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answer_all(pqi, sid)
    before = session(sid)
    # Make it look like a report issued before the scale existed.
    old = dict(before["report"])
    for key in ("score_scale", "native_scores"):
        old.pop(key)
    old["headline"] = {k: v for k, v in old["headline"].items() if k != "scale"}
    old["overall_pqi_score"] = before["report"]["native_scores"]["overall"]
    database.update_session(sid, report=old)
    main._cache.pop(sid, None)

    row = next(r for r in pqi.get("/api/admin/sessions", headers=ADMIN).json() if r["session_id"] == sid)
    assert "scale" not in row["pqi_headline"]

    use_claude(pqi, monkeypatch, pf.FakePqiClaude())
    queued = pqi.post("/api/admin/pqi/reissue-all", headers=ADMIN).json()
    assert [q["session_id"] for q in queued["queued"]] == [sid]

    after = session(sid)
    assert after["status"] == "completed" and not after.get("error")
    # One narrative call; nothing evaluated, reviewed or cross-checked again.
    assert pqi.fake.modes() == ["report"]
    assert after["scores"] == before["scores"]
    assert after["report"]["score_scale"]["name"] == pqi_scoring.CURRENT_SCALE
    assert after["report"]["overall_pqi_score"] == before["report"]["overall_pqi_score"]
    assert after["report"]["overall_readiness"] == before["report"]["overall_readiness"]
    assert after["report"]["reissued_from"]["score_scale"] == "native (level x 10)"
    assert session(sid).get("pdf_bytes")

    # Already on the current scale: nothing to do.
    assert pqi.post("/api/admin/pqi/reissue-all", headers=ADMIN).json()["queued"] == []


def test_a_failed_reissue_keeps_the_report_that_was_there(pqi, monkeypatch):
    sid = start(pqi, new_code(pqi)).json()["session_id"]
    answer_all(pqi, sid)
    before = session(sid)["report"]
    use_claude(pqi, monkeypatch, pf.FakePqiClaude(report=lambda p: {"top_strengths": "not a list"}))
    assert pqi.post(f"/api/admin/pqi/reissue/{sid}", headers=ADMIN).status_code == 200
    after = session(sid)
    assert after["status"] == "completed" and after["error"].startswith("Re-issue failed")
    assert after["report"] == before


def test_only_a_finished_pqi_report_can_be_reissued(pqi):
    sid = start(pqi, new_code(pqi)).json()["session_id"]             # still in progress
    assert pqi.post(f"/api/admin/pqi/reissue/{sid}", headers=ADMIN).status_code == 409


def test_voice_language_reaches_the_evaluator(pqi):
    first = start(pqi, new_code(pqi)).json()
    srt_id = first["questions"][0]["srt_id"]
    pqi.post("/api/submit-all", json={"session_id": first["session_id"], "answers": {srt_id: "पहले lab test"},
                                      "answer_meta": {srt_id: {"input": "voice", "language": "hi-IN"}}})
    call = next(c for c in pqi.fake.calls if c["mode"] == "evaluate")
    assert "Hindi speech recognition" in call["payload"]["Candidate"]["Response_Capture"]


def test_quick_test_runs_a_pqi_attempt(pqi):
    r = pqi.post("/api/admin/quick-test", headers=ADMIN, json={"assessment_type": "pqi", "candidate_name": "QT"}).json()
    assert r["assessment_type"] == "pqi"
    assert session(r["session_id"])["status"] == "completed"


def test_plant_manager_sessions_refuse_pqi_saves(pqi):
    pm = start(pqi, new_code(pqi, "plant_manager"), EMPLOYEE).json()
    r = pqi.post("/api/save-answer", json={"session_id": pm["session_id"], "srt_id": pm["questions"][0]["srt_id"], "text": "x"})
    assert r.status_code == 400


def test_a_broken_pqi_master_leaves_plant_manager_working(pqi, monkeypatch):
    pqi_code, pm_code = new_code(pqi, "pqi"), new_code(pqi, "plant_manager")
    monkeypatch.setitem(pqi_service._state, "master", None)
    monkeypatch.setitem(pqi_service._state, "error", "PQI_SRT_Master_100 lacks column(s): Situation")

    status = pqi.get("/api/admin/pqi-master", headers=ADMIN).json()
    assert status["available"] is False and "Situation" in status["error"]
    assert pqi.post("/api/admin/generate-code", headers=ADMIN, json={"assessment_type": "pqi"}).status_code == 503
    assert pqi.post("/api/validate-code", json={"code": pqi_code}).status_code == 503
    assert start(pqi, pqi_code).status_code == 503
    assert used(pqi, pqi_code) == 0

    assert pqi.post("/api/validate-code", json={"code": pm_code}).status_code == 200
    assert start(pqi, pm_code, EMPLOYEE).status_code == 200
