import asyncio
import os
import random
import uuid
import logging
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict

import anthropic
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from models import (
    CandidateInfo, ScoreRequest, FinalReportRequest, SubmitAllRequest,
    AccessCodeValidate, AccessCodeGenerate,
)
from question_bank import load_questions, get_session_questions
from scorer import score_question
from report_generator import generate_final_report
from pdf_generator import generate_pdf
from database import (
    init_db, create_session, get_session, update_session,
    delete_session as db_delete_session, list_sessions, reset_session,
    create_access_code, get_access_code, consume_access_code,
    list_access_codes, delete_access_code,
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

# Global pipeline concurrency cap. Scoring 30 questions per assessment =
# 30 Anthropic API calls. With N candidates submitting in parallel, we end
# up with ~N concurrent calls slamming the rate limit. Cap=2 keeps us under
# Haiku tier-1's 50 RPM (2 pipelines × ~30 calls per 90s ≈ 40 RPM).
# Applies to BOTH live candidate submissions and admin rescore requests.
_PIPELINE_MAX_CONCURRENT = 2
_pipeline_semaphore: asyncio.Semaphore | None = None


def _get_pipeline_semaphore() -> asyncio.Semaphore:
    """Lazily instantiate the semaphore on first use so it binds to the live event loop."""
    global _pipeline_semaphore
    if _pipeline_semaphore is None:
        _pipeline_semaphore = asyncio.Semaphore(_PIPELINE_MAX_CONCURRENT)
    return _pipeline_semaphore


async def _pipeline_guarded(session_id: str, source: str = "submit") -> None:
    """Run the scoring + report pipeline with global concurrency cap.
    Used for live submissions AND admin rescores — a single queue prevents
    a rush of live candidates from starving the Anthropic rate limit.
    """
    sem = _get_pipeline_semaphore()
    async with sem:
        logger.info(
            "Pipeline semaphore acquired for %s (source=%s, cap=%d)",
            session_id, source, _PIPELINE_MAX_CONCURRENT,
        )
        await process_assessment_async(session_id)


# Back-compat alias — existing call sites still reference _rescore_guarded.
async def _rescore_guarded(session_id: str) -> None:
    await _pipeline_guarded(session_id, source="rescore")


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

    # v4.15: SMART RESUME. Don't wipe scores — preserve them so the
    # pipeline can skip already-scored questions and only retry errors
    # / missing ones. Saksham 20/30 case → next Rescore only does 10
    # API calls instead of 30. Wipe only the derived artifacts (report,
    # pdf) that need regeneration after any score change.
    existing_scores = session.get("scores") or {}
    session["report"]    = None
    session["pdf_bytes"] = None
    session["pdf_error"] = None
    session["error"]     = None
    session["status"]    = "processing"
    # Pre-set progress to count of valid prior scores (resume signal in UI)
    valid_prior = sum(
        1 for v in existing_scores.values()
        if (v or {}).get("score", 0) > 0 or
           "Question not answered" in str(((v or {}).get("improvements") or [""])[0])
    )
    session["progress"]  = valid_prior
    _cache[session_id]   = session

    update_session(
        session_id,
        report=None,
        pdf_bytes=None,
        pdf_error=None,
        error=None,
        status="processing",
        progress=valid_prior,
    )

    background_tasks.add_task(_pipeline_guarded, session_id, "rescore")
    logger.info(
        "Admin rescore scheduled for session %s (%d answers, %d valid prior scores preserved, "
        "queued behind pipeline semaphore cap=%d)",
        session_id, len(collected), valid_prior, _PIPELINE_MAX_CONCURRENT,
    )
    return {
        "status":           "rescoring",
        "session_id":       session_id,
        "answers":          len(collected),
        "resumed_scores":   valid_prior,
        "questions_to_run": 30 - valid_prior,
    }


# ─── API: Admin — Bulk rescore all stuck sessions (30/30 but not completed) ──
@app.post("/api/admin/rescore-stuck")
async def rescore_stuck_sessions(
    background_tasks: BackgroundTasks,
    x_admin_token: str = Header(None),
):
    """Rescue path for a batch that got stuck in 'In Progress' state despite
    having all 30 answers in the database. Schedules a rescore for each
    eligible session — the pipeline semaphore serializes them cleanly.
    """
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    summary = list_sessions()
    scheduled: list[str] = []
    skipped:   list[dict] = []

    for s in summary:
        sid          = s["session_id"]
        is_done      = s.get("status") == "completed"
        # Any non-completed session with AT LEAST ONE stored transcript is
        # eligible. Partial submits (e.g. 21/30) are still rescorable — the
        # scorer handles empty answers by returning 0 immediately without an
        # API call. Without this, a candidate who submitted partially (like
        # Sateshwar) would be permanently stuck because the UI had no way to
        # kick off their rescore.
        has_any_answers = (s.get("collected_count", 0) > 0)
        eligible     = (not is_done) and has_any_answers
        if not eligible:
            continue

        full = _get(sid)
        if not full:
            skipped.append({"session_id": sid, "reason": "not found"})
            continue
        collected = full.get("collected_answers") or {}
        if not collected:
            skipped.append({"session_id": sid, "reason": "no stored answers"})
            continue

        # v4.15: SMART RESUME — preserve prior scores. Pipeline skips
        # already-validly-scored questions, only retries errors / missing.
        existing = full.get("scores") or {}
        valid_prior = sum(
            1 for v in existing.values()
            if (v or {}).get("score", 0) > 0 or
               "Question not answered" in str(((v or {}).get("improvements") or [""])[0])
        )
        full["report"]    = None
        full["pdf_bytes"] = None
        full["pdf_error"] = None
        full["error"]     = None
        full["status"]    = "processing"
        full["progress"]  = valid_prior
        _cache[sid]       = full

        update_session(
            sid,
            report=None,
            pdf_bytes=None,
            pdf_error=None,
            error=None,
            status="processing",
            progress=valid_prior,
        )
        background_tasks.add_task(_pipeline_guarded, sid, "rescore-stuck")
        scheduled.append({"session_id": sid, "resumed_scores": valid_prior})

    logger.info(
        "Admin bulk-rescore of stuck sessions: %d scheduled, %d skipped "
        "(semaphore cap=%d)",
        len(scheduled), len(skipped), _PIPELINE_MAX_CONCURRENT,
    )
    return {
        "scheduled":    len(scheduled),
        "session_ids":  scheduled,
        "skipped":      skipped,
        "concurrency":  _PIPELINE_MAX_CONCURRENT,
    }




# ─── API: Admin — Diagnose a session (why is it stuck?) ──────────────────────
@app.get("/api/admin/diagnose/{session_id}")
async def diagnose_session(session_id: str, x_admin_token: str = Header(None)):
    """Return granular session state so admin can see WHY a session isn't
    flipping to 'completed'. Use this when a session is stuck 'In Progress'
    and you want to know: is it genuinely processing? Did scoring complete?
    Did report gen fail? Is the error field populated?
    """
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    collected = session.get("collected_answers") or {}
    scores    = session.get("scores") or {}
    report    = session.get("report")
    pdf_bytes = session.get("pdf_bytes")

    answered_nonempty = sum(1 for v in collected.values() if (v or "").strip())

    # v4.15: Decompose the scores dict into resume-relevant buckets.
    #   • valid_scored = real scores from a successful API call (score > 0)
    #   • legit_zeros  = score=0 because transcript was empty (legitimate)
    #   • error_zeros  = score=0 because the API call failed (will retry on Rescore)
    # The next Rescore re-runs ONLY error_zeros + missing questions.
    valid_scored = 0
    legit_zeros  = 0
    error_zeros  = 0
    scoring_errors_list = []
    for srt_id, v in scores.items():
        score = (v or {}).get("score", 0)
        imps  = (v or {}).get("improvements") or []
        first_imp = str(imps[0]) if imps else ""
        if score > 0:
            valid_scored += 1
        elif "Question not answered" in first_imp:
            legit_zeros += 1
        else:
            # score=0 with non-empty failure reason → error_zero
            error_zeros += 1
            if "Scoring error" in first_imp or "BadRequest" in first_imp or "after" in first_imp:
                scoring_errors_list.append({"srt_id": srt_id, "improvements": imps})

    questions_to_rerun = error_zeros + max(0, len(scores) and (30 - len(scores)))
    missing_questions  = max(0, 30 - len(scores))

    return {
        "session_id":          session_id,
        "candidate_name":      (session.get("candidate") or {}).get("candidate_name"),
        "status":              session.get("status"),
        "progress":            session.get("progress"),
        "error":               session.get("error"),
        "pdf_error":           session.get("pdf_error"),
        "collected_count":     len(collected),
        "answered_nonempty":   answered_nonempty,
        "scored_count":        len(scores),

        # ── New v4.15 resume-aware fields ─────────────────────────────────
        "valid_scored":         valid_scored,        # real scores, will be preserved
        "legit_zeros":          legit_zeros,         # empty-transcript zeros, preserved
        "error_zeros":          error_zeros,         # API-failed zeros, will retry
        "missing_questions":    missing_questions,   # never attempted, will score
        "next_rescore_runs":    error_zeros + missing_questions,  # actual API calls on Rescore

        # Legacy alias kept for backward compat with older admin.html
        "scored_zeros":        legit_zeros + error_zeros,
        "scoring_error_count": len(scoring_errors_list),
        "scoring_errors":      scoring_errors_list[:5],

        "has_report":          bool(report),
        "has_pdf":             bool(pdf_bytes),
        "pdf_bytes_len":       len(pdf_bytes) if pdf_bytes else 0,
        "pipeline_concurrency": _PIPELINE_MAX_CONCURRENT,
        "scorer_model":        __import__("scorer").SCORER_MODEL,
        "scorer_max_tokens":   __import__("scorer").SCORER_MAX_TOKENS,
        "report_model":        __import__("report_generator").REPORT_MODEL,
        "report_max_tokens":   __import__("report_generator").REPORT_MAX_TOKENS,
    }


# ─── API: Admin — Force Reset a stuck 'processing' session ───────────────────
@app.post("/api/admin/force-reset-processing/{session_id}")
async def force_reset_processing(session_id: str, x_admin_token: str = Header(None)):
    """Clear the 'processing' lock on a stuck session WITHOUT wiping transcripts.
    Use this ONLY if a session is stuck in 'processing' status (container was
    killed, task never finished) and you want to flip it to 'failed' so the
    Rescore button can then re-run the pipeline cleanly. Preserves
    collected_answers so the rescore path still works.

    v4.12: Non-destructive for existing errors. If the session is already
    status='failed' with a real pipeline error message, we preserve that
    error — earlier versions bulldozed real failure reasons on every admin
    click, destroying diagnostic information the next time Diagnose was run.
    """
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    current_status = session.get("status")

    # No-op + reject if the session isn't actually stuck. Prevents admins from
    # accidentally stomping on a real error by clicking this after a failure.
    if current_status != "processing":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Session status is '{current_status}', not 'processing' — "
                f"Force Reset does nothing here. Click Rescore directly instead."
            ),
        )

    stock_msg = "Stuck in processing — manually reset by admin. Click Rescore to retry."
    session["status"] = "failed"
    session["error"]  = stock_msg
    _cache[session_id] = session
    update_session(session_id, status="failed", error=stock_msg)
    logger.info("Admin cleared stuck 'processing' lock on session %s", session_id)
    return {"status": "failed", "session_id": session_id}


