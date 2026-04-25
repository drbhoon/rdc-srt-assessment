import json
import logging
import os
import re
import time
from pathlib import Path
import anthropic

logger = logging.getLogger(__name__)

# Old default `claude-3-haiku-20240307` was deprecated by Anthropic and now
# returns 404 not_found_error. Override via env var ANTHROPIC_REPORT_MODEL.
# Report gen analyzes 30 transcripts in one call — Haiku works but Sonnet
# gives better holistic synthesis if you want to trade cost for quality.
REPORT_MODEL = os.getenv("ANTHROPIC_REPORT_MODEL", "claude-haiku-4-5")

# ── Max output tokens ────────────────────────────────────────────────────────
# The report JSON is LARGE — it contains 10 competency narratives (~80 words
# each), strengths, dev areas, coaching plan for 30/60/90 days, and more. At
# 4096 output tokens, Haiku-4.5 consistently truncates mid-JSON on verbose
# candidates → `stop_reason=max_tokens` → our retry wrapper tries 3× but all
# 3 hit the same ceiling → session fails. Haiku-4.5 supports up to 64K
# output; 16K is plenty of headroom and still well within billing sanity.
REPORT_MAX_TOKENS = int(os.getenv("ANTHROPIC_REPORT_MAX_TOKENS", "16000"))

# ── Retry configuration (mirrors scorer.py) ──────────────────────────────────
# Without retries, ANY transient API hiccup (brief 5xx, 429, network blip)
# during the single report-gen call kills the whole session with status=failed.
# Observed in prod v4.8: Vivek's rescore succeeded (136/300), Ashwani/Saksham
# rescores failed on identical inputs — classic unretried-transient-error
# pattern.
REPORT_MAX_ATTEMPTS    = 3
REPORT_BACKOFF_SECONDS = (2, 5)  # sleep before attempts 2, 3 — report gen is
                                  # more expensive than scoring so use longer
                                  # backoffs to let throttles clear.


class ReportTruncatedError(Exception):
    """Raised when Claude hit max_tokens ceiling and output is incomplete JSON."""


def get_system_prompt() -> str:
    skill_path = Path(__file__).parent / "skill" / "RDC-Plant-Incharge-SRT-Assessment.md"
    return skill_path.read_text(encoding="utf-8")


def _report_once(
    client: anthropic.Anthropic,
    system_prompt: str,
    input_data: dict,
) -> dict:
    """Single report-gen attempt. Raises on failure so caller can retry."""
    message = client.messages.create(
        model=REPORT_MODEL,
        max_tokens=REPORT_MAX_TOKENS,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    "Generate the final assessment report. "
                    "All 30 candidate transcripts are included in the results array — "
                    "analyze behavioral patterns across ALL responses holistically. "
                    "Identify cross-competency signals (e.g., a Cost question response may "
                    "reveal Communication or Integrity traits). "
                    "Ground every observation in specific phrases from the candidate's actual words. "
                    "Apply RMC India operational context (SARTAJ, batching plant, transit mixers, "
                    "monsoon, vendor/contractor dynamics). "
                    "You MUST respond with ONLY a valid JSON object — no prose, no markdown, "
                    "no explanation before or after. Start with '{' and end with '}'.\n\n"
                    f"{json.dumps(input_data, indent=2, ensure_ascii=False)}"
                ),
            },
            {
                # Assistant prefill — forces the model to continue from '{' → pure JSON
                "role": "assistant",
                "content": "{",
            },
        ],
    )

    # Detect truncation early so we retry rather than attempt to parse garbage
    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.warning("Report generator truncated at max_tokens — will retry")
        raise ReportTruncatedError("Report response truncated at max_tokens")

    # Model continues after our prefilled '{', so prepend it back
    response_text = "{" + message.content[0].text.strip()

    # Strip any trailing markdown fence if model added one
    response_text = re.sub(r"```.*$", "", response_text, flags=re.DOTALL).strip()

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Fallback: try to extract the outermost JSON object.
        # Re-raise if even that fails so the retry wrapper can try again.
        obj_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if obj_match:
            return json.loads(obj_match.group())   # may raise — intended
        raise


def generate_final_report(
    client: anthropic.Anthropic,
    candidate_name: str,
    plant_location: str,
    assessment_date: str,
    results: list,
) -> dict:
    """Call Claude API in MODE 2 (final_report) and return parsed JSON report.
    Retries up to REPORT_MAX_ATTEMPTS on transient API errors / truncation /
    JSON parse failures. Propagates the final error to the caller (the
    background pipeline sets session.error = str(exc) if this escapes).
    """
    system_prompt = get_system_prompt()
    input_data = {
        "mode": "final_report",
        "candidate_name": candidate_name,
        "plant_location": plant_location,
        "assessment_date": assessment_date,
        "results": results,
    }

    last_exc: Exception | None = None
    for attempt in range(1, REPORT_MAX_ATTEMPTS + 1):
        try:
            return _report_once(client, system_prompt, input_data)

        # 400 BadRequestError is deterministic — same payload → same rejection.
        # Common causes: 30 verbose transcripts pushing past context window,
        # malformed unicode in a transcript, content-policy hit, prefill
        # conflict. Retrying wastes 7 seconds (2+5 backoff) and burns billing.
        # Bail immediately AND log forensic context (candidate name, payload
        # size, first transcript head) so we can actually diagnose. MUST come
        # BEFORE the generic APIStatusError catch — order = priority.
        except anthropic.BadRequestError as exc:
            last_exc = exc
            try:
                payload_chars    = len(json.dumps(input_data, ensure_ascii=False))
                first_transcript = (input_data.get("results") or [{}])[0].get("transcript", "")
            except Exception:
                payload_chars    = -1
                first_transcript = ""
            logger.error(
                "Report-gen 400 BadRequestError — bailing immediately (no retry, deterministic).\n"
                "  candidate=%s model=%s max_tokens=%d\n"
                "  payload_chars=%d first_transcript_head=%r\n"
                "  full_error=%s",
                input_data.get("candidate_name"), REPORT_MODEL, REPORT_MAX_TOKENS,
                payload_chars, first_transcript[:200],
                str(exc)[:1000],
                exc_info=True,
            )
            break

        except (
            anthropic.APIError,
            anthropic.APIStatusError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            ReportTruncatedError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            if attempt < REPORT_MAX_ATTEMPTS:
                delay = (
                    REPORT_BACKOFF_SECONDS[attempt - 1]
                    if attempt - 1 < len(REPORT_BACKOFF_SECONDS) else 5
                )
                logger.warning(
                    "Report-gen attempt %d/%d failed (%s: %s) — retrying in %ds",
                    attempt, REPORT_MAX_ATTEMPTS,
                    type(exc).__name__, str(exc)[:160], delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Report-gen attempt %d/%d FINAL failure (%s: %s)",
                    attempt, REPORT_MAX_ATTEMPTS,
                    type(exc).__name__, str(exc)[:300],
                )

    # All retries exhausted (or 400 fast-fail) — re-raise so
    # process_assessment_async captures the message in session.error.
    # Admin can then see the precise reason via Diagnose.
    raise last_exc if last_exc else RuntimeError("Report generation failed (no exception captured)")
