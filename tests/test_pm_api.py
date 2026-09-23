"""Plant Manager HTTP surface: candidate flow, access codes and the admin console APIs."""
import importlib.util
import inspect
from collections import Counter

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import assessment_types
import database
import main
import pm_fakes as fk
from conftest import ROOT

ADMIN = {"x-admin-token": "test-admin-password"}
SAFE_QUESTION_KEYS = {"question_number", "srt_id", "primary_competency", "secondary_competency", "situation"}

EMPLOYEE = {
    "candidate_name": "Typed Name", "plant_location": "Typed Plant", "assessment_date": "2026-09-13",
    "candidate_type": "employee", "employee_code": "E1001", "email": "Person@Example.com",
}
EXTERNAL = {
    "candidate_name": "  Outside Applicant ", "plant_location": " Nagpur ", "assessment_date": "2026-09-13",
    "candidate_type": "external", "email": "applicant@example.com",
}
PERSON = {
    "person_id": 77, "employee_code": "E1001", "email": "person@rdc.in", "full_name": "Master Name",
    "employment": {"location": "Master Plant", "designation": "Plant Manager"},
}

PM_ADMIN_ROUTES = {
    ("GET", "/api/admin/sessions"), ("POST", "/api/admin/watchdog"), ("GET", "/api/admin/report/{session_id}"),
    ("DELETE", "/api/admin/session/{session_id}"), ("POST", "/api/admin/quick-test"),
    ("POST", "/api/admin/force-reset/{session_id}"), ("POST", "/api/admin/rescore/{session_id}"),
    ("POST", "/api/admin/rescore-stuck"), ("POST", "/api/admin/rescore-validation-batch"),
    ("GET", "/api/admin/diagnose/{session_id}"), ("POST", "/api/admin/force-reset-processing/{session_id}"),
    ("POST", "/api/admin/generate-code"), ("GET", "/api/admin/access-codes"),
    ("DELETE", "/api/admin/access-code/{code}"), ("GET", "/api/admin/download-pdf/{session_id}"),
}


@pytest.fixture
def api(store, monkeypatch):
    fake = fk.FakeClaude(score=fk.scorer(5, 8), review=fk.reviewer, report=fk.reporter())
    monkeypatch.setattr(main, "client", fake)
    with TestClient(main.app) as client:
        client.fake = fake
        client.store = store
        yield client


