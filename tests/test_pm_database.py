"""Plant Manager storage on Postgres: schema, migrations over old rows, maintenance queries."""
import json

import database
import main

# The sessions table exactly as it was first created, before any of the
# migrations in init_db. Production rows from that time still exist.
LEGACY_SESSIONS_DDL = """
    CREATE TABLE sessions (
        session_id       TEXT PRIMARY KEY,
        candidate_name   TEXT,
        plant_location   TEXT,
        assessment_date  TEXT,
        status           TEXT DEFAULT 'in_progress',
        progress         INTEGER DEFAULT 0,
        error            TEXT,
        questions        JSONB,
        collected_answers JSONB,
        scores           JSONB DEFAULT '{}'::jsonb,
        report           JSONB,
        pdf_data         BYTEA,
        pdf_error        TEXT,
        created_at       TIMESTAMP DEFAULT NOW()
    )
"""
LEGACY_CODES_DDL = """
    CREATE TABLE access_codes (
        code        TEXT PRIMARY KEY,
        label       TEXT,
        max_uses    INTEGER DEFAULT 10,
        used_count  INTEGER DEFAULT 0,
        created_at  TIMESTAMP DEFAULT NOW()
    )
"""
LEGACY_REPORT = {
    "overall_score_out_of_300": 187.5, "normalized_score_out_of_100": 62.5,
    "overall_readiness": "Ready with Structured Support", "competency_summary": {"Integrity & Trust": 6.5},
}


