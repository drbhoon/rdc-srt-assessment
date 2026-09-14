"""PQI orchestration: the live master, starting and resuming attempts, and the scoring pipeline.

main.py owns HTTP; this module owns what a PQI attempt is. Plant Manager never
comes through here — process_assessment_async hands a session over only when
its assessment_type is "pqi".

Pipeline, following the master's AI_Evaluation_Sequence:
  1–10  first-pass evaluation of every administered SRT (blank answers score 0
        without a call), with the lift, caps and AFI rules applied in code
  11    a consistency check across the candidate's answers
  12    a neutral second review for confirmed Critical Failures, material
        contradictions, or an Overall within the master's proximity of a
        readiness boundary — the reviewer is never told the total or the band
  then  headline scores, readiness and guardrails (pqi_scoring), the narrative,
        and the PDF

Evaluations persist as they complete, so Rescore resumes from where a run
stopped. Every evaluation — first pass, review, error — is also appended to
srt_evaluations as an audit trail that is never rewritten.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import database
import pqi_evaluator as evaluator
import pqi_scoring as scoring
from assessment_types import PQI
from pqi_generator import generate_form
from pqi_master import load_master, srt_index
from pqi_pdf import generate_pqi_pdf

logger = logging.getLogger(__name__)

PQI_MASTER_PATH = os.environ.get(
    "PQI_MASTER_PATH",
    str(Path(__file__).parent / "data" / "RDC_PQI_Master_100_AI_Scoring_v1.0_PILOT.xlsx"),
)
EVAL_CONCURRENCY = max(1, int(os.environ.get("PQI_EVAL_CONCURRENCY", "3")))
# An answer still in flight when the clock runs out is accepted for this long.
SAVE_GRACE_SECONDS = 120
# A submission this long after the deadline uses only what was saved in time.
SUBMIT_GRACE_SECONDS = 300
# An attempt left open this long past its deadline is submitted with its saved answers.
EXPIRED_SUBMIT_AFTER_MINUTES = int(os.environ.get("PQI_EXPIRED_SUBMIT_AFTER_MINUTES", "10"))
MAX_ANSWER_CHARS = 20000
NOT_ANSWERED = "Question not answered — counted as zero."


class PqiUnavailable(Exception):
    pass


class AnswerRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ── Time ─────────────────────────────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def remaining_seconds(session, now: datetime | None = None) -> int:
    deadline = parse_iso(session.get("deadline_at"))
    if not deadline:
        return 0
    return max(0, int((deadline - (now or utcnow())).total_seconds()))


# ── The live master ──────────────────────────────────────────────────────────
_state = {"master": None, "error": "not loaded", "path": PQI_MASTER_PATH}
_masters_by_sha: dict = {}
_prompt_versions: dict = {}


def load_current_master(path: str | None = None):
    path = path or PQI_MASTER_PATH
    try:
        master = load_master(path)
    except Exception as exc:  # fail closed: PQI off, Plant Manager unaffected
        _state.update(master=None, error=str(exc), path=path)
        logger.error("PQI master NOT loaded — PQI assessments are unavailable: %s", exc)
        return None
    _state.update(master=master, error=None, path=path)
    _masters_by_sha[master["sha256"]] = master
    logger.info("PQI master loaded: %s %s (%s), sha256 %s", master["file_name"], master["version"],
                master["status"], master["sha256"])
    for warning in master["warnings"]:
        logger.warning("PQI master: %s", warning)
    return master


def current_master() -> dict:
    if _state["master"] is None:
        raise PqiUnavailable(_state["error"] or "PQI master not loaded")
    return _state["master"]


def master_status() -> dict:
    master = _state["master"]
    if not master:
        return {"available": False, "error": _state["error"], "path": Path(_state["path"]).name}
    return {
        "available": True, "file_name": master["file_name"], "sha256": master["sha256"],
        "version": master["version"], "rubric_version": master["rubric_version"], "status": master["status"],
        "effective_date": master["effective_date"], "srts": len(master["srts"]),
        "competencies": len(master["competencies"]), "has_critical_failure_type": master["has_failure_type"],
        "warnings": master["warnings"], "evaluator_model": evaluator.PQI_EVALUATOR_MODEL,
        "report_model": evaluator.PQI_REPORT_MODEL, "prompt_version": prompt_version_for(master),
    }


def register_current_master():
    if _state["master"]:
        database.register_master(_state["master"])


def master_for_session(session) -> dict:
    sha = session.get("master_sha256")
    if not sha:
        raise PqiUnavailable("this attempt records no master")
    if sha not in _masters_by_sha:
        stored = database.get_master(sha)
        if not stored:
            raise PqiUnavailable(f"master {session.get('master_version')} ({sha[:12]}) is not available on this server")
        _masters_by_sha[sha] = stored
    return _masters_by_sha[sha]


def prompt_version_for(master) -> str:
    if master["sha256"] not in _prompt_versions:
        _prompt_versions[master["sha256"]] = evaluator.prompt_version(master)
    return _prompt_versions[master["sha256"]]


# ── Attempts ─────────────────────────────────────────────────────────────────
def create_attempt(details: dict, assessment_minutes: int):
    master = current_master()
    form = generate_form(master)
    index = srt_index(master)
    snapshot, questions = [], []
    for number, srt_id in enumerate(form["srt_ids"], start=1):
        srt = dict(index[srt_id])
        srt["question_number"] = number
        snapshot.append(srt)
        questions.append({
            "question_number": number, "srt_id": srt_id, "primary_competency": srt["primary_competency"],
            "secondary_competency": "", "situation": srt["situation"],
        })
    session_id = str(uuid.uuid4())
    pqi = {
        "master_version":  master["version"],
        "rubric_version":  master["rubric_version"],
        "master_sha256":   master["sha256"],
        "prompt_version":  prompt_version_for(master),
        "evaluator_model": evaluator.PQI_EVALUATOR_MODEL,
        "generation_seed": form["seed"],
        "generation_info": {k: form[k] for k in ("attempts", "afi_yes", "within_target", "decision_items")},
        "srt_snapshot":    snapshot,
        "deadline_at":     utcnow() + timedelta(minutes=assessment_minutes),
    }
    session = database.create_session(session_id, details, questions, assessment_type=PQI, pqi=pqi)
    try:
        database.increment_bank_administrations(master["sha256"], form["srt_ids"])
    except Exception as exc:
        logger.warning("Could not update SRT administration counts: %s", exc)
    logger.info("PQI attempt %s created: seed=%s afi_yes=%s within_target=%s", session_id, form["seed"],
                form["afi_yes"], form["within_target"])
    return session_id, session


def candidate_payload(session_id: str, session: dict, resumed: bool) -> dict:
    """What the candidate's browser receives. Situations only — never master guidance."""
    return {
        "session_id":        session_id,
        "assessment_type":   PQI,
        "questions":         [{"question_number": q["question_number"], "srt_id": q["srt_id"],
                               "situation": q["situation"]} for q in session["questions"]],
        "total_questions":   len(session["questions"]),
        "answers":           session.get("collected_answers") or {},
        "answer_meta":       session.get("answer_meta") or {},
        "remaining_seconds": remaining_seconds(session),
        "deadline_at":       session.get("deadline_at"),
        "status":            session.get("status"),
        "resumed":           resumed,
    }