def new_code(api, max_uses=10):
    r = api.post("/api/admin/generate-code", json={"label": "Batch A", "max_uses": max_uses}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def start(api, code, body=EMPLOYEE):
    return api.post("/api/start-session", json={**body, "access_code": code})


def used(api, code):
    return api.post("/api/validate-code", json={"code": code}).json()["used_count"]


def stored_row(session_id):
    conn = database._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT candidate_name, plant_location, assessment_date, status, person_id,
                          employee_code, captured_email, candidate_type, jsonb_array_length(questions)
                   FROM sessions WHERE session_id = %s""",
                (session_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ── Pages and public config ──────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/", "/assessment", "/thank-you", "/admin", "/admin/report"])
def test_pages_are_served_with_an_empty_base_path(api, path):
    r = api.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'var B = "";' in r.text


def test_health_and_config(api):
    assert api.get("/health").json() == {"status": "ok", "questions_loaded": 100}

    # /api/config is how every page learns what to call itself: one engine name
    # for all of them, and one entry per assessment underneath. A page that
    # cannot read this falls back to the role-free default, so "default" being
    # present matters as much as the assessments themselves.
    cfg = api.get("/api/config").json()
    assert cfg["assessment_minutes"] == 75
    assert cfg["engine_name"] == assessment_types.ENGINE_NAME
    assert cfg["assessment_order"] == ["plant_manager", "pqi"]
    assert set(cfg["assessments"]) == {"default", "plant_manager", "pqi"}
    assert cfg["assessments"]["plant_manager"]["role"] == "Plant Manager"
    assert cfg["assessments"]["default"]["role"] == ""


# ── Admin authentication ─────────────────────────────────────────────────────
def admin_routes():
    for route in main.app.routes:
        if isinstance(route, APIRoute) and "_admin_identity" in inspect.getsource(route.endpoint):
            for method in route.methods:
                yield method, route.path


def test_every_admin_route_refuses_anonymous_callers(api):
    routes = set(admin_routes())
    assert PM_ADMIN_ROUTES <= routes
    for method, path in sorted(routes):
        url = path.replace("{session_id}", "no-such-session").replace("{code}", "1234567890")
        extra = {"json": {}} if method == "POST" else {}
        for headers in ({}, {"x-auth-email": "someone@rdc.in"}, {"x-admin-token": "wrong"}):
            r = api.request(method, url, headers=headers, **extra)
            assert r.status_code == 401, (method, path, headers, r.status_code)


def test_admin_gate_check_passes():
    spec = importlib.util.spec_from_file_location("check_admin_gate", ROOT / "tools" / "check_admin_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main_check() == 0


def test_admin_password_and_sso(api, monkeypatch):
    assert api.post("/api/admin/login", json={"password": "nope"}).status_code == 401
    assert api.post("/api/admin/login", json={"password": "test-admin-password"}).json() == {
        "success": True, "token": "test-admin-password",
    }
    sso = {"x-auth-email": "hr@rdc.in"}
    assert api.get("/api/admin/me", headers=sso).json() == {"email": None, "sso": False}
    assert api.get("/api/admin/sessions", headers=sso).status_code == 401

    monkeypatch.setattr(main, "REQUIRE_SSO", True)
    assert api.get("/api/admin/me", headers=sso).json() == {"email": "hr@rdc.in", "sso": True}
    assert api.get("/api/admin/sessions", headers=sso).status_code == 200
    assert api.get("/api/admin/sessions", headers=ADMIN).status_code == 200


# ── Access codes ─────────────────────────────────────────────────────────────
def test_access_code_lifecycle(api):
    for bad in (-1, 101):
        r = api.post("/api/admin/generate-code", json={"max_uses": bad}, headers=ADMIN)
        assert (r.status_code, r.json()["detail"]) == (400, "max_uses must be between 1 and 100")
    code = new_code(api, max_uses=2)
    assert len(code) == 10 and code.isdigit()
    listed = api.get("/api/admin/access-codes", headers=ADMIN).json()
    assert [(c["code"], c["label"], c["max_uses"], c["used_count"]) for c in listed] == [(code, "Batch A", 2, 0)]

    for bad in ("123", "abcdefghij", ""):
        r = api.post("/api/validate-code", json={"code": bad})
        assert (r.status_code, r.json()["detail"]) == (400, "Access code must be exactly 10 digits.")
    assert api.post("/api/validate-code", json={"code": "0000000001"}).status_code == 404
    assert api.post("/api/validate-code", json={"code": f" {code} "}).json() == {
        "valid": True, "max_uses": 2, "used_count": 0, "remaining": 2,
        # Added with PQI (additive): the code names its assessment.
        "assessment_type": "plant_manager", "assessment_label": "Plant Manager",
    }

    assert start(api, code).status_code == 200
    assert start(api, code).status_code == 200
    third = start(api, code)
    assert (third.status_code, third.json()["detail"]) == (
        410, "This access code has been fully used (2/2). Please request a new one from HR.",
    )
    assert api.post("/api/validate-code", json={"code": code}).status_code == 410

    assert api.delete(f"/api/admin/access-code/{code}", headers=ADMIN).json() == {"deleted": True, "code": code}
    assert api.delete(f"/api/admin/access-code/{code}", headers=ADMIN).status_code == 404

    # Current behaviour, recorded rather than endorsed: 0 is falsy, so it becomes the default of 10.
    zero = api.post("/api/admin/generate-code", json={"max_uses": 0}, headers=ADMIN)
    assert (zero.status_code, zero.json()["max_uses"]) == (200, 10)


# ── Starting a session ───────────────────────────────────────────────────────
def test_start_session_rejects_incomplete_requests(api):
    code = new_code(api)
    cases = [
        ({**EMPLOYEE, "access_code": ""}, 400, "A valid 10-digit access code is required."),
        ({**EMPLOYEE, "access_code": code, "candidate_type": "contractor"}, 400, "Unknown candidate type."),
        ({**EMPLOYEE, "access_code": code, "email": "  "}, 400, "An e-mail address is required."),
        ({**EMPLOYEE, "access_code": code, "employee_code": " "}, 400, "Employee code is required."),
        ({**EXTERNAL, "access_code": code, "candidate_name": " A "}, 400, "Please enter your full name."),
        ({**EMPLOYEE, "access_code": "0000000001"}, 404, "Invalid access code. Please check with HR."),
    ]
    for body, status, detail in cases:
        r = api.post("/api/start-session", json=body)
        assert (r.status_code, r.json()["detail"]) == (status, detail), body
    assert used(api, code) == 0


def test_employee_admitted_without_directory_keeps_typed_details(api):
    code = new_code(api)
    r = start(api, code)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "questions", "total_questions"}
    assert body["total_questions"] == 30
    qs = body["questions"]
    assert all(set(q) == SAFE_QUESTION_KEYS for q in qs)
    assert [q["question_number"] for q in qs] == list(range(1, 31))
    assert set(Counter(q["primary_competency"] for q in qs).values()) == {3}
    assert used(api, code) == 1

    session = database.get_session(body["session_id"])
    assert session["status"] == "in_progress"
    assert [q["srt_id"] for q in session["questions"]] == [q["srt_id"] for q in qs]
    if api.store == "postgres":
        assert stored_row(body["session_id"]) == (
            "Typed Name", "Typed Plant", "2026-09-13", "in_progress", None, "E1001", "person@example.com", "employee", 30,
        )
    else:
        assert session["candidate"] == {
            "candidate_name": "Typed Name", "plant_location": "Typed Plant", "assessment_date": "2026-09-13",
            "candidate_type": "employee", "employee_code": "E1001", "captured_email": "person@example.com",
        }


def test_employee_details_come_from_the_master(api, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "resolve_employee", lambda **kw: seen.append(kw) or {"ok": True, "person": PERSON})
    sid = start(api, new_code(api)).json()["session_id"]
    assert seen == [{"employee_code": "E1001", "email": "person@example.com"}]
    candidate = database.get_session(sid)["candidate"]
    assert (candidate["candidate_name"], candidate["plant_location"]) == ("Master Name", "Master Plant")
    if api.store == "postgres":
        assert stored_row(sid) == (
            "Master Name", "Master Plant", "2026-09-13", "in_progress", 77, "E1001", "person@rdc.in", "employee", 30,
        )


@pytest.mark.parametrize("reason,status", [("not_found", 403), ("unavailable", 503)])
def test_directory_refusal_does_not_use_the_code(api, monkeypatch, reason, status):
    monkeypatch.setattr(main, "resolve_employee", lambda **kw: {"ok": False, "reason": reason, "message": None})
    code = new_code(api)
    assert start(api, code).status_code == status
    assert used(api, code) == 0
    assert api.get("/api/admin/sessions", headers=ADMIN).json() == []


def test_external_candidates(api, monkeypatch):
    code = new_code(api)

    def details(sid):
        c = database.get_session(sid)["candidate"]
        return c["candidate_name"], c["plant_location"], c["candidate_type"]

    # No directory in this environment: admitted on the typed details.
    assert details(start(api, code, EXTERNAL).json()["session_id"]) == ("Outside Applicant", "Nagpur", "external")

    calls = []

    def resolver(result):
        def resolve(**kw):
            calls.append(kw)
            return result
        return resolve

    # An applicant whose address is already on the master is recorded as an employee.
    monkeypatch.setattr(main, "resolve_person", resolver({"ok": True, "person": PERSON}))
    assert details(start(api, code, EXTERNAL).json()["session_id"]) == ("Master Name", "Master Plant", "employee")
    assert calls[-1] == {"email": "applicant@example.com", "name": "Outside Applicant",
                         "require_internal": False, "create": True}

    registered = {"person_id": 9, "email": "applicant@example.com", "full_name": "Registered"}
    monkeypatch.setattr(main, "resolve_person", resolver({"ok": True, "person": registered}))
    assert details(start(api, code, EXTERNAL).json()["session_id"]) == ("Outside Applicant", "Nagpur", "external")

    monkeypatch.setattr(main, "resolve_person", resolver({"ok": False, "reason": "not_found", "message": "Address rejected"}))
    r = start(api, code, EXTERNAL)
    assert (r.status_code, r.json()["detail"]) == (400, "Address rejected")
    assert used(api, code) == 3


# ── Submission, results and PDF ──────────────────────────────────────────────
def test_candidate_submission_to_admin_pdf(api):
    body = start(api, new_code(api)).json()
    sid = body["session_id"]
    assert api.get(f"/api/admin/report/{sid}", headers=ADMIN).json() == {"detail": "Report not yet generated"}
    assert api.get(f"/api/submission-status/{sid}").json() == {
        "status": "in_progress", "progress": 0, "total": 30, "error": None,
    }

    # TestClient runs the background pipeline before returning, so the next call sees the result.
    r = api.post("/api/submit-all", json={"session_id": sid, "answers": fk.answers(body["questions"])})
    assert r.json() == {"status": "processing", "total": 30}
    assert api.get(f"/api/submission-status/{sid}").json() == {
        "status": "completed", "progress": 30, "total": 30, "error": None,
    }

    rows = api.get("/api/admin/sessions", headers=ADMIN).json()
    assert len(rows) == 1
    row = rows[0]
    expected_keys = {
        "session_id", "candidate_name", "plant_location", "assessment_date", "candidate_type", "created_at",
        "status", "total_score", "normalized", "readiness", "questions_answered", "collected_count",
        "has_pdf", "error",
        "assessment_type", "pqi_headline",   # added with PQI (additive)
    }
    if api.store == "postgres":
        expected_keys.add("processing_started_at")
    assert set(row) == expected_keys

    detail = api.get(f"/api/admin/report/{sid}", headers=ADMIN).json()
    assert set(detail) == {"candidate", "report", "scores", "assessment_type"}   # assessment_type added with PQI
    assert detail["assessment_type"] == "plant_manager"
    report = detail["report"]
    assert row["total_score"] == report["overall_score_out_of_300"]
    assert row["normalized"] == report["normalized_score_out_of_100"]
    assert row["readiness"] == report["overall_readiness"]
    assert (row["status"], row["questions_answered"], row["collected_count"], row["has_pdf"], row["error"]) == (
        "completed", 30, 26, True, None,
    )

    anon = api.get(f"/api/admin/download-pdf/{sid}")
    assert (anon.status_code, anon.json()["detail"]) == (401, "Unauthorized — admin only")
    pdf = api.get(f"/api/admin/download-pdf/{sid}", headers=ADMIN)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == 'attachment; filename="RDC_SBCA_Typed_Name.pdf"'
    assert pdf.content.startswith(b"%PDF")
    legacy = api.get(f"/api/download-pdf/{sid}", follow_redirects=False)
    assert (legacy.status_code, legacy.headers["location"]) == (307, f"/api/admin/download-pdf/{sid}")


def test_submit_while_processing_is_ignored(api):
    sid = start(api, new_code(api)).json()["session_id"]
    database.update_session(sid, status="processing")
    main._cache.clear()
    assert api.post("/api/submit-all", json={"session_id": sid, "answers": {}}).json() == {"status": "already_processing"}
    assert api.post("/api/submit-all", json={"session_id": "nope", "answers": {}}).status_code == 404


def test_admin_maintenance_actions(api):
    sid = start(api, new_code(api)).json()["session_id"]
    qs = database.get_session(sid)["questions"]
    api.post("/api/submit-all", json={"session_id": sid, "answers": fk.answers(qs)})
    assert api.fake.modes().count("score_one") == 26

    diag = api.get(f"/api/admin/diagnose/{sid}", headers=ADMIN).json()
    assert {k: diag[k] for k in (
        "status", "collected_count", "answered_nonempty", "scored_count", "valid_scored", "legit_zeros",
        "error_zeros", "missing_questions", "next_rescore_runs", "has_report", "has_pdf",
        "scorer_model", "report_model", "pipeline_concurrency",
    )} == {
        "status": "completed", "collected_count": 29, "answered_nonempty": 26, "scored_count": 30,
        "valid_scored": 26, "legit_zeros": 4, "error_zeros": 0, "missing_questions": 0, "next_rescore_runs": 0,
        "has_report": True, "has_pdf": True, "scorer_model": "claude-haiku-4-5",
        "report_model": "claude-haiku-4-5", "pipeline_concurrency": 2,
    }

    # Smart resume keeps every valid score: no new scoring calls, a fresh report.
    assert api.post(f"/api/admin/rescore/{sid}", headers=ADMIN).json() == {
        "status": "rescoring", "mode": "smart-resume", "session_id": sid,
        "answers": 29, "resumed_scores": 30, "questions_to_run": 0,
    }
    assert api.fake.modes().count("score_one") == 26
    assert api.fake.modes().count("final_report") == 2

    full = api.post(f"/api/admin/rescore/{sid}?force_full=true", headers=ADMIN).json()
    assert (full["mode"], full["resumed_scores"], full["questions_to_run"]) == ("force-full", 0, 30)
    assert api.fake.modes().count("score_one") == 52
    assert api.get(f"/api/submission-status/{sid}").json()["status"] == "completed"

    r = api.post(f"/api/admin/force-reset-processing/{sid}", headers=ADMIN)
    assert r.status_code == 400 and "not 'processing'" in r.json()["detail"]
    database.update_session(sid, status="processing")
    main._cache.clear()
    assert api.post(f"/api/admin/force-reset-processing/{sid}", headers=ADMIN).json() == {"status": "failed", "session_id": sid}
    assert database.get_session(sid)["error"] == "Stuck in processing — manually reset by admin. Click Rescore to retry."

    stuck = api.post("/api/admin/rescore-stuck", headers=ADMIN).json()
    assert (stuck["scheduled"], stuck["skipped"], stuck["concurrency"]) == (1, [], 2)
    assert api.get(f"/api/submission-status/{sid}").json()["status"] == "completed"

    batch = api.post("/api/admin/rescore-validation-batch", json={"names": ["typed name", "Nobody Here"]},
                     headers=ADMIN).json()
    assert (batch["scheduled"], batch["scheduled_names"], batch["unmatched_names"]) == (1, ["Typed Name"], ["nobody here"])

    assert api.post("/api/admin/watchdog", headers=ADMIN).json() == {"auto_failed": 0, "timeout_minutes": 15}

    assert api.post(f"/api/admin/force-reset/{sid}", headers=ADMIN).json() == {"reset": True, "session_id": sid}
    reset = database.get_session(sid)
    assert (reset["status"], reset["progress"], reset["scores"], reset.get("report")) == ("in_progress", 0, {}, None)
    assert not reset.get("collected_answers")
    r = api.post(f"/api/admin/rescore/{sid}", headers=ADMIN)
    assert (r.status_code, r.json()["detail"]) == (400, "Session has no stored candidate answers — cannot rescore.")

    assert api.delete(f"/api/admin/session/{sid}", headers=ADMIN).json() == {"deleted": True}
    assert api.delete(f"/api/admin/session/{sid}", headers=ADMIN).status_code == 404
    assert api.post(f"/api/admin/rescore/{sid}", headers=ADMIN).status_code == 404


def test_quick_test_runs_a_full_dummy_assessment(api):
    r = api.post("/api/admin/quick-test", json={"candidate_name": "QT"}, headers=ADMIN).json()
    assert (r["status"], r["total"]) == ("processing", 30)
    session = database.get_session(r["session_id"])
    assert session["status"] == "completed" and len(session["collected_answers"]) == 30
    assert session["candidate"]["plant_location"] == "Test Plant – Admin"
