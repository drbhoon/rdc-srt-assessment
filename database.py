"""PostgreSQL persistence layer for RDC SRT Assessment sessions.

Uses psycopg2 directly with JSONB columns for complex data.
Falls back to in-memory dict if DATABASE_URL is not set.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── In-memory fallback (development / no DB configured) ──────────────────────
_memory_store: Dict[str, dict] = {}


def _get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create sessions + access_codes tables if they don't exist."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — using in-memory storage (data lost on restart)")
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
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
            """)
            # v4.20 watchdog migration — add processing_started_at column if
            # missing, so we can detect stuck pipelines automatically. Idempotent.
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP NULL
            """)
            # Identity migration — link every session to the shared person
            # spine in the portal. Until now a session recorded a typed name
            # and a typed plant and nothing else, so an SRT score could not be
            # tied to the person who earned it.
            #
            # SRT is roll-bound: everyone assessed here is an employee, on-roll
            # or off-roll, so employee_code and email are both required at the
            # door. person_id stays NULLABLE because sessions created before
            # this migration have no identity to give them.
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS person_id BIGINT NULL
            """)
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS employee_code TEXT NULL
            """)
            # The address exactly as it was given, kept beside the resolved id.
            # The address book can change afterwards, and without this there is
            # no record of what the link was actually made from.
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS captured_email TEXT NULL
            """)
            # SRT is also used to screen people who are NOT on the rolls yet —
            # recruitment. Those sessions carry a typed name and e-mail and no
            # employee code, so "employee_code IS NULL" alone cannot tell an
            # outside candidate apart from a pre-identity legacy row.
            #
            # Records what the RESOLUTION found, not which tab was clicked: if
            # an outside applicant types an address the master already holds,
            # this says 'employee', because that is what they are. Existing
            # rows default to 'employee', which is what SRT admitted until now.
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS candidate_type TEXT NOT NULL DEFAULT 'employee'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS sessions_person_idx ON sessions(person_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS sessions_employee_code_idx ON sessions(employee_code)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS access_codes (
                    code        TEXT PRIMARY KEY,
                    label       TEXT,
                    max_uses    INTEGER DEFAULT 10,
                    used_count  INTEGER DEFAULT 0,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            # Assessment-type migration (PQI). Every existing session and code
            # was Plant Manager, which is exactly what the defaults record, so
            # no existing row changes meaning. Everything else is PQI-only and
            # nullable; Plant Manager never writes it.
            cur.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS assessment_type TEXT NOT NULL DEFAULT 'plant_manager'
            """)
            cur.execute("""
                ALTER TABLE access_codes
                ADD COLUMN IF NOT EXISTS assessment_type TEXT NOT NULL DEFAULT 'plant_manager'
            """)
            for column, sql_type in (
                ("master_version",  "TEXT"),       # e.g. 1.0-PILOT
                ("rubric_version",  "TEXT"),       # e.g. SRT_Scoring_Rubric_v1
                ("master_sha256",   "TEXT"),       # exact workbook the attempt was built from
                ("prompt_version",  "TEXT"),
                ("evaluator_model", "TEXT"),
                ("generation_seed", "BIGINT"),     # reproduces the 30-SRT form
                ("generation_info", "JSONB"),
                ("srt_snapshot",    "JSONB"),      # the 30 SRT records, frozen at start
                ("deadline_at",     "TIMESTAMP"),  # UTC; start + ASSESSMENT_MINUTES, never extended
                ("last_saved_at",   "TIMESTAMP"),
                ("answer_meta",     "JSONB"),      # {srt_id: {input, language}}
            ):
                cur.execute(f"ALTER TABLE sessions ADD COLUMN IF NOT EXISTS {column} {sql_type} NULL")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS sessions_type_email_idx
                ON sessions(assessment_type, captured_email)
            """)
            # Every master workbook ever used, by exact file hash, so an attempt
            # can be rescored under the version it was administered with.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS srt_masters (
                    sha256          TEXT PRIMARY KEY,
                    assessment_type TEXT NOT NULL,
                    master_version  TEXT,
                    rubric_version  TEXT,
                    status          TEXT,
                    file_name       TEXT,
                    content         JSONB NOT NULL,
                    registered_at   TIMESTAMP DEFAULT NOW()
                )
            """)
            # One row per SRT per master. The statistics columns stay empty
            # until pilot data exists; expert estimates are not validated
            # psychometrics and are not treated as such.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS srt_bank (
                    master_sha256             TEXT NOT NULL,
                    srt_id                    TEXT NOT NULL,
                    assessment_type           TEXT NOT NULL,
                    primary_competency        TEXT,
                    demand_type               TEXT,
                    decision_applicable       TEXT,
                    afi_applicability         TEXT,
                    critical_failure_severity TEXT,
                    critical_failure_type     TEXT,
                    expert_difficulty         NUMERIC NULL,
                    empirical_mean_score      NUMERIC NULL,
                    discrimination            NUMERIC NULL,
                    administration_count      INTEGER NOT NULL DEFAULT 0,
                    language_stats            JSONB NOT NULL DEFAULT '{}'::jsonb,
                    PRIMARY KEY (master_sha256, srt_id)
                )
            """)
            # Append-only audit of every AI evaluation: first pass, second
            # review and failures. Rows are never updated.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS srt_evaluations (
                    id                 BIGSERIAL PRIMARY KEY,
                    session_id         TEXT NOT NULL,
                    assessment_type    TEXT NOT NULL,
                    srt_id             TEXT NOT NULL,
                    primary_competency TEXT,
                    pass               TEXT NOT NULL,
                    status             TEXT NOT NULL,
                    response_text      TEXT,
                    response_capture   JSONB,
                    raw_output         JSONB,
                    result             JSONB,
                    final_score        INTEGER,
                    model              TEXT,
                    master_version     TEXT,
                    rubric_version     TEXT,
                    prompt_version     TEXT,
                    created_at         TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS srt_evaluations_session_idx ON srt_evaluations(session_id)
            """)
        conn.commit()
        logger.info("PostgreSQL sessions + access_codes tables ready (identity migration applied)")
    finally:
        conn.close()


# ── CRUD Operations ──────────────────────────────────────────────────────────

def _iso(dt) -> Optional[str]:
    """Naive-UTC datetime → ISO string with 'Z', matching how timestamps are returned."""
    return (dt.isoformat() + "Z") if dt else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# PQI attempt fields copied verbatim from the `pqi` argument of create_session.
_PQI_FIELDS = ("master_version", "rubric_version", "master_sha256", "prompt_version",
               "evaluator_model", "generation_seed", "generation_info", "srt_snapshot")


def _create_pqi_session(session_id: str, candidate: dict, questions: list,
                        assessment_type: str, pqi: dict) -> dict:
    session = {
        "candidate":         candidate,
        "questions":         questions,
        "scores":            {},
        "status":            "in_progress",
        "progress":          0,
        "assessment_type":   assessment_type,
        "collected_answers": {},
        "answer_meta":       {},
        **{k: pqi.get(k) for k in _PQI_FIELDS},
        "deadline_at":       _iso(pqi["deadline_at"]),
        "last_saved_at":     None,
    }

    if not DATABASE_URL:
        session["created_at"] = _iso(_utcnow())
        _memory_store[session_id] = session
        return session

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions
                   (session_id, candidate_name, plant_location, assessment_date,
                    questions, status, progress, scores,
                    person_id, employee_code, captured_email, candidate_type,
                    assessment_type, master_version, rubric_version, master_sha256,
                    prompt_version, evaluator_model, generation_seed, generation_info,
                    srt_snapshot, deadline_at, collected_answers, answer_meta)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    session_id,
                    candidate.get("candidate_name", ""),
                    candidate.get("plant_location", ""),
                    candidate.get("assessment_date", ""),
                    json.dumps(questions),
                    "in_progress",
                    0,
                    json.dumps({}),
                    candidate.get("person_id"),
                    candidate.get("employee_code"),
                    candidate.get("captured_email"),
                    candidate.get("candidate_type") or "employee",
                    assessment_type,
                    pqi.get("master_version"),
                    pqi.get("rubric_version"),
                    pqi.get("master_sha256"),
                    pqi.get("prompt_version"),
                    pqi.get("evaluator_model"),
                    pqi.get("generation_seed"),
                    json.dumps(pqi.get("generation_info") or {}),
                    json.dumps(pqi.get("srt_snapshot") or []),
                    pqi["deadline_at"],
                    json.dumps({}),
                    json.dumps({}),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return session


def create_session(session_id: str, candidate: dict, questions: list,
                   assessment_type: Optional[str] = None, pqi: Optional[dict] = None) -> dict:
    """Insert a new session. Returns the session dict.

    Plant Manager calls this with three arguments and takes the original path
    below, unchanged. A PQI attempt also passes `pqi`: its master versions,
    form seed, frozen SRT snapshot and deadline.
    """
    if pqi is not None:
        return _create_pqi_session(session_id, candidate, questions, assessment_type or "pqi", pqi)

    session = {
        "candidate":         candidate,
        "questions":         questions,
        "scores":            {},
        "status":            "in_progress",
        "progress":          0,
    }

    if not DATABASE_URL:
        _memory_store[session_id] = session
        return session

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions
                   (session_id, candidate_name, plant_location, assessment_date,
                    questions, status, progress, scores,
                    person_id, employee_code, captured_email, candidate_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    session_id,
                    candidate.get("candidate_name", ""),
                    candidate.get("plant_location", ""),
                    candidate.get("assessment_date", ""),
                    json.dumps(questions),
                    "in_progress",
                    0,
                    json.dumps({}),
                    candidate.get("person_id"),
                    candidate.get("employee_code"),
                    candidate.get("captured_email"),
                    candidate.get("candidate_type") or "employee",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return session


def get_session(session_id: str) -> Optional[dict]:
    """Retrieve a session by ID. Returns None if not found."""
    if not DATABASE_URL:
        return _memory_store.get(session_id)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT candidate_name, plant_location, assessment_date,
                          status, progress, error,
                          questions, collected_answers, scores,
                          report, pdf_data, pdf_error,
                          processing_started_at, candidate_type,
                          assessment_type, master_version, rubric_version, master_sha256,
                          prompt_version, evaluator_model, generation_seed, generation_info,
                          srt_snapshot, deadline_at, last_saved_at, answer_meta
                   FROM sessions WHERE session_id = %s""",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            return {
                # Assessment type and the PQI attempt fields. Appended after the
                # original columns so the positions below keep their meaning;
                # all None on Plant Manager rows.
                "assessment_type":   row[14] or "plant_manager",
                "master_version":    row[15],
                "rubric_version":    row[16],
                "master_sha256":     row[17],
                "prompt_version":    row[18],
                "evaluator_model":   row[19],
                "generation_seed":   row[20],
                "generation_info":   _json_value(row[21]),
                "srt_snapshot":      _json_value(row[22]),
                "deadline_at":       (row[23].isoformat() + "Z") if row[23] else None,
                "last_saved_at":     (row[24].isoformat() + "Z") if row[24] else None,
                "answer_meta":       _json_value(row[25]),
                "candidate": {
                    "candidate_name":  row[0],
                    "plant_location":  row[1],
                    "assessment_date": row[2],
                    "candidate_type":  row[13] or "employee",
                },
                "status":            row[3],
                "progress":          row[4],
                "error":             row[5],
                "questions":         row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
                "collected_answers": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
                "scores":            row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}"),
                "report":            row[9] if isinstance(row[9], dict) else json.loads(row[9] or "null"),
                "pdf_bytes":         bytes(row[10]) if row[10] else None,
                "pdf_error":         row[11],
                # v4.21 — append 'Z' so JS interprets as UTC (not local time).
                # Column is TIMESTAMP without TZ; we always write _now_utc()
                # so the naive value IS UTC. Without the Z, browsers in any
                # non-UTC zone (e.g. IST) misread this as local time and the
                # elapsed-minutes badge shows ~330m off in IST.
                "processing_started_at": (row[12].isoformat() + "Z") if row[12] else None,
            }
    finally:
        conn.close()