def _clean_meta(meta) -> dict:
    meta = meta if isinstance(meta, dict) else {}
    capture = "voice" if meta.get("input") == "voice" else "typed"
    language = meta.get("language") if meta.get("language") in ("en-IN", "hi-IN") else None
    return {"input": capture, "language": language if capture == "voice" else None}


def save_answer(session_id: str, session: dict, srt_id: str, text: str, meta) -> None:
    if session.get("assessment_type") != PQI:
        raise AnswerRejected(400, "This assessment saves answers when it is submitted.")
    if session.get("status") != "in_progress":
        raise AnswerRejected(409, "This assessment has already been submitted.")
    if srt_id not in {q["srt_id"] for q in session["questions"]}:
        raise AnswerRejected(400, "That question is not part of this assessment.")
    deadline = parse_iso(session.get("deadline_at"))
    if deadline and utcnow() > deadline + timedelta(seconds=SAVE_GRACE_SECONDS):
        raise AnswerRejected(410, "The assessment time has ended.")
    text = str(text or "")[:MAX_ANSWER_CHARS]
    meta = _clean_meta(meta)
    if not database.save_answer(session_id, srt_id, text, meta, utcnow()):
        raise AnswerRejected(409, "This assessment has already been submitted.")
    answers = dict(session.get("collected_answers") or {})
    metas = dict(session.get("answer_meta") or {})
    if text.strip():
        answers[srt_id], metas[srt_id] = text, meta
    else:
        answers.pop(srt_id, None)
        metas.pop(srt_id, None)
    session["collected_answers"], session["answer_meta"] = answers, metas


