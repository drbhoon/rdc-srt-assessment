import asyncio
import os
import uuid
import logging
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict

import anthropic
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from models import CandidateInfo, ScoreRequest, FinalReportRequest, SubmitAllRequest
from question_bank import load_questions, get_session_questions
from scorer import score_question
from report_generator import generate_final_report
from pdf_generator import generate_pdf
from database import (
    init_db, create_session, get_session, update_session,
    delete_session as db_delete_session, list_sessions, reset_session,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App Setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="RDC SBCA Engine", version="4.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Config ──────────────────────────────────────────────────────────────────
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "rdc@admin2024")
EXCEL_PATH         = os.environ.get("EXCEL_PATH", str(Path(__file__).parent / "data" / "RDC_SRT_Master_100.xlsx"))
ASSESSMENT_MINUTES = int(os.environ.get("ASSESSMENT_MINUTES", "60"))

# ─── Globals ─────────────────────────────────────────────────────────────────
questions_db = load_questions(EXCEL_PATH)
client       = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# In-memory cache for active sessions (synced to DB on key events)
_cache: Dict[str, Any] = {}


@app.on_event("startup")
async def startup():
    init_db()


# ─── Page Routes ─────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/assessment")
async def assessment():
    return FileResponse(str(STATIC_DIR / "assessment.html"))

@app.get("/thank-you")
async def thank_you():
    return FileResponse(str(STATIC_DIR / "thank-you.html"))

@app.get("/admin")
async def admin():
    return FileResponse(str(STATIC_DIR / "admin.html"))

@app.get("/admin/report")
async def admin_report():
    return FileResponse(str(STATIC_DIR / "report.html"))

@app.get("/health")
async def health():
    return {"status": "ok", "questions_loaded": sum(len(v) for v in questions_db.values())}

# ─── API: Config ─────────────────────────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    return {"assessment_minutes": ASSESSMENT_MINUTES}