# ─── API: Admin — Generate Access Code ───────────────────────────────────────
def _fresh_access_code(max_tries: int = 6) -> str:
    """Produce a 10-digit numeric code that doesn't clash with an existing one."""
    for _ in range(max_tries):
        candidate_code = f"{random.randint(10**9, 10**10 - 1)}"
        if not get_access_code(candidate_code):
            return candidate_code
    # Extremely unlikely fallback — just return a candidate; collision risk ~1 in 9B
    return f"{random.randint(10**9, 10**10 - 1)}"


@app.post("/api/admin/generate-code")
async def admin_generate_code(
    payload: AccessCodeGenerate,
    x_admin_token: str = Header(None),
):
    """Generate a fresh 10-digit access code (max 10 uses by default)."""
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    max_uses = int(payload.max_uses or 10)
    if max_uses < 1 or max_uses > 100:
        raise HTTPException(status_code=400, detail="max_uses must be between 1 and 100")

    code   = _fresh_access_code()
    record = create_access_code(code, label=payload.label or "", max_uses=max_uses)
    logger.info("Admin generated new access code %s (max_uses=%d, label=%r)",
                code, max_uses, payload.label or "")
    return record


@app.get("/api/admin/access-codes")
async def admin_list_codes(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_access_codes()


@app.delete("/api/admin/access-code/{code}")
async def admin_delete_code(code: str, x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not delete_access_code(code):
        raise HTTPException(status_code=404, detail="Code not found")
    return {"deleted": True, "code": code}


# ─── API: Candidate — Validate Access Code (read-only check) ─────────────────
@app.post("/api/validate-code")
async def validate_code(payload: AccessCodeValidate):
    """Public endpoint — checks code exists and has uses remaining.
    Does NOT consume the code (that happens at start-session).
    """
    code = (payload.code or "").strip()
    if not code or not code.isdigit() or len(code) != 10:
        raise HTTPException(status_code=400, detail="Access code must be exactly 10 digits.")

    record = get_access_code(code)
    if not record:
        raise HTTPException(status_code=404, detail="Invalid access code. Please check with HR.")
    if record["used_count"] >= record["max_uses"]:
        raise HTTPException(
            status_code=410,
            detail=f"This access code has been fully used ({record['used_count']}/{record['max_uses']}). Please request a new one from HR.",
        )
    return {
        "valid":      True,
        "max_uses":   record["max_uses"],
        "used_count": record["used_count"],
        "remaining":  record["max_uses"] - record["used_count"],
    }


# ─── API: Start Session ─────────────────────────────────────────────────────
@app.post("/api/start-session")
async def start_session(candidate: CandidateInfo):
    # Validate + atomically consume the access code
    code = (candidate.access_code or "").strip()
    if not code or not code.isdigit() or len(code) != 10:
        raise HTTPException(status_code=400, detail="A valid 10-digit access code is required.")

    consumed = consume_access_code(code)
    if not consumed:
        # Either code doesn't exist or it's exhausted — distinguish for UX
        rec = get_access_code(code)
        if not rec:
            raise HTTPException(status_code=404, detail="Invalid access code. Please check with HR.")
        raise HTTPException(
            status_code=410,
            detail=f"This access code has been fully used ({rec['used_count']}/{rec['max_uses']}). Please request a new one from HR.",
        )
    logger.info(
        "Access code %s consumed (%d/%d used)",
        code, consumed["used_count"], consumed["max_uses"],
    )

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

        # ── Step 1: Score every question (RESUME-AWARE) ─────────────────────
        # v4.15: Preserve valid prior scores across rescore attempts. The
        # decision tree per question:
        #   • Has prior score > 0          → keep, skip API call (RESUMED)
        #   • Has prior "Question not answered" zero → keep, skip (LEGIT EMPTY)
        #   • Has prior "Scoring error..." zero      → re-score (RETRY)
        #   • No prior entry at all                  → score fresh (NEW)
        # This makes Rescore safe to retry: a pipeline killed at q20
        # only re-runs q21-30 instead of starting over. 3× less rate
        # limit pressure, 3× less wall time, 3× less chance to die again.
        scores = session.get("scores", {})
        resumed_count = 0
        retry_count   = 0
        new_count     = 0
        for i, q in enumerate(questions):
            srt_id     = q["srt_id"]
            transcript = collected_answers.get(srt_id, "").strip()

            # ── Resume guard: skip questions that already have valid results
            prior = scores.get(srt_id) or {}
            prior_score = int(prior.get("score", 0)) if prior else 0
            prior_imp   = (prior.get("improvements") or [""])[0] if prior else ""
            is_valid_score = (prior_score > 0) or (
                prior_score == 0 and "Question not answered" in str(prior_imp)
            )
            if is_valid_score:
                resumed_count += 1
                session["progress"] = i + 1
                logger.info(
                    "Resume: skipping %s (prior score=%d) for session %s",
                    srt_id, prior_score, session_id,
                )
                continue

            # ── Need to (re-)score this question
            if prior:  # had a prior entry but it was an error → retry
                retry_count += 1
            else:
                new_count += 1

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

        logger.info(
            "Scoring loop done for %s: %d resumed, %d retried (was error), %d new",
            session_id, resumed_count, retry_count, new_count,
        )

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

            # ── Readiness floor (3-tier rule) ────────────────────────────────
            # Below 50% normalized → force "Not ready yet" regardless of what
            # Claude concluded. Above 50%, keep Claude's holistic judgment
            # (Ready for higher responsibility / Ready with structured support).
            claude_readiness = (report_data.get("overall_readiness") or "").strip()
            if normalized_score < 50:
                report_data["overall_readiness"] = "Not ready yet"
                if claude_readiness.lower() not in ("not ready yet", "not yet ready"):
                    logger.info(
                        "Readiness floor applied for %s: %.1f%% < 50%% — "
                        "overriding '%s' → 'Not ready yet'",
                        session_id, normalized_score, claude_readiness,
                    )

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

    # Route through the shared pipeline semaphore so 11 candidates hitting
    # Submit simultaneously don't stampede the Anthropic rate limit and
    # kill each other's jobs. Excess submissions queue politely.
    background_tasks.add_task(_pipeline_guarded, req.session_id, "submit")
    logger.info(
        "Submitted session %s with %d answers (queued behind pipeline semaphore cap=%d)",
        req.session_id, len(req.answers), _PIPELINE_MAX_CONCURRENT,
    )
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