def merge_submission(session: dict, submitted: dict, submitted_meta) -> tuple[dict, dict]:
    """Server-saved answers, overlaid with the browser's — unless the browser is too late."""
    ids = {q["srt_id"] for q in session["questions"]}
    answers = {k: v for k, v in (session.get("collected_answers") or {}).items() if k in ids}
    metas = dict(session.get("answer_meta") or {})
    deadline = parse_iso(session.get("deadline_at"))
    if deadline and utcnow() > deadline + timedelta(seconds=SUBMIT_GRACE_SECONDS):
        logger.warning("Late PQI submission ignored; using answers saved before the deadline")
        return answers, metas
    submitted_meta = submitted_meta if isinstance(submitted_meta, dict) else {}
    for srt_id, text in (submitted or {}).items():
        if srt_id in ids and str(text or "").strip():
            answers[srt_id] = str(text)[:MAX_ANSWER_CHARS]
            if srt_id in submitted_meta or srt_id not in metas:
                metas[srt_id] = _clean_meta(submitted_meta.get(srt_id))
    return answers, {k: v for k, v in metas.items() if k in answers}


def expired_session_ids() -> list:
    return database.list_expired_sessions(PQI, utcnow() - timedelta(minutes=EXPIRED_SUBMIT_AFTER_MINUTES))


# ── Pipeline ─────────────────────────────────────────────────────────────────
def _compat(result: dict, srt: dict) -> dict:
    """Fields the shared admin tools read from every session's scores."""
    return {
        "competency":   srt["primary_competency"],
        "score":        result["final_score"],
        "strengths":    [],
        "improvements": [NOT_ANSWERED] if result["status"] == "blank" else [result.get("primary_gap") or ""],
    }


def _fail(session_id, session, message):
    logger.error("PQI pipeline failed for %s: %s", session_id, message)
    session["status"], session["error"] = "failed", message
    database.update_session(session_id, status="failed", error=message, processing_started_at=None)


def _earlier(result) -> dict:
    return {
        "Core_Response_Level": result["core_response_level"], "Excellence_Lift": result["evaluator_excellence_lift"],
        "Decision_Made": result["decision_made"], "Critical_Failure": "Yes" if result["critical_failure"] else "No",
        "Critical_Failure_Reason": result["critical_failure_reason"],
        "AFI_Activated": "Yes" if result["afi_activated"] else "No",
        "AFI_Evidence_Level": result["afi_evidence_level"], "Final_SRT_Score": result["final_score"],
        "Score_Justification": result["score_justification"], "Primary_Gap": result["primary_gap"],
    }


async def run_pipeline(session_id: str, session: dict, client) -> None:
    try:
        await _run(session_id, session, client)
    except Exception as exc:
        logger.exception("Unexpected PQI pipeline error for %s", session_id)
        _fail(session_id, session, f"Unexpected error: {exc}")