def update_session(session_id: str, **fields):
    """Update specific fields of a session."""
    if not DATABASE_URL:
        session = _memory_store.get(session_id)
        if session:
            for k, v in fields.items():
                session[k] = v
        return

    # Map Python field names to DB columns + serialize
    col_map = {
        "status":            ("status",            lambda v: v),
        "progress":          ("progress",          lambda v: v),
        "error":             ("error",             lambda v: v),
        "collected_answers": ("collected_answers",  lambda v: json.dumps(v)),
        "scores":            ("scores",            lambda v: json.dumps(v)),
        "report":            ("report",            lambda v: json.dumps(v)),
        "pdf_bytes":         ("pdf_data",          lambda v: v),  # already bytes
        "pdf_error":         ("pdf_error",         lambda v: v),
        "processing_started_at": ("processing_started_at", lambda v: v),  # datetime or None
        # PQI only
        "answer_meta":       ("answer_meta",       lambda v: json.dumps(v)),
        "prompt_version":    ("prompt_version",    lambda v: v),
        "evaluator_model":   ("evaluator_model",   lambda v: v),
        "last_saved_at":     ("last_saved_at",     lambda v: v),  # naive UTC datetime
    }

    sets, vals = [], []
    for key, value in fields.items():
        if key in col_map:
            col, serializer = col_map[key]
            sets.append(f"{col} = %s")
            vals.append(serializer(value))

    if not sets:
        return

    vals.append(session_id)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = %s",
                vals,
            )
        conn.commit()
    finally:
        conn.close()