# ─── API: Admin Login ────────────────────────────────────────────────────────
@app.post("/api/admin/login")
async def admin_login(payload: dict):
    if payload.get("password") == ADMIN_PASSWORD:
        return {"success": True, "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401, detail="Invalid password")

# ─── Helper: get session from cache or DB ────────────────────────────────────
def _get(session_id: str) -> dict | None:
    if session_id in _cache:
        return _cache[session_id]
    session = get_session(session_id)
    if session:
        _cache[session_id] = session
    return session


# ─── API: Admin — List Sessions ──────────────────────────────────────────────
@app.get("/api/admin/sessions")
async def admin_list_sessions(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_sessions()

# ─── API: Admin — Get Report ─────────────────────────────────────────────────
@app.get("/api/admin/report/{session_id}")
async def get_report(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    report = session.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Report not yet generated")
    return {"candidate": session["candidate"], "report": report, "scores": session.get("scores", {})}

# ─── API: Admin — Delete Session ─────────────────────────────────────────────
@app.delete("/api/admin/session/{session_id}")
async def delete_session_endpoint(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _cache.pop(session_id, None)
    if not db_delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}

# ─── API: Admin — Quick Test ─────────────────────────────────────────────────
@app.post("/api/admin/quick-test")
async def admin_quick_test(
    payload: dict,
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(None),
):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    import datetime
    session_id = str(uuid.uuid4())
    candidate  = {
        "candidate_name":  payload.get("candidate_name",  "Test Candidate"),
        "plant_location":  payload.get("plant_location",  "Test Plant – Admin"),
        "assessment_date": payload.get("assessment_date",
                                       datetime.date.today().isoformat()),
    }
    questions = get_session_questions(questions_db, per_competency=3)

    dummy_answer = (
        "I would first assess the situation carefully by reviewing all available data "
        "and consulting with my team and relevant stakeholders. I would identify the "
        "root cause using a structured approach, implement preventive and corrective "
        "actions following RDC protocols, document everything, and ensure follow-up "
        "to prevent recurrence. Safety and operational discipline are my top priorities "
        "throughout this process."
    )
    dummy_answers = {q["srt_id"]: dummy_answer for q in questions}

    session = create_session(session_id, candidate, questions)
    session["collected_answers"] = dummy_answers
    session["status"] = "processing"
    _cache[session_id] = session
    update_session(session_id, status="processing", collected_answers=dummy_answers)

    background_tasks.add_task(process_assessment_async, session_id)
    logger.info("Admin quick-test session %s started (%d questions)", session_id, len(questions))
    return {"session_id": session_id, "status": "processing", "total": len(questions)}


# ─── API: Admin — Force Reset stuck session ──────────────────────────────────
@app.post("/api/admin/force-reset/{session_id}")
async def force_reset_endpoint(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    reset_session(session_id)
    _cache.pop(session_id, None)
    logger.info("Admin force-reset session %s", session_id)
    return {"reset": True, "session_id": session_id}


# ─── API: Admin — Rescore a single session from persisted transcripts ────────
@app.post("/api/admin/rescore/{session_id}")
async def rescore_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(None),
):
    """Re-run scoring + report + PDF against transcripts already in collected_answers.
    No candidate involvement needed — answers are loaded from Postgres.
    """
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    collected = session.get("collected_answers") or {}
    if not collected:
        raise HTTPException(
            status_code=400,
            detail="Session has no stored candidate answers — cannot rescore.",
        )

    # Wipe derived artifacts, preserve questions + collected_answers
    session["scores"]    = {}
    session["report"]    = None
    session["pdf_bytes"] = None
    session["pdf_error"] = None
    session["error"]     = None
    session["status"]    = "processing"
    session["progress"]  = 0
    _cache[session_id]   = session

    update_session(
        session_id,
        scores={},
        report=None,
        pdf_bytes=None,
        pdf_error=None,
        error=None,
        status="processing",
        progress=0,
    )

    background_tasks.add_task(process_assessment_async, session_id)
    logger.info("Admin rescore scheduled for session %s (%d answers)", session_id, len(collected))
    return {"status": "rescoring", "session_id": session_id, "answers": len(collected)}


# ─── API: Admin — Rescore all sessions with suspicious zero scores ───────────
@app.post("/api/admin/rescore-all-zeros")
async def rescore_all_zeros(
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(None),
):
    """Find completed sessions where any question has score=0 but the transcript is
    non-empty in collected_answers, and schedule a rescore for each.
    """
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    scheduled: list[str] = []
    skipped:   list[dict] = []

    for row in list_sessions():
        sid = row["session_id"]
        if row.get("status") != "completed":
            continue

        session = _get(sid)
        if not session:
            continue

        collected = session.get("collected_answers") or {}
        scores    = session.get("scores") or {}
        if not collected or not scores:
            skipped.append({"session_id": sid, "reason": "no transcripts or scores"})
            continue

        # Has at least one question scored 0 where transcript is non-empty?
        has_suspicious_zero = any(
            (sc.get("score", 0) == 0)
            and (collected.get(srt_id, "") or "").strip()
            for srt_id, sc in scores.items()
        )
        if not has_suspicious_zero:
            continue

        # Wipe derived artifacts
        session["scores"]    = {}
        session["report"]    = None
        session["pdf_bytes"] = None
        session["pdf_error"] = None
        session["error"]     = None
        session["status"]    = "processing"
        session["progress"]  = 0
        _cache[sid]          = session
        update_session(
            sid,
            scores={}, report=None, pdf_bytes=None, pdf_error=None,
            error=None, status="processing", progress=0,
        )

        background_tasks.add_task(process_assessment_async, sid)
        scheduled.append(sid)

    logger.info("Bulk rescore scheduled: %d sessions (skipped: %d)", len(scheduled), len(skipped))
    return {"scheduled": scheduled, "count": len(scheduled), "skipped": skipped}


# ─── API: Start Session ─────────────────────────────────────────────────────
@app.post("/api/start-session")
async def start_session(candidate: CandidateInfo):
    session_id = str(uuid.uuid4())
    questions  = get_session_questions(questions_db, per_competency=3)
    session    = create_session(session_id, candidate.model_dump(), questions)
    _cache[session_id] = session

    safe_questions = [
        {
            "question_number":      q["question_number"],
            "srt_id":               q["srt_id"],
            "primary_competency":   q["primary_competency"],
            "secondary_competency": q["secondary_competency"],
            "situation":            q["situation"],
        }
        for q in questions
    ]
    return {"session_id": session_id, "questions": safe_questions, "total_questions": len(questions)}

# ─── BACKGROUND TASK: Process entire assessment asynchronously ───────────────
async def process_assessment_async(session_id: str) -> None:
    """Score all questions + generate report in a background thread pool.
    Uses asyncio.to_thread so the synchronous Anthropic SDK never blocks the event loop.
    """
    session = _get(session_id)
    if not session:
        return

    try:
        questions         = session["questions"]
        collected_answers = session.get("collected_answers", {})
        total             = len(questions)

        logger.info("Starting background processing for session %s (%d questions)", session_id, total)

        # ── Step 1: Score every question ─────────────────────────────────────
        scores = session.get("scores", {})
        for i, q in enumerate(questions):
            srt_id     = q["srt_id"]
            transcript = collected_answers.get(srt_id, "").strip()

            try:
                result = await asyncio.to_thread(
                    score_question,
                    client=client,
                    srt_id=srt_id,
                    situation=q["situation"],
                    primary_competency=q["primary_competency"],
                    secondary_competency=q["secondary_competency"],
                    candidate_transcript=transcript,
                )
            except Exception as exc:
                logger.error("Scoring failed for %s: %s", srt_id, exc, exc_info=True)
                result = {
                    "srt_id": srt_id,
                    "total": 0,
                    "strengths": [],
                    "improvements": [f"Scoring error: {str(exc)[:80]}"],
                }

            scores[srt_id] = {
                "competency":   q["primary_competency"],
                "score":        int(result.get("total", 0)),
                "strengths":    result.get("strengths", []),
                "improvements": result.get("improvements", []),
                "details":      result,
            }
            session["scores"]   = scores
            session["progress"] = i + 1
            # Persist progress every 5 questions
            if (i + 1) % 5 == 0 or (i + 1) == total:
                update_session(session_id, scores=scores, progress=i + 1)
            logger.info("Scored %d/%d for session %s", i + 1, total, session_id)

        # ── Step 2: Build results array with transcripts for deep analysis ───
        results = []
        for q in questions:
            srt_id = q["srt_id"]
            if srt_id in scores:
                sc = scores[srt_id]
                results.append({
                    "competency":           sc["competency"],
                    "secondary_competency": q.get("secondary_competency", ""),
                    "situation":            q["situation"],
                    "transcript":           collected_answers.get(srt_id, ""),
                    "score":                sc["score"],
                    "strengths":            sc["strengths"],
                    "improvements":         sc["improvements"],
                })
            else:
                results.append({
                    "competency":           q["primary_competency"],
                    "secondary_competency": q.get("secondary_competency", ""),
                    "situation":            q["situation"],
                    "transcript":           "",
                    "score":                0,
                    "strengths":            [],
                    "improvements":         ["Not answered — counted as zero."],
                })

        # ── Step 2b: Compute all numeric fields in Python (authoritative) ────
        overall_score = sum(r["score"] for r in results)
        normalized_score = round((overall_score / 300) * 100, 1)

        comp_buckets: dict = defaultdict(list)
        for r in results:
            comp_buckets[r["competency"]].append(r["score"])
        python_competency_summary = {
            comp: round(sum(sc) / len(sc), 1)
            for comp, sc in comp_buckets.items()
        }
        logger.info(
            "Python-computed scores — total: %d/300 (%.1f%%)",
            overall_score, normalized_score,
        )

        # ── Step 3: Generate final report ────────────────────────────────────
        candidate = session["candidate"]
        try:
            report_data = await asyncio.to_thread(
                generate_final_report,
                client=client,
                candidate_name=candidate["candidate_name"],
                plant_location=candidate["plant_location"],
                assessment_date=candidate["assessment_date"],
                results=results,
            )
            # Override numeric fields with Python ground truth
            report_data["overall_score_out_of_300"]   = overall_score
            report_data["normalized_score_out_of_100"] = normalized_score
            report_data["competency_summary"]          = python_competency_summary

            # Attach verbatim transcript appendix for PDF Section 12
            report_data["transcript_appendix"] = [
                {
                    "question_number": i + 1,
                    "competency":      r["competency"],
                    "situation":       r["situation"],
                    "transcript":      r["transcript"],
                    "score":           r["score"],
                }
                for i, r in enumerate(results)
            ]

            session["report"] = report_data
            update_session(session_id, report=report_data)
            logger.info("Report generated for session %s", session_id)
        except Exception as exc:
            logger.error("Report generation failed for %s: %s", session_id, exc, exc_info=True)
            session["status"] = "failed"
            session["error"]  = str(exc)
            update_session(session_id, status="failed", error=str(exc))
            return

        # ── Step 4: Generate PDF ─────────────────────────────────────────────
        try:
            pdf_bytes = await asyncio.to_thread(generate_pdf, report_data=report_data, candidate=candidate)
            session["pdf_bytes"] = pdf_bytes
            update_session(session_id, pdf_bytes=pdf_bytes)
            logger.info("PDF generated (%d bytes) for session %s", len(pdf_bytes), session_id)
        except Exception as exc:
            logger.error("PDF failed for %s: %s", session_id, exc, exc_info=True)
            session["pdf_error"] = str(exc)
            update_session(session_id, pdf_error=str(exc))

        session["status"] = "completed"
        update_session(session_id, status="completed")
        logger.info("Session %s fully completed", session_id)

    except Exception as exc:
        logger.error("FATAL background task error for session %s: %s", session_id, exc, exc_info=True)
        session["status"] = "failed"
        session["error"]  = f"Unexpected error: {str(exc)}"
        update_session(session_id, status="failed", error=f"Unexpected error: {str(exc)}")


# ─── API: Submit All Answers ─────────────────────────────────────────────────
@app.post("/api/submit-all")
async def submit_all(req: SubmitAllRequest, background_tasks: BackgroundTasks):
    session = _get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("status") == "processing":
        return {"status": "already_processing"}

    session["collected_answers"] = req.answers
    session["status"]            = "processing"
    session["progress"]          = 0
    update_session(req.session_id, collected_answers=req.answers, status="processing", progress=0)

    background_tasks.add_task(process_assessment_async, req.session_id)
    logger.info("Submitted session %s with %d answers", req.session_id, len(req.answers))
    return {"status": "processing", "total": len(session["questions"])}


# ─── API: Poll submission status ─────────────────────────────────────────────
@app.get("/api/submission-status/{session_id}")
async def submission_status(session_id: str):
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "status":   session.get("status", "in_progress"),
        "progress": session.get("progress", 0),
        "total":    len(session.get("questions", [])),
        "error":    session.get("error"),
    }


# ─── API: Download PDF ──────────────────────────────────────────────────────
@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized — admin only")
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    pdf_bytes = session.get("pdf_bytes")
    if not pdf_bytes:
        detail = session.get("pdf_error", "PDF not ready yet")
        raise HTTPException(status_code=404, detail=detail)
    name = session["candidate"]["candidate_name"].replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="RDC_SBCA_{name}.pdf"'},
    )