def sql(connect, statement, params=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() if cur.description else None
        conn.commit()
        return rows
    finally:
        conn.close()


def schema(connect):
    columns = sql(connect, """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name IN ('sessions', 'access_codes')
        ORDER BY table_name, ordinal_position""")
    indexes = sql(connect, """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename IN ('sessions', 'access_codes')""")
    return {
        "columns": {f"{t}.{c}": {"type": d, "nullable": n, "default": df} for t, c, d, n, df in columns},
        "indexes": dict(indexes),
    }


def test_schema_keeps_every_pm_column(pg, golden):
    database.init_db()
    actual = schema(pg)
    expected = golden.expected("pm_db_schema", actual)
    for name, spec in expected["columns"].items():
        assert actual["columns"].get(name) == spec, f"Plant Manager column {name} changed"
    for name, definition in expected["indexes"].items():
        assert actual["indexes"].get(name) == definition, f"index {name} changed"
    for name, spec in actual["columns"].items():
        if name not in expected["columns"]:
            assert spec["nullable"] == "YES" or spec["default"] is not None, (
                f"new column {name} is NOT NULL without a default, so existing inserts would fail"
            )


def test_init_db_is_idempotent(pg):
    database.init_db()
    first = schema(pg)
    database.init_db()
    database.init_db()
    assert schema(pg) == first


def test_rows_written_before_the_migrations_still_read_correctly(pg):
    sql(pg, LEGACY_SESSIONS_DDL)
    sql(pg, LEGACY_CODES_DDL)
    sql(pg, """
        INSERT INTO sessions (session_id, candidate_name, plant_location, assessment_date, status, progress,
                              questions, collected_answers, scores, report, pdf_data, created_at)
        VALUES ('legacy-1', 'Old Candidate', 'Old Plant', '2026-03-01', 'completed', 30,
                %s, %s, %s, %s, %s, '2026-03-01 10:00:00')""",
        (json.dumps([{"srt_id": "IT-01", "question_number": 1}]), json.dumps({"IT-01": "answer"}),
         json.dumps({"IT-01": {"score": 6.5}}), json.dumps(LEGACY_REPORT), b"%PDF-legacy"))
    sql(pg, "INSERT INTO access_codes (code, label, max_uses, used_count) VALUES ('1111111111', 'old', 3, 1)")

    database.init_db()

    assert database.get_session("legacy-1") == {
        "candidate": {"candidate_name": "Old Candidate", "plant_location": "Old Plant",
                      "assessment_date": "2026-03-01", "candidate_type": "employee"},
        "status": "completed", "progress": 30, "error": None,
        "questions": [{"srt_id": "IT-01", "question_number": 1}],
        "collected_answers": {"IT-01": "answer"},
        "scores": {"IT-01": {"score": 6.5}},
        "report": LEGACY_REPORT,
        "pdf_bytes": b"%PDF-legacy", "pdf_error": None, "processing_started_at": None,
        # Added with PQI (additive): a pre-PQI row reads as Plant Manager with no PQI data.
        "assessment_type": "plant_manager", "master_version": None, "rubric_version": None,
        "master_sha256": None, "prompt_version": None, "evaluator_model": None, "generation_seed": None,
        "generation_info": None, "srt_snapshot": None, "deadline_at": None, "last_saved_at": None,
        "answer_meta": None,
    }
    assert database.list_sessions() == [{
        "session_id": "legacy-1", "candidate_name": "Old Candidate", "plant_location": "Old Plant",
        "assessment_date": "2026-03-01", "candidate_type": "employee", "status": "completed",
        "total_score": 187.5, "normalized": 62.5, "readiness": "Ready with Structured Support",
        "questions_answered": 1, "collected_count": 1, "has_pdf": True, "error": None,
        "created_at": "2026-03-01T10:00:00Z", "processing_started_at": None,
        "assessment_type": "plant_manager", "pqi_headline": None,   # added with PQI (additive)
    }]
    assert sql(pg, "SELECT person_id, employee_code, captured_email FROM sessions") == [(None, None, None)]
    assert database.consume_access_code("1111111111")["used_count"] == 2


def test_watchdog_fails_only_stale_processing_sessions(pg):
    database.init_db()
    assert sql(pg, "SHOW TimeZone") == [("UTC",)], "run the test database in UTC, as production does"
    for sid, status, minutes, error in [
        ("stale", "processing", 20, None),
        ("fresh", "processing", 5, None),
        ("stale-with-error", "processing", 30, "real failure"),
        ("done", "completed", 60, None),
    ]:
        database.create_session(sid, {"candidate_name": sid}, [])
        sql(pg, """UPDATE sessions SET status = %s, error = %s,
                       processing_started_at = (NOW() AT TIME ZONE 'UTC') - make_interval(mins => %s)
                   WHERE session_id = %s""", (status, error, minutes, sid))
    database.create_session("live", {"candidate_name": "live"}, [])
    database.update_session("live", status="processing", processing_started_at=main._now_utc())

    assert database.auto_fail_stale_processing(timeout_minutes=15) == 2
    rows = {r[0]: r[1:] for r in sql(pg, "SELECT session_id, status, error, processing_started_at IS NULL FROM sessions")}
    status, error, cleared = rows["stale"]
    assert status == "failed" and cleared
    assert error.startswith("Watchdog auto-fail: pipeline stuck in processing >15 min")
    assert rows["stale-with-error"] == ("failed", "real failure", True)
    assert rows["fresh"] == ("processing", None, False)
    assert rows["live"] == ("processing", None, False)
    assert rows["done"] == ("completed", None, False)


def test_access_code_cannot_be_used_past_its_limit(pg):
    database.init_db()
    database.create_access_code("2222222222", label="x", max_uses=1)
    assert database.consume_access_code("2222222222")["used_count"] == 1
    assert database.consume_access_code("2222222222") is None
    assert database.consume_access_code("9999999999") is None
    assert database.get_access_code("2222222222")["used_count"] == 1


def test_update_session_ignores_unknown_fields(pg):
    database.init_db()
    database.create_session("s1", {"candidate_name": "A"}, [{"srt_id": "X"}])
    database.update_session("s1", nonsense=1)
    database.update_session("s1", status="failed", nonsense=2)
    assert database.get_session("s1")["status"] == "failed"