def _json_value(value):
    """JSONB normally arrives decoded from psycopg2; tolerate text as well."""
    if value is None or not isinstance(value, str):
        return value
    return json.loads(value)


def delete_session(session_id: str) -> bool:
    """Delete a session. Returns True if found and deleted."""
    if not DATABASE_URL:
        _memory_evaluations[:] = [e for e in _memory_evaluations if e["session_id"] != session_id]
        return _memory_store.pop(session_id, None) is not None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
            deleted = cur.rowcount > 0
            # A PQI session's evaluation audit belongs to it and goes with it.
            cur.execute("DELETE FROM srt_evaluations WHERE session_id = %s", (session_id,))
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_sessions() -> List[dict]:
    """Return summary of all sessions, sorted by date descending."""
    if not DATABASE_URL:
        result = []
        for sid, s in _memory_store.items():
            c        = s.get("candidate", {})
            report   = s.get("report") or {}
            collected = s.get("collected_answers") or {}
            result.append({
                "session_id":         sid,
                "candidate_name":     c.get("candidate_name", ""),
                "plant_location":     c.get("plant_location", ""),
                "assessment_date":    c.get("assessment_date", ""),
                "candidate_type":     c.get("candidate_type") or "employee",
                "created_at":         s.get("created_at"),
                "status":             s.get("status", "in_progress"),
                "total_score":        report.get("overall_score_out_of_300"),
                "normalized":         report.get("normalized_score_out_of_100"),
                "readiness":          report.get("overall_readiness", "—"),
                "questions_answered": len(s.get("scores", {})),
                "progress":           s.get("progress", 0),
                # Separate from questions_answered — transcripts are preserved
                # even after a rescore wipes scores to {}, so this is the stable
                # indicator that a candidate actually completed their 30 answers.
                "collected_count":    sum(1 for v in collected.values() if (v or "").strip()),
                "has_pdf":            "pdf_bytes" in s,
                "error":              s.get("error"),
                "assessment_type":    s.get("assessment_type") or "plant_manager",
                "pqi_headline":       report.get("headline") if s.get("assessment_type") == "pqi" else None,
            })
        result.sort(key=lambda x: x["created_at"] or x["assessment_date"] or "", reverse=True)
        return result

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT session_id, candidate_name, plant_location, assessment_date,
                          status, scores, report, pdf_data IS NOT NULL, error, created_at,
                          collected_answers, processing_started_at, candidate_type,
                          assessment_type, progress
                   FROM sessions ORDER BY created_at DESC NULLS LAST, assessment_date DESC"""
            )
            result = []
            for row in cur.fetchall():
                scores    = row[5]  if isinstance(row[5],  dict) else json.loads(row[5]  or "{}")
                report    = row[6]  if isinstance(row[6],  dict) else json.loads(row[6]  or "{}")
                collected = row[10] if isinstance(row[10], dict) else json.loads(row[10] or "{}")
                result.append({
                    "session_id":         row[0],
                    "candidate_name":     row[1],
                    "plant_location":     row[2],
                    "assessment_date":    row[3],
                    "candidate_type":     row[12] or "employee",
                    "assessment_type":    row[13] or "plant_manager",
                    "pqi_headline":       (report or {}).get("headline") if row[13] == "pqi" else None,
                    "status":             row[4],
                    "total_score":        (report or {}).get("overall_score_out_of_300"),
                    "normalized":         (report or {}).get("normalized_score_out_of_100"),
                    "readiness":          (report or {}).get("overall_readiness", "—"),
                    "questions_answered": len(scores),
                    # Live count while a run is in flight — PQI records it as
                    # each evaluation completes, before any score is saved.
                    "progress":           row[14] or 0,
                    "collected_count":    sum(1 for v in collected.values() if (v or "").strip()),
                    "has_pdf":            row[7],
                    "error":              row[8],
                    # v4.21 — append 'Z' so JS interprets as UTC (see get_session)
                    "created_at":         (row[9].isoformat()  + "Z") if row[9]  else None,
                    "processing_started_at": (row[11].isoformat() + "Z") if row[11] else None,
                })
            return result
    finally:
        conn.close()


# ── v4.20 Watchdog: auto-fail stale processing sessions ──────────────────────
def auto_fail_stale_processing(timeout_minutes: int = 15) -> int:
    """Sweep sessions whose status='processing' but processing_started_at is
    older than the timeout. Flip them to status='failed' with a clear marker
    so the admin doesn't have to click Clear Lock manually.

    Returns the number of sessions auto-failed. Safe to call frequently
    (it's a single bounded UPDATE).

    Background-task pipelines on Railway sometimes die silently when the
    worker process restarts (deploy / OOM / asyncio task GC). Without a
    watchdog, those sessions stay 'processing' forever, requiring manual
    Clear Lock + Rescore. With this watchdog, they auto-recover within
    one timeout window.
    """
    if not DATABASE_URL:
        # In-memory mode — no easy way to track timestamps; skip
        return 0

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sessions
                   SET status = 'failed',
                       error  = COALESCE(NULLIF(error,''), %s),
                       processing_started_at = NULL
                   WHERE status = 'processing'
                     AND processing_started_at IS NOT NULL
                     AND processing_started_at < NOW() - (%s || ' minutes')::interval
                """,
                (
                    f"Watchdog auto-fail: pipeline stuck in processing >{timeout_minutes} min "
                    "(likely worker restart / silent crash). Click Rescore to retry.",
                    str(timeout_minutes),
                ),
            )
            count = cur.rowcount
        conn.commit()
        if count:
            logger.warning(
                "Watchdog auto-failed %d stale 'processing' session(s) (>%d min)",
                count, timeout_minutes,
            )
        return count
    finally:
        conn.close()


