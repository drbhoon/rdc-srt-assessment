import os
import uuid
from pathlib import Path
from typing import Dict, Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from models import CandidateInfo, ScoreRequest, FinalReportRequest
from question_bank import load_questions, get_session_questions
from scorer import score_question
from report_generator import generate_final_report
from pdf_generator import generate_pdf

# ─── App Setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="RDC SRT Assessment Engine", version="2.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Globals ─────────────────────────────────────────────────────────────────
sessions: Dict[str, Any] = {}

EXCEL_PATH = os.environ.get("EXCEL_PATH", str(Path(__file__).parent / "data" / "RDC_SRT_Master_100.xlsx"))
questions_db = load_questions(EXCEL_PATH)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

PDF_DIR = Path("/tmp") if Path("/tmp").exists() else Path(".")

# ─── Page Routes ─────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/assessment")
async def assessment():
    return FileResponse(str(STATIC_DIR / "assessment.html"))

@app.get("/report")
async def report():
    return FileResponse(str(STATIC_DIR / "report.html"))

@app.get("/health")
async def health():
    return {"status": "ok", "questions_loaded": sum(len(v) for v in questions_db.values())}

# ─── API: Start Session ───────────────────────────────────────────────────────
@app.post("/api/start-session")
async def start_session(candidate: CandidateInfo):
    session_id = str(uuid.uuid4())
    questions = get_session_questions(questions_db, per_competency=3)

    sessions[session_id] = {
        "candidate": candidate.model_dump(),
        "questions": questions,
        "scores": {},
        "status": "in_progress",
    }

    safe_questions = [
        {
            "question_number": q["question_number"],
            "srt_id": q["srt_id"],
            "primary_competency": q["primary_competency"],
            "secondary_competency": q["secondary_competency"],
            "situation": q["situation"],
        }
        for q in questions
    ]

    return {
        "session_id": session_id,
        "questions": safe_questions,
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
        "competency": req.primary_competency,
        "score": result.get("total", 0),
        "strengths": result.get("strengths", []),
        "improvements": result.get("improvements", []),
        "details": result,
    }

    return result

# ─── API: Generate Final Report ───────────────────────────────────────────────
@app.post("/api/final-report")
async def final_report(req: FinalReportRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    candidate = session["candidate"]
    scores = session["scores"]
    questions = session["questions"]

    results = []
    for q in questions:
        srt_id = q["srt_id"]
        if srt_id in scores:
            results.append({
                "competency": scores[srt_id]["competency"],
                "score": scores[srt_id]["score"],
                "strengths": scores[srt_id]["strengths"],
                "improvements": scores[srt_id]["improvements"],
            })

    if not results:
        raise HTTPException(status_code=400, detail="No scored questions found for this session")

    report_data = generate_final_report(
        client=client,
        candidate_name=candidate["candidate_name"],
        plant_location=candidate["plant_location"],
        assessment_date=candidate["assessment_date"],
        results=results,
    )

    # Generate PDF
    pdf_path = str(PDF_DIR / f"report_{req.session_id}.pdf")
    try:
        generate_pdf(report_data=report_data, candidate=candidate, output_path=pdf_path)
        sessions[req.session_id]["pdf_path"] = pdf_path
    except Exception as e:
        sessions[req.session_id]["pdf_error"] = str(e)

    sessions[req.session_id]["report"] = report_data
    sessions[req.session_id]["status"] = "completed"

    return report_data

# ─── API: Download PDF ────────────────────────────────────────────────────────
@app.get("/api/download-pdf/{session_id}")
async def download_pdf(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    pdf_path = session.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF not ready. Please generate the report first.")

    candidate_name = session["candidate"]["candidate_name"].replace(" ", "_")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"RDC_SRT_Assessment_{candidate_name}.pdf",
    )
