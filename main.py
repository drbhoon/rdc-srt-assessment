import os
import uuid
import logging
from pathlib import Path
from typing import Dict, Any

import anthropic
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response

from models import CandidateInfo, ScoreRequest, FinalReportRequest
from question_bank import load_questions, get_session_questions
from scorer import score_question
from report_generator import generate_final_report
from pdf_generator import generate_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── App Setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="RDC SRT Assessment Engine", version="2.1")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Config ──────────────────────────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "rdc@admin2024")
EXCEL_PATH     = os.environ.get("EXCEL_PATH", str(Path(__file__).parent / "data" / "RDC_SRT_Master_100.xlsx"))
ASSESSMENT_MINUTES = int(os.environ.get("ASSESSMENT_MINUTES", "60"))

# ─── Globals ─────────────────────────────────────────────────────────────────
sessions: Dict[str, Any] = {}
questions_db = load_questions(EXCEL_PATH)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

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
        c = s.get("candidate", {})
        report = s.get("report", {})
        result.append({
            "session_id": sid,
            "candidate_name":  c.get("candidate_name", ""),
            "plant_location":  c.get("plant_location", ""),
            "assessment_date": c.get("assessment_date", ""),
            "status":          s.get("status", "in_progress"),
            "total_score":     report.get("overall_score_out_of_300", "—"),
            "normalized":      report.get("normalized_score_out_of_100", "—"),
            "readiness":       report.get("overall_readiness", "—"),
            "questions_answered": len(s.get("scores", {})),
            "has_pdf":         "pdf_bytes" in s,
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
    return {
        "candidate": session["candidate"],
        "report":    report,
        "scores":    session.get("scores", {}),
    }

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
    }

    # Return questions WITHOUT competency labels — hide from candidate
    safe_questions = [
        {
            "question_number":     q["question_number"],
            "srt_id":              q["srt_id"],
            "primary_competency":  q["primary_competency"],   # needed for scoring
            "secondary_competency":q["secondary_competency"], # needed for scoring
            "situation":           q["situation"],
        }
        for q in questions
    ]
    return {
        "session_id":      session_id,
        "questions":       safe_questions,
        "total_questions": len(questions),
    }

# ─── API: Score One Question ──────────────────────────────────────────────────
@app.post("/api/score-question")
async def score_one(req: ScoreRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = score_question(
        client=client,
        srt_id=req.srt_id,
        situation=req.situation,
        primary_competency=req.primary_competency,
        secondary_competency=req.secondary_competency,
        candidate_transcript=req.candidate_transcript,
    )
    sessions[req.session_id]["scores"][req.srt_id] = {
        "competency":   req.primary_competency,
        "score":        result.get("total", 0),
        "strengths":    result.get("strengths", []),
        "improvements": result.get("improvements", []),
        "details":      result,
    }
    return result

# ─── API: Generate Final Report ──────────────────────────────────────────────
@app.post("/api/final-report")
async def final_report(req: FinalReportRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    candidate = session["candidate"]
    scores    = session["scores"]
    questions = session["questions"]

    # Build results — include ALL 30 questions (unanswered = score 0)
    results = []
    for q in questions:
        srt_id = q["srt_id"]
        if srt_id in scores:
            results.append({
                "competency":   scores[srt_id]["competency"],
                "score":        scores[srt_id]["score"],
                "strengths":    scores[srt_id]["strengths"],
                "improvements": scores[srt_id]["improvements"],
            })
        else:
            # Unanswered — counts as 0
            results.append({
                "competency":   q["primary_competency"],
                "score":        0,
                "strengths":    [],
                "improvements": ["Not answered — counted as zero."],
            })

    report_data = generate_final_report(
        client=client,
        candidate_name=candidate["candidate_name"],
        plant_location=candidate["plant_location"],
        assessment_date=candidate["assessment_date"],
        results=results,
    )

    # Generate PDF and store as bytes in memory
    try:
        pdf_bytes = generate_pdf(report_data=report_data, candidate=candidate)
        sessions[req.session_id]["pdf_bytes"] = pdf_bytes
        logger.info("PDF generated (%d bytes) for session %s", len(pdf_bytes), req.session_id)
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        sessions[req.session_id]["pdf_error"] = str(e)

    sessions[req.session_id]["report"] = report_data
    sessions[req.session_id]["status"] = "completed"

    return report_data

# ─── API: Admin — Delete Session ─────────────────────────────────────────────
@app.delete("/api/admin/session/{session_id}")
async def delete_session(session_id: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    del sessions[session_id]
    return {"deleted": True}

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
        pdf_error = session.get("pdf_error", "PDF not generated yet")
        raise HTTPException(status_code=404, detail=f"PDF not available: {pdf_error}")

    candidate_name = session["candidate"]["candidate_name"].replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="RDC_SRT_{candidate_name}.pdf"'},
    )
