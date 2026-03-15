import asyncio
import os
import uuid
import logging
from pathlib import Path
from typing import Dict, Any

import anthropic
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from models import CandidateInfo, ScoreRequest, FinalReportRequest, SubmitAllRequest
from question_bank import load_questions, get_session_questions
from scorer import score_question
from report_generator import generate_final_report
from pdf_generator import generate_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App Setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="RDC SBCA Engine", version="3.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Config ──────────────────────────────────────────────────────────────────
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "rdc@admin2024")
EXCEL_PATH         = os.environ.get("EXCEL_PATH", str(Path(__file__).parent / "data" / "RDC_SRT_Master_100.xlsx"))
ASSESSMENT_MINUTES = int(os.environ.get("ASSESSMENT_MINUTES", "60"))

# ─── Globals ─────────────────────────────────────────────────────────────────
sessions:     Dict[str, Any] = {}
questions_db = load_questions(EXCEL_PATH)
client       = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

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

# ─── API: Admin — List Sessions ──────────────────────────────────────────────
@app.get("/api/admin/sessions")
async def list_sessions(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = []
    for sid, s in sessions.items():
        c      = s.get("candidate", {})
        report = s.get("report", {})
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
    result.sort(key=lambda x: x["assessment_date"], reverse=True)
    return result

# ─── API: Admin — Get Report ─────────────────────────────────────────────────
@app.get("/api/admin/report/{session_id}")
async def get_report(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    report = session.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="Report not yet generated")
    return {"candidate": session["candidate"], "report": report, "scores": session.get("scores", {})}

# ─── API: Admin — Delete Session ─────────────────────────────────────────────
@app.delete("/api/admin/session/{session_id}")
async def delete_session(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del sessions[session_id]
    return {"deleted": True}

# ─── API: Admin — Quick Test (creates dummy session without answering questions)
@app.post("/api/admin/quick-test")
async def admin_quick_test(
    payload: dict,
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(None),
):
    """Admin-only: spin up a test session with auto-generated dummy answers.
    Use this to verify the scoring + report pipeline without sitting an assessment.
    """
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

    # Generic answer that will produce non-zero scores from Claude
    dummy_answer = (
        "I would first assess the situation carefully by reviewing all available data "
        "and consulting with my team and relevant stakeholders. I would identify the "
        "root cause using a structured approach, implement preventive and corrective "
        "actions following RDC protocols, document everything, and ensure follow-up "
        "to prevent recurrence. Safety and operational discipline are my top priorities "
        "throughout this process."
    )
    dummy_answers = {q["srt_id"]: dummy_answer for q in questions}

    sessions[session_id] = {
        "candidate":          candidate,
        "questions":          questions,
        "scores":             {},
        "status":             "processing",
        "progress":           0,
        "collected_answers":  dummy_answers,
    }

    background_tasks.add_task(process_assessment_async, session_id)
    logger.info("Admin quick-test session %s started (%d questions)", session_id, len(questions))
    return {"session_id": session_id, "status": "processing", "total": len(questions)}


# ─── API: Admin — Force Reset stuck session ───────────────────────────────────
@app.post("/api/admin/force-reset/{session_id}")
async def force_reset_session(session_id: str, x_admin_token: str = Header(None)):
    """Reset a stuck / failed session back to in_progress so the candidate can resubmit."""
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session["status"]   = "in_progress"
    session["progress"] = 0
    session.pop("error",             None)
    session.pop("report",            None)
    session.pop("pdf_bytes",         None)
    session.pop("pdf_error",         None)
    session.pop("collected_answers", None)
    session["scores"] = {}
    logger.info("Admin force-reset session %s", session_id)
    return {"reset": True, "session_id": session_id}


# ─── API: Start Session ───────────────────────────────────────────────────────
@app.post("/api/start-session")
async def start_session(candidate: CandidateInfo):
    session_id = str(uuid.uuid4())
    questions  = get_session_questions(questions_db, per_competency=3)
    sessions[session_id] = {
        "candidate": candidate.model_dump(),
        "questions": questions,
        "scores":    {},
        "status":    "in_progress",
        "progress":  0,
    }
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

# ─── BACKGROUND TASK: Process entire assessment asynchronously ────────────────
async def process_assessment_async(session_id: str) -> None:
    """Score all questions + generate report in a background thread pool.
    Uses asyncio.to_thread so the synchronous Anthropic SDK never blocks the event loop.
    A master try/except guarantees session["status"] is always updated — no silent hangs.
    """
    session = sessions.get(session_id)
    if not session:
        return

    try:  # ← MASTER GUARD: ensures status is always set even on unexpected crash
        questions         = session["questions"]
        collected_answers = session.get("collected_answers", {})
        total             = len(questions)

        logger.info("Starting background processing for session %s (%d questions)", session_id, total)

        # ── Step 1: Score every question ──────────────────────────────────────
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

            session["scores"][srt_id] = {
                "competency":   q["primary_competency"],
                "score":        int(result.get("total", 0)),
                "strengths":    result.get("strengths", []),
                "improvements": result.get("improvements", []),
                "details":      result,
            }
            session["progress"] = i + 1
            logger.info("Scored %d/%d for session %s", i + 1, total, session_id)

        # ── Step 2: Build results array (unanswered = 0) ──────────────────────
        results = []
        for q in questions:
            srt_id = q["srt_id"]
            if srt_id in session["scores"]:
                sc = session["scores"][srt_id]
                results.append({
                    "competency":   sc["competency"],
                    "score":        sc["score"],
                    "strengths":    sc["strengths"],
                    "improvements": sc["improvements"],
                })
            else:
                results.append({
                    "competency":   q["primary_competency"],
                    "score":        0,
                    "strengths":    [],
                    "improvements": ["Not answered — counted as zero."],
                })

        # ── Step 3: Generate final report ─────────────────────────────────────
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
            session["report"] = report_data
            logger.info("Report generated for session %s", session_id)
        except Exception as exc:
            logger.error("Report generation failed for %s: %s", session_id, exc, exc_info=True)
            session["status"] = "failed"
            session["error"]  = str(exc)
            return

        # ── Step 4: Generate PDF ───────────────────────────────────────────────
        try:
            pdf_bytes = await asyncio.to_thread(generate_pdf, report_data=report_data, candidate=candidate)
            session["pdf_bytes"] = pdf_bytes
            logger.info("PDF generated (%d bytes) for session %s", len(pdf_bytes), session_id)
        except Exception as exc:
            logger.error("PDF failed for %s: %s", session_id, exc, exc_info=True)
            session["pdf_error"] = str(exc)

        session["status"] = "completed"
        logger.info("Session %s fully completed", session_id)

    except Exception as exc:  # ← catches anything the inner handlers missed
        logger.error("FATAL background task error for session %s: %s", session_id, exc, exc_info=True)
        session["status"] = "failed"
        session["error"]  = f"Unexpected error: {str(exc)}"


# ─── API: Submit All Answers (new primary submit endpoint) ────────────────────
@app.post("/api/submit-all")
async def submit_all(req: SubmitAllRequest, background_tasks: BackgroundTasks):
    """Accept all answers at once, return immediately, process in background."""
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("status") == "processing":
        return {"status": "already_processing"}

    session["collected_answers"] = req.answers
    session["status"]            = "processing"
    session["progress"]          = 0

    background_tasks.add_task(process_assessment_async, req.session_id)
    logger.info("Submitted session %s with %d answers", req.session_id, len(req.answers))
    return {"status": "processing", "total": len(session["questions"])}


# ─── API: Poll submission status ─────────────────────────────────────────────
@app.get("/api/submission-status/{session_id}")
async def submission_status(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "status":   session.get("status", "in_progress"),
        "progress": session.get("progress", 0),
        "total":    len(session.get("questions", [])),
        "error":    session.get("error"),
    }


# ─── API: Download PDF ────────────────────────────────────────────────────────
@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized — admin only")
    session = sessions.get(session_id)
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
