"""Shared fixtures for the SRT test suite.

The Plant Manager (PM) tests in this folder are characterisation tests. They
record what the PM assessment does today — which questions it picks, what it
sends to Claude, how it turns Claude's replies into scores, readiness and a
PDF, and what it stores — and they fail if any of that changes. They exist so
a second assessment (PQI) can be added beside PM with evidence, not assurance,
that PM is untouched.

Running
-------
    python -m pip install -r requirements.txt -r requirements-dev.txt
    python -m pytest

Postgres. Production runs on Postgres, so every storage-dependent test runs
twice: against the in-memory fallback and against a real database. The
Postgres half runs only when TEST_DATABASE_URL is set, and only against a
server on this machine whose database name contains "test" — the fixtures
DROP the tables. Never point it at hr_srt. The database must use UTC, as
production does, or the watchdog's timestamp arithmetic is wrong:

    ALTER DATABASE srt_test SET timezone TO 'UTC';
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5439/srt_test

Goldens. Expected outputs live in tests/golden/. They were recorded from the
PM code as deployed (commit acf04b9). A failing golden means PM behaviour
changed. Only if that change was explicitly approved, re-record with

    UPDATE_GOLDEN=1 python -m pytest

and commit the golden diff with the code change so it can be reviewed.
"""
import datetime as _datetime
import json
import os
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The app reads its configuration at import time. Pin every setting PM
# behaviour depends on to its code default, so a developer's shell or a copied
# .env cannot make the goldens pass or fail by accident.
for _var in (
    "DATABASE_URL", "ROOT_PATH", "REQUIRE_SSO", "MASTER_API_URL", "MASTER_API_KEY",
    "EXCEL_PATH", "ASSESSMENT_MINUTES", "SCORE_CEILING", "WATCHDOG_TIMEOUT_MINUTES",
    "ANTHROPIC_SCORER_MODEL", "ANTHROPIC_REPORT_MODEL", "ANTHROPIC_REPORT_MAX_TOKENS",
    "REVIEW_LOW_THRESHOLD", "REVIEW_HIGH_THRESHOLD", "REVIEW_MAX_TOKENS",
):
    os.environ.pop(_var, None)
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-a-real-key"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"

# ReportLab otherwise stamps a creation time and a random document id into
# every file, which would make PDF bytes differ run to run.
from reportlab import rl_config  # noqa: E402

rl_config.invariant = 1

import database  # noqa: E402
import main  # noqa: E402
import pdf_generator  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
GOLDEN_DIR = Path(__file__).parent / "golden"
FIXED_REPORT_DATE = _datetime.date(2026, 9, 13)


# ── Goldens ──────────────────────────────────────────────────────────────────
def _normalise(value):
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False))


class Golden:
    updating = os.environ.get("UPDATE_GOLDEN") == "1"

    def _path(self, name):
        return GOLDEN_DIR / f"{name}.json"

    def expected(self, name, actual):
        """The recorded value for `name`; records `actual` when updating."""
        path = self._path(name)
        actual = _normalise(actual)
        if self.updating:
            GOLDEN_DIR.mkdir(exist_ok=True)
            path.write_text(
                json.dumps(actual, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return actual
        assert path.exists(), f"missing golden {path.name}; record it with UPDATE_GOLDEN=1"
        return json.loads(path.read_text(encoding="utf-8"))

    def __call__(self, name, actual):
        expected = self.expected(name, actual)
        assert _normalise(actual) == expected, f"Plant Manager behaviour differs from golden {name}.json"


@pytest.fixture
def golden():
    return Golden()


# ── Storage ──────────────────────────────────────────────────────────────────
def _require_throwaway_database():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    url = urlparse(TEST_DATABASE_URL)
    if url.hostname not in ("127.0.0.1", "localhost", "::1") or "test" not in url.path:
        pytest.exit(
            "TEST_DATABASE_URL must name a local database with 'test' in its name; "
            "the fixtures drop tables.",
            returncode=2,
        )


def drop_tables():
    conn = database._get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS sessions, access_codes, srt_evaluations, srt_masters, srt_bank CASCADE")
        conn.commit()
    finally:
        conn.close()


def _reset_app_state(monkeypatch):
    main._cache.clear()
    monkeypatch.setattr(main, "_pipeline_semaphore", None)


@pytest.fixture(params=["memory", "postgres"])
def store(request, monkeypatch):
    """Runs the test once per storage backend, each starting empty."""
    if request.param == "postgres":
        _require_throwaway_database()
        monkeypatch.setattr(database, "DATABASE_URL", TEST_DATABASE_URL)
        drop_tables()
        database.init_db()
    else:
        monkeypatch.setattr(database, "DATABASE_URL", None)
        monkeypatch.setattr(database, "_memory_store", {})
        monkeypatch.setattr(database, "_memory_codes", {})
        monkeypatch.setattr(database, "_memory_evaluations", [])
        monkeypatch.setattr(database, "_memory_masters", {})
    _reset_app_state(monkeypatch)
    yield request.param
    main._cache.clear()


@pytest.fixture
def pg(monkeypatch):
    """Postgres only, with no tables yet — the test decides when init_db runs."""
    _require_throwaway_database()
    monkeypatch.setattr(database, "DATABASE_URL", TEST_DATABASE_URL)
    drop_tables()
    _reset_app_state(monkeypatch)
    yield database._get_conn
    main._cache.clear()


# ── Determinism ──────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def fixed_report_date(monkeypatch):
    """The PDF prints today's date; pin it so PDF bytes are comparable."""
    class _Date(_datetime.date):
        @classmethod
        def today(cls):
            return FIXED_REPORT_DATE

    monkeypatch.setattr(pdf_generator, "datetime", types.SimpleNamespace(date=_Date))