async def _run(session_id: str, session: dict, client) -> None:
    try:
        master = master_for_session(session)
    except PqiUnavailable as exc:
        _fail(session_id, session, f"PQI master unavailable: {exc}")
        return

    snapshot = session.get("srt_snapshot") or []
    by_id = {s["srt_id"]: s for s in snapshot}
    answers = session.get("collected_answers") or {}
    metas = session.get("answer_meta") or {}
    results = {k: v for k, v in (session.get("scores") or {}).items()
               if k in by_id and (v or {}).get("status") in ("scored", "blank")}

    version = prompt_version_for(master)
    session["prompt_version"], session["evaluator_model"] = version, evaluator.PQI_EVALUATOR_MODEL
    database.update_session(session_id, prompt_version=version, evaluator_model=evaluator.PQI_EVALUATOR_MODEL)
    common = {"session_id": session_id, "assessment_type": PQI, "master_version": master["version"],
              "rubric_version": master["rubric_version"], "prompt_version": version}

    def log(srt, pass_name, status, text, meta, raw, result):
        try:
            database.insert_evaluation({
                **common, "srt_id": srt["srt_id"], "primary_competency": srt["primary_competency"],
                "pass": pass_name, "status": status, "response_text": text, "response_capture": meta or {},
                "raw_output": raw, "result": result,
                "final_score": (result or {}).get("final_score"),
                "model": evaluator.PQI_EVALUATOR_MODEL if raw is not None or status == "error" else None,
            })
        except Exception:
            logger.exception("Could not append evaluation for %s/%s", session_id, srt["srt_id"])

    semaphore = asyncio.Semaphore(EVAL_CONCURRENCY)
    errors: dict = {}
    session["progress"] = len(results)

    # ── Steps 1–10: first pass ───────────────────────────────────────────────
    async def first_pass(srt):
        srt_id = srt["srt_id"]
        text = (answers.get(srt_id) or "").strip()
        meta = metas.get(srt_id)
        raw = None
        async with semaphore:
            if not text:
                result = scoring.blank_result(master, srt)
            else:
                try:
                    raw, observed = await asyncio.to_thread(evaluator.evaluate_srt, client, master, srt, text, meta)
                except evaluator.EvaluatorError as exc:
                    errors[srt_id] = str(exc)
                    log(srt, "first", "error", text, meta, None, {"error": str(exc)})
                    return
                result = scoring.apply_rules(master, srt, observed)
        result.update(_compat(result, srt), **{"pass": "first", "reviewed": False})
        results[srt_id] = result
        session["progress"] = len(results)
        log(srt, "first", result["status"], text, meta, raw, result)

    await asyncio.gather(*(first_pass(s) for s in snapshot if s["srt_id"] not in results))
    session["scores"] = {s["srt_id"]: results[s["srt_id"]] for s in snapshot if s["srt_id"] in results}
    database.update_session(session_id, scores=session["scores"], progress=len(results))
    if errors:
        detail = "; ".join(f"{k}: {v[:120]}" for k, v in list(errors.items())[:3])
        _fail(session_id, session, f"Evaluation failed for {len(errors)} of {len(snapshot)} SRT(s) ({detail}). "
                                   "Click Rescore — completed evaluations are kept.")
        return

    ordered = [results[s["srt_id"]] for s in snapshot]

    # ── Step 11: consistency check ───────────────────────────────────────────
    answered = [{"srt": by_id[r["srt_id"]], "response": (answers.get(r["srt_id"]) or "").strip()}
                for r in ordered if r["status"] == "scored"]
    consistency = {"status": "skipped", "contradictions": []}
    if len(answered) >= 2:
        try:
            _, contradictions = await asyncio.to_thread(evaluator.consistency_check, client, master, answered)
            consistency = {"status": "done", "contradictions": contradictions}
        except evaluator.EvaluatorError as exc:
            consistency = {"status": "error", "error": str(exc), "contradictions": []}

    # ── Step 12: second review ───────────────────────────────────────────────
    head = scoring.headline(master, ordered)
    plan = scoring.review_plan(master, head, ordered, consistency["contradictions"])
    review_failures, changes = [], []

    async def second_review(srt_id, reason):
        srt, earlier = by_id[srt_id], results[srt_id]
        text = (answers.get(srt_id) or "").strip()
        meta = metas.get(srt_id)
        async with semaphore:
            try:
                raw, observed = await asyncio.to_thread(
                    evaluator.evaluate_srt, client, master, srt, text, meta,
                    {"reason": reason, "earlier": _earlier(earlier)},
                )
            except evaluator.EvaluatorError as exc:
                review_failures.append(srt_id)
                earlier["review_error"] = str(exc)
                log(srt, "review", "error", text, meta, None, {"error": str(exc)})
                return
        reviewed = scoring.apply_rules(master, srt, observed)
        reviewed.update(_compat(reviewed, srt), **{
            "pass": "review", "reviewed": True, "review_reason": reason,
            "first_pass": {k: earlier[k] for k in ("final_score", "core_response_level", "excellence_lift",
                                                    "decision_made", "critical_failure", "afi_evidence_level")},
        })
        results[srt_id] = reviewed
        if (reviewed["final_score"], reviewed["critical_failure"], reviewed["afi_evidence_level"]) != (
                earlier["final_score"], earlier["critical_failure"], earlier["afi_evidence_level"]):
            changes.append({"srt_id": srt_id, "from_score": earlier["final_score"], "to_score": reviewed["final_score"],
                            "from_critical_failure": earlier["critical_failure"],
                            "to_critical_failure": reviewed["critical_failure"]})
        log(srt, "review", "scored", text, meta, raw, reviewed)

    pending = [(k, reason) for k, reason in plan["srts"].items() if not results[k].get("reviewed")]
    await asyncio.gather(*(second_review(k, reason) for k, reason in pending))

    ordered = [results[s["srt_id"]] for s in snapshot]
    session["scores"] = {s["srt_id"]: results[s["srt_id"]] for s in snapshot}
    database.update_session(session_id, scores=session["scores"])
    head = scoring.headline(master, ordered)
    ready = scoring.readiness(master, head, ordered, review_failures)

    # ── Narrative ────────────────────────────────────────────────────────────
    report = build_report(master, session, snapshot, ordered, head, ready, plan, changes, review_failures,
                          consistency, answers, metas)
    try:
        _, narrative = await asyncio.to_thread(evaluator.write_report, client, master, narrative_input(report),
                                               list(by_id))
    except evaluator.EvaluatorError as exc:
        _fail(session_id, session, f"Report narrative failed: {exc}. Click Rescore — evaluations are kept.")
        return
    report.update(narrative)
    session["report"] = report
    database.update_session(session_id, report=report)

    try:
        pdf = await asyncio.to_thread(generate_pqi_pdf, report, session["candidate"])
        session["pdf_bytes"] = pdf
        database.update_session(session_id, pdf_bytes=pdf)
    except Exception as exc:
        logger.exception("PQI PDF failed for %s", session_id)
        session["pdf_error"] = str(exc)
        database.update_session(session_id, pdf_error=str(exc))

    session["status"] = "completed"
    database.update_session(session_id, status="completed", processing_started_at=None)
    logger.info("PQI attempt %s completed: overall %.1f, readiness %s", session_id, head["overall"], ready["band"])