# ── Access Codes (10-digit HR-shared codes, up to 10 uses each) ──────────────

_memory_codes: Dict[str, dict] = {}


def create_access_code(code: str, label: str = "", max_uses: int = 10,
                       assessment_type: str = "plant_manager") -> dict:
    """Insert a new access code. Caller generates the 10-digit string.

    A code starts exactly one assessment type, fixed when it is generated.
    """
    record = {
        "code":            code,
        "label":           label or "",
        "max_uses":        max_uses,
        "used_count":      0,
        "assessment_type": assessment_type,
    }

    if not DATABASE_URL:
        _memory_codes[code] = record
        return record

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO access_codes (code, label, max_uses, used_count, assessment_type)
                   VALUES (%s, %s, %s, 0, %s)""",
                (code, label or "", max_uses, assessment_type),
            )
        conn.commit()
    finally:
        conn.close()

    return record


def get_access_code(code: str) -> Optional[dict]:
    """Return the access_code record or None if not found."""
    if not DATABASE_URL:
        return _memory_codes.get(code)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT code, label, max_uses, used_count, created_at, assessment_type
                   FROM access_codes WHERE code = %s""",
                (code,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "code":            row[0],
                "label":           row[1] or "",
                "max_uses":        row[2],
                "used_count":      row[3],
                "created_at":      row[4].isoformat() if row[4] else None,
                "assessment_type": row[5] or "plant_manager",
            }
    finally:
        conn.close()


