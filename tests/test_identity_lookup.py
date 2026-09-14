"""Employee lookup, including test identities for environments with no portal."""
from fastapi.testclient import TestClient

import identity
import main

UNAVAILABLE = "The employee directory is not available in this environment."


def lookup(code, email):
    with TestClient(main.app) as client:
        return client.post("/api/identity/lookup", json={"employee_code": code, "email": email})


def test_no_portal_and_no_test_list_is_refused(store, monkeypatch):
    monkeypatch.delenv("SRT_TEST_IDENTITIES", raising=False)
    r = lookup("K00089", "ksbhoon@rdc.in")
    assert (r.status_code, r.json()["detail"]) == (503, UNAVAILABLE)


def test_a_test_identity_stands_in_where_there_is_no_portal(store, monkeypatch):
    monkeypatch.setenv("SRT_TEST_IDENTITIES", "A1|a@x.in|Someone|Pune ; K00089 | KSBhoon@rdc.in | Test Person | Head Office")
    r = lookup(" k00089 ", "ksbhoon@RDC.in")
    assert r.status_code == 200
    assert r.json() == {"employee_code": "K00089", "full_name": "Test Person", "designation": "Test identity",
                        "location": "Head Office"}
    assert lookup("K00089", "someone.else@rdc.in").status_code == 503     # both must match
    assert lookup("K00090", "ksbhoon@rdc.in").status_code == 503


def test_a_wildcard_code_accepts_any_employee_code_for_that_email(store, monkeypatch):
    monkeypatch.setenv("SRT_TEST_IDENTITIES", "*|tester@rdc.in|Tester (test)|Test")
    r = lookup(" A00123 ", "Tester@rdc.in")
    assert r.json() == {"employee_code": "A00123", "full_name": "Tester (test)", "designation": "Test identity",
                        "location": "Test"}
    assert lookup("A00123", "other@rdc.in").status_code == 503               # the e-mail must still match
    assert lookup("", "tester@rdc.in").status_code in (422, 503)              # a code is still required


def test_the_test_list_is_ignored_when_the_portal_is_configured(store, monkeypatch):
    monkeypatch.setenv("SRT_TEST_IDENTITIES", "K00089|ksbhoon@rdc.in|Test Person|Head Office")
    monkeypatch.setattr(identity, "MASTER_API_URL", "https://portal.example")
    monkeypatch.setattr(identity, "MASTER_API_KEY", "key")
    monkeypatch.setattr(main, "identity_configured", lambda: True)
    monkeypatch.setattr(main, "resolve_employee", lambda **kw: {"ok": False, "reason": "not_found", "message": None})
    assert identity.test_identity("K00089", "ksbhoon@rdc.in") is None
    assert lookup("K00089", "ksbhoon@rdc.in").status_code == 404