def _r1(value):
    return None if value is None else round(value, 1)


def build_report(master, session, snapshot, ordered, head, ready, plan, changes, review_failures,
                 consistency, answers, metas) -> dict:
    names = {c["code"]: c["name"] for c in master["competencies"]}
    results = {r["srt_id"]: r for r in ordered}
    flags = [{
        "question_number": s["question_number"], "srt_id": s["srt_id"], "competency": s["primary_competency"],
        "competency_name": names.get(s["primary_competency"], ""),
        "severity": results[s["srt_id"]]["critical_failure_severity"],
        "type": results[s["srt_id"]]["critical_failure_type"],
        "reason": results[s["srt_id"]]["critical_failure_reason"],
    } for s in snapshot if results[s["srt_id"]]["critical_failure"]]
    competency_scores = [{**{k: c[k] for k in ("code", "name", "lens", "weight", "srt_ids")}, "score": _r1(c["score"])}
                         for c in head["competencies"]]
    srt_results = []
    for s in snapshot:
        r = results[s["srt_id"]]
        srt_results.append({
            "question_number": s["question_number"], "competency_name": names.get(s["primary_competency"], ""),
            "situation": s["situation"], "response": answers.get(s["srt_id"], ""),
            "response_capture": evaluator.describe_capture(metas.get(s["srt_id"])) if answers.get(s["srt_id"]) else "",
            **{k: v for k, v in r.items() if k not in ("competency", "score", "strengths", "improvements")},
        })
    headline = {
        "overall": _r1(head["overall"]), "technical": _r1(head["technical_acumen"]),
        "business": _r1(head["business_acumen"]), "afi": _r1(head["afi"]), "readiness": ready["band"],
        "manual_review": ready["manual_review_required"], "critical_flags": len(flags),
    }
    return {
        "assessment_type":         PQI,
        "report_version":          1,
        "master": {k: master[k] for k in ("file_name", "sha256", "version", "rubric_version", "status", "effective_date")},
        "prompt_version":          session.get("prompt_version"),
        "evaluator_model":         evaluator.PQI_EVALUATOR_MODEL,
        "report_model":            evaluator.PQI_REPORT_MODEL,
        "overall_pqi_score":       _r1(head["overall"]),
        "overall_exact":           head["overall"],
        "overall_attainable_max":  master["overall_max"],
        "technical_acumen":        _r1(head["technical_acumen"]),
        "business_acumen":         _r1(head["business_acumen"]),
        "anti_firefighting_index": _r1(head["afi"]),
        "afi_band":                head["afi_band"],
        "afi_scored_srts":         head["afi_scored_srts"],
        "competency_scores":       competency_scores,
        "competency_summary":      {f"{c['code']} {c['name']}": c["score"] for c in competency_scores},
        "readiness":               ready,
        "overall_readiness":       ready["band"],
        "critical_flags":          flags,
        "second_review": {
            "triggers": plan["triggers"], "reviewed_srts": [r["srt_id"] for r in ordered if r.get("reviewed")],
            "changes": changes, "failures": review_failures,
        },
        "consistency_check":       consistency,
        "srt_results":             srt_results,
        "headline":                headline,
        "top_strengths":           [],
        "development_priorities":  [],
        "development_narrative":   "",
        "generated_at":            iso(utcnow()),
    }