def consume_access_code(code: str) -> Optional[dict]:
    """Atomically increment used_count if the code exists and has uses left.
    Returns the updated record on success, or None if invalid / exhausted.
    """
    if not DATABASE_URL:
        rec = _memory_codes.get(code)
        if not rec:
            return None
        if rec["used_count"] >= rec["max_uses"]:
            return None
        rec["used_count"] += 1
        return rec

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Atomic increment — only succeeds if room remains
            cur.execute(
                """UPDATE access_codes
                   SET used_count = used_count + 1
                   WHERE code = %s AND used_count < max_uses
                   RETURNING code, label, max_uses, used_count, assessment_type""",
                (code,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return None
            return {
                "code":            row[0],
                "label":           row[1] or "",
                "max_uses":        row[2],
                "used_count":      row[3],
                "assessment_type": row[4] or "plant_manager",
            }
    finally:
        conn.close()


def list_access_codes() -> List[dict]:
    """Return all access codes, newest first."""
    if not DATABASE_URL:
        rows = list(_memory_codes.values())
        return sorted(rows, key=lambda x: x.get("code", ""), reverse=True)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT code, label, max_uses, used_count, created_at, assessment_type
                   FROM access_codes ORDER BY created_at DESC"""
            )
            result = []
            for row in cur.fetchall():
                result.append({
                    "code":            row[0],
                    "label":           row[1] or "",
                    "max_uses":        row[2],
                    "used_count":      row[3],
                    "created_at":      row[4].isoformat() if row[4] else None,
                    "assessment_type": row[5] or "plant_manager",
                })
            return result
    finally:
        conn.close()


def delete_access_code(code: str) -> bool:
    """Delete an access code. Returns True if found and deleted."""
    if not DATABASE_URL:
        return _memory_codes.pop(code, None) is not None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM access_codes WHERE code = %s", (code,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def reset_session(session_id: str):
    """Reset a session back to in_progress (for force-reset)."""
    if not DATABASE_URL:
        session = _memory_store.get(session_id)
        if session:
            session["status"]   = "in_progress"
            session["progress"] = 0
            session.pop("error", None)
            session.pop("report", None)
            session.pop("pdf_bytes", None)
            session.pop("pdf_error", None)
            session.pop("collected_answers", None)
            session["scores"] = {}
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE sessions
                   SET status='in_progress', progress=0, error=NULL,
                       report=NULL, pdf_data=NULL, pdf_error=NULL,
                       collected_answers=NULL, scores='{}'::jsonb
                   WHERE session_id = %s""",
                (session_id,),
            )
        conn.commit()
    finally:
        conn.close()


# ── PQI: resume, per-answer saves, evaluation audit, master registry ─────────
# Plant Manager never calls anything below.

_memory_evaluations: List[dict] = []
_memory_masters: Dict[str, dict] = {}


def find_active_session(assessment_type: str, email: str):
    """The candidate's open attempt of this type as (session_id, session), or None."""
    email = (email or "").strip().lower()
    if not email:
        return None

    if not DATABASE_URL:
        matches = [
            (sid, s) for sid, s in _memory_store.items()
            if s.get("assessment_type") == assessment_type and s.get("status") == "in_progress"
            and ((s.get("candidate") or {}).get("captured_email") or "").strip().lower() == email
        ]
        return max(matches, key=lambda m: m[1].get("created_at") or "") if matches else None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT session_id FROM sessions
                   WHERE assessment_type = %s AND status = 'in_progress'
                     AND lower(captured_email) = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (assessment_type, email),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    session = get_session(row[0])
    return (row[0], session) if session else None


def save_answer(session_id: str, srt_id: str, text: str, meta: dict, saved_at) -> bool:
    """Store one answer (a blank one removes it). False if the session is no longer open."""
    keep = bool((text or "").strip())

    if not DATABASE_URL:
        session = _memory_store.get(session_id)
        if not session or session.get("status") != "in_progress":
            return False
        answers = dict(session.get("collected_answers") or {})
        metas = dict(session.get("answer_meta") or {})
        if keep:
            answers[srt_id], metas[srt_id] = text, meta
        else:
            answers.pop(srt_id, None)
            metas.pop(srt_id, None)
        session["collected_answers"], session["answer_meta"] = answers, metas
        session["last_saved_at"] = _iso(saved_at)
        return True

    if keep:
        sql = """UPDATE sessions
                 SET collected_answers = COALESCE(collected_answers, '{}'::jsonb) || jsonb_build_object(%s::text, %s::text),
                     answer_meta = COALESCE(answer_meta, '{}'::jsonb) || jsonb_build_object(%s::text, %s::jsonb),
                     last_saved_at = %s
                 WHERE session_id = %s AND status = 'in_progress'"""
        params = (srt_id, text, srt_id, json.dumps(meta), saved_at, session_id)
    else:
        sql = """UPDATE sessions
                 SET collected_answers = COALESCE(collected_answers, '{}'::jsonb) - %s::text,
                     answer_meta = COALESCE(answer_meta, '{}'::jsonb) - %s::text,
                     last_saved_at = %s
                 WHERE session_id = %s AND status = 'in_progress'"""
        params = (srt_id, srt_id, saved_at, session_id)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            saved = cur.rowcount > 0
        conn.commit()
        return saved
    finally:
        conn.close()


def list_expired_sessions(assessment_type: str, cutoff) -> List[str]:
    """Open attempts whose deadline (naive UTC) is before `cutoff`."""
    if not DATABASE_URL:
        out = []
        for sid, s in _memory_store.items():
            deadline = s.get("deadline_at")
            if (s.get("assessment_type") == assessment_type and s.get("status") == "in_progress" and deadline
                    and datetime.fromisoformat(str(deadline).rstrip("Z")) < cutoff):
                out.append(sid)
        return out

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT session_id FROM sessions
                   WHERE assessment_type = %s AND status = 'in_progress'
                     AND deadline_at IS NOT NULL AND deadline_at < %s""",
                (assessment_type, cutoff),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


_EVALUATION_COLUMNS = ("session_id", "assessment_type", "srt_id", "primary_competency", "pass", "status",
                       "response_text", "response_capture", "raw_output", "result", "final_score", "model",
                       "master_version", "rubric_version", "prompt_version")
_EVALUATION_JSON = {"response_capture", "raw_output", "result"}


def insert_evaluation(row: dict) -> None:
    """Append one evaluation to the audit trail. Rows are never updated."""
    if not DATABASE_URL:
        _memory_evaluations.append({**{c: row.get(c) for c in _EVALUATION_COLUMNS},
                                    "id": len(_memory_evaluations) + 1, "created_at": _iso(_utcnow())})
        return

    values = [json.dumps(row.get(c)) if c in _EVALUATION_JSON else row.get(c) for c in _EVALUATION_COLUMNS]
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO srt_evaluations ({', '.join(_EVALUATION_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(_EVALUATION_COLUMNS))})",
                values,
            )
        conn.commit()
    finally:
        conn.close()


def list_evaluations(session_id: str) -> List[dict]:
    if not DATABASE_URL:
        return [dict(e) for e in _memory_evaluations if e["session_id"] == session_id]

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, created_at, {', '.join(_EVALUATION_COLUMNS)} FROM srt_evaluations "
                "WHERE session_id = %s ORDER BY id",
                (session_id,),
            )
            out = []
            for row in cur.fetchall():
                record = {"id": row[0], "created_at": (row[1].isoformat() + "Z") if row[1] else None}
                for column, value in zip(_EVALUATION_COLUMNS, row[2:]):
                    record[column] = _json_value(value) if column in _EVALUATION_JSON else value
                out.append(record)
            return out
    finally:
        conn.close()


def register_master(master: dict) -> None:
    """Record a master workbook (by exact hash) and its SRT bank. Idempotent."""
    if not DATABASE_URL:
        _memory_masters.setdefault(master["sha256"], master)
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO srt_masters
                   (sha256, assessment_type, master_version, rubric_version, status, file_name, content)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (sha256) DO NOTHING""",
                (master["sha256"], "pqi", master["version"], master["rubric_version"], master["status"],
                 master["file_name"], json.dumps(master)),
            )
            cur.executemany(
                """INSERT INTO srt_bank
                   (master_sha256, srt_id, assessment_type, primary_competency, demand_type,
                    decision_applicable, afi_applicability, critical_failure_severity, critical_failure_type)
                   VALUES (%s, %s, 'pqi', %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (master_sha256, srt_id) DO NOTHING""",
                [(master["sha256"], s["srt_id"], s["primary_competency"], s["demand_type"],
                  s["decision_applicable"], s["afi_applicability"], s["critical_failure_severity"],
                  s.get("critical_failure_type")) for s in master["srts"]],
            )
        conn.commit()
    finally:
        conn.close()


def get_master(sha256: str) -> Optional[dict]:
    if not DATABASE_URL:
        return _memory_masters.get(sha256)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM srt_masters WHERE sha256 = %s", (sha256,))
            row = cur.fetchone()
            return _json_value(row[0]) if row else None
    finally:
        conn.close()


def increment_bank_administrations(sha256: str, srt_ids: List[str]) -> None:
    if not DATABASE_URL or not srt_ids:
        return
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE srt_bank SET administration_count = administration_count + 1
                   WHERE master_sha256 = %s AND srt_id = ANY(%s)""",
                (sha256, list(srt_ids)),
            )
        conn.commit()
    finally:
        conn.close()
