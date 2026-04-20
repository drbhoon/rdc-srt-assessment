"""PostgreSQL persistence layer for RDC SRT Assessment sessions.

Uses psycopg2 directly with JSONB columns for complex data.
Falls back to in-memory dict if DATABASE_URL is not set.
"""

import json
import logging
import os
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS access_codes (
                    code        TEXT PRIMARY KEY,
                    label       TEXT,
                    max_uses    INTEGER DEFAULT 10,
                    used_count  INTEGER DEFAULT 0,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
        logger.info("PostgreSQL sessions + access_codes tables ready")
    finally:
        conn.close()


# ── CRUD Operations ──────────────────────────────────────────────────────────

def create_session(session_id: str, candidate: dict, questions: list) -> dict:
    """Insert a new session. Returns the session dict."""
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
                    questions, status, progress, scores)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    session_id,
                    candidate.get("candidate_name", ""),
                    candidate.get("plant_location", ""),
                    candidate.get("assessment_date", ""),
                    json.dumps(questions),
                    "in_progress",
                    0,
                    json.dumps({}),
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
                          report, pdf_data, pdf_error
                   FROM sessions WHERE session_id = %s""",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            return {
                "candidate": {
                    "candidate_name":  row[0],
                    "plant_location":  row[1],
                    "assessment_date": row[2],
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


def delete_session(session_id: str) -> bool:
    """Delete a session. Returns True if found and deleted."""
    if not DATABASE_URL:
        return _memory_store.pop(session_id, None) is not None

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE session_id = %s", (session_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_sessions() -> List[dict]:
    """Return summary of all sessions, sorted by date descending."""
    if not DATABASE_URL:
        result = []
        for sid, s in _memory_store.items():
            c      = s.get("candidate", {})
            report = s.get("report") or {}
            result.append({
                "session_id":         sid,
                "candidate_name":     c.get("candidate_name", ""),
                "plant_location":     c.get("plant_location", ""),
                "assessment_date":    c.get("assessment_date", ""),
                "status":             s.get("status", "in_progress"),
                "total_score":        report.get("overall_score_out_of_300"),
                "normalized":         report.get("normalized_score_out_of_100"),
                "readiness":          report.get("overall_readiness", "—"),
                "questions_answered": len(s.get("scores", {})),
                "has_pdf":            "pdf_bytes" in s,
                "error":              s.get("error"),
            })
        result.sort(key=lambda x: x["assessment_date"] or "", reverse=True)
        return result

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT session_id, candidate_name, plant_location, assessment_date,
                          status, scores, report, pdf_data IS NOT NULL, error
                   FROM sessions ORDER BY assessment_date DESC"""
            )
            result = []
            for row in cur.fetchall():
                scores = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
                report = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
                result.append({
                    "session_id":         row[0],
                    "candidate_name":     row[1],
                    "plant_location":     row[2],
                    "assessment_date":    row[3],
                    "status":             row[4],
                    "total_score":        (report or {}).get("overall_score_out_of_300"),
                    "normalized":         (report or {}).get("normalized_score_out_of_100"),
                    "readiness":          (report or {}).get("overall_readiness", "—"),
                    "questions_answered": len(scores),
                    "has_pdf":            row[7],
                    "error":              row[8],
                })
            return result
    finally:
        conn.close()


# ── Access Codes (10-digit HR-shared codes, up to 10 uses each) ──────────────

_memory_codes: Dict[str, dict] = {}


def create_access_code(code: str, label: str = "", max_uses: int = 10) -> dict:
    """Insert a new access code. Caller generates the 10-digit string."""
    record = {
        "code":       code,
        "label":      label or "",
        "max_uses":   max_uses,
        "used_count": 0,
    }

    if not DATABASE_URL:
        _memory_codes[code] = record
        return record

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO access_codes (code, label, max_uses, used_count)
                   VALUES (%s, %s, %s, 0)""",
                (code, label or "", max_uses),
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
                """SELECT code, label, max_uses, used_count, created_at
                   FROM access_codes WHERE code = %s""",
                (code,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "code":       row[0],
                "label":      row[1] or "",
                "max_uses":   row[2],
                "used_count": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
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
                   RETURNING code, label, max_uses, used_count""",
                (code,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return None
            return {
                "code":       row[0],
                "label":      row[1] or "",
                "max_uses":   row[2],
                "used_count": row[3],
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
                """SELECT code, label, max_uses, used_count, created_at
                   FROM access_codes ORDER BY created_at DESC"""
            )
            result = []
            for row in cur.fetchall():
                result.append({
                    "code":       row[0],
                    "label":      row[1] or "",
                    "max_uses":   row[2],
                    "used_count": row[3],
                    "created_at": row[4].isoformat() if row[4] else None,
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