def narrative_input(report: dict) -> dict:
    return {
        "Overall_PQI_Score": report["overall_pqi_score"],
        "Overall_Attainable_Max": report["overall_attainable_max"],
        "Technical_Acumen": report["technical_acumen"],
        "Business_Acumen": report["business_acumen"],
        "Anti_Firefighting_Index": report["anti_firefighting_index"],
        "AFI_Interpretation": report["afi_band"],
        "Readiness": report["overall_readiness"],
        "Readiness_Guardrails": report["readiness"]["caps"],
        "Competency_Scores": [{"Code": c["code"], "Competency": c["name"], "Score_out_of_10": c["score"]}
                              for c in report["competency_scores"]],
        "Critical_Flags": [{"SRT_ID": f["srt_id"], "Severity": f["severity"], "Reason": f["reason"]}
                           for f in report["critical_flags"]],
        "SRT_Evaluations": [{
            "SRT_ID": r["srt_id"], "Primary_Competency": f"{r['primary_competency']} {r['competency_name']}",
            "Situation": r["situation"], "Candidate_Response": r["response"] or "(no response)",
            "Final_SRT_Score": r["final_score"], "Decision_Made": r["decision_made"],
            "AFI_Evidence_Level": r["afi_evidence_level"], "Score_Justification": r["score_justification"],
            "Primary_Gap": r["primary_gap"],
        } for r in report["srt_results"]],
    }
