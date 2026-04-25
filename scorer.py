import json
import os
import re
import time
import logging
from pathlib import Path
import anthropic

logger = logging.getLogger(__name__)

# ── Retry configuration ──────────────────────────────────────────────────────
MAX_ATTEMPTS       = 3
BACKOFF_SECONDS    = (1, 2, 4)   # sleep before attempts 2, 3 (index 0 unused)
SCORER_MAX_TOKENS  = 2048        # was 1024 — bumped to prevent JSON truncation

# ── Model selection ──────────────────────────────────────────────────────────
# Old default `claude-3-haiku-20240307` was deprecated by Anthropic and now
# returns 404 not_found_error (confirmed in prod April 2026). Override via env
# var ANTHROPIC_SCORER_MODEL if Anthropic retires the current default too.
SCORER_MODEL = os.getenv("ANTHROPIC_SCORER_MODEL", "claude-haiku-4-5")


def get_system_prompt() -> str:
    skill_path = Path(__file__).parent / "skill" / "RDC-Plant-Incharge-SRT-Assessment.md"
    return skill_path.read_text(encoding="utf-8")


def _extract_json(text: str) -> str:
    """Robustly extract a JSON object from text, handling markdown fences and nested braces."""
    # 1. Try to strip markdown code fences
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text)
    if fence:
        return fence.group(1)

    # 2. Find the first '{' and match the closing '}' by counting braces
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    # fallback: return everything from first brace
    return text[start:]


class TruncatedResponseError(Exception):
    """Raised when Claude hit max_tokens ceiling and output is incomplete JSON."""


def _score_once(
    client: anthropic.Anthropic,
    srt_id: str,
    system_prompt: str,
    input_data: dict,
) -> dict:
    """Single scoring attempt. Raises on failure so caller can retry.

    v4.14: System prompt is wrapped as a single content block with
    cache_control=ephemeral. The 9000-char skill prompt is identical for
    every one of the 30 scoring calls per candidate. After the first call
    populates the cache, the next 29 read it at ~10% input cost AND a
    fraction of the TPM weight — which is the difference between staying
    under Sonnet 4.5 tier-1 limits vs. cascading 429s. Cache TTL is 5min,
    so a full 30-question batch (~3-5 min) stays warm end-to-end.
    """
    message = client.messages.create(
        model=SCORER_MODEL,
        max_tokens=SCORER_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Score this SRT response. "
                    "Respond with ONLY a valid JSON object — no prose, no markdown fences. "
                    "Start with '{' and end with '}'.\n\n"
                    + json.dumps(input_data, indent=2, ensure_ascii=False)
                ),
            },
            {
                # Assistant prefill — guarantees response begins with '{'
                "role": "assistant",
                "content": "{",
            },
        ],
    )

    # Surface cache hit/miss to logs for tuning visibility. Anthropic returns
    # cache_creation_input_tokens (write) and cache_read_input_tokens (hit).
    usage = getattr(message, "usage", None)
    if usage is not None:
        logger.info(
            "Scorer usage for %s: in=%s out=%s cache_write=%s cache_read=%s",
            srt_id,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
            getattr(usage, "cache_creation_input_tokens", 0),
            getattr(usage, "cache_read_input_tokens", 0),
        )

    # Detect truncation early so we retry rather than attempt to parse garbage
    if getattr(message, "stop_reason", None) == "max_tokens":
        logger.warning(
            "Scorer truncated at max_tokens for %s (stop_reason=max_tokens) — will retry",
            srt_id,
        )
        raise TruncatedResponseError(f"Response truncated at max_tokens for {srt_id}")

    # Prepend the prefilled '{' back before parsing
    response_text = "{" + message.content[0].text.strip()
    logger.info("Scorer raw response for %s: %s", srt_id, response_text[:200])
    json_str = _extract_json(response_text)
    result = json.loads(json_str)
    result["total"] = int(result.get("total", 0))
    return result


def score_question(
    client: anthropic.Anthropic,
    srt_id: str,
    situation: str,
    primary_competency: str,
    secondary_competency: str,
    candidate_transcript: str,
) -> dict:
    """Call Claude API in MODE 1 (score_one) with retry, return parsed JSON result."""

    # Empty transcript → score 0 immediately (unanswered question)
    if not candidate_transcript or not candidate_transcript.strip():
        return {
            "srt_id": srt_id,
            "primary_competency": primary_competency,
            "problem_understanding": 0,
            "primary_depth": 0,
            "secondary_awareness": 0,
            "structure_clarity": 0,
            "total": 0,
            "strengths": [],
            "improvements": ["Question not answered — counted as zero."],
        }

    system_prompt = get_system_prompt()
    input_data = {
        "mode": "score_one",
        "srt_id": srt_id,
        "situation": situation,
        "primary_competency": primary_competency,
        "secondary_competency": secondary_competency,
        "candidate_transcript": candidate_transcript,
    }

    last_exc: Exception | None = None
    final_attempt = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        final_attempt = attempt
        try:
            return _score_once(client, srt_id, system_prompt, input_data)

        # 400 BadRequestError is DETERMINISTIC — same input → same 400 every time.
        # Don't waste 2 more retries (= 6 extra wasted seconds + double API cost).
        # Bail immediately AND log full forensic context (model, transcript head,
        # exception body) so we can diagnose what content tripped the validator.
        # MUST come BEFORE the generic APIStatusError catch below — Python picks
        # the first matching except, so order = priority.
        except anthropic.BadRequestError as exc:
            last_exc = exc
            logger.error(
                "Scorer 400 BadRequestError for %s — bailing immediately (no retry, deterministic).\n"
                "  model=%s max_tokens=%d\n"
                "  transcript_len=%d transcript_head=%r\n"
                "  full_error=%s",
                srt_id, SCORER_MODEL, SCORER_MAX_TOKENS,
                len(candidate_transcript), candidate_transcript[:200],
                str(exc)[:800],
                exc_info=True,
            )
            break

        # Retryable classes: 429/5xx, connection blips, truncation marker, JSON parse errors
        except (
            anthropic.APIError,
            anthropic.APIStatusError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            TruncatedResponseError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                # Default fixed backoff (1s, 2s, 4s)
                delay = BACKOFF_SECONDS[attempt - 1] if attempt - 1 < len(BACKOFF_SECONDS) else 4

                # On 429 RateLimitError, Anthropic returns a `retry-after`
                # header telling us EXACTLY how long to wait. Honoring that
                # is much more efficient than guessing — our (1,2,4) sequence
                # was retrying too soon and re-triggering the same 429.
                if isinstance(exc, anthropic.RateLimitError):
                    try:
                        retry_after_hdr = exc.response.headers.get("retry-after")
                        if retry_after_hdr:
                            ra = int(float(retry_after_hdr))
                            # Clamp 1-30s — protect against pathological values
                            delay = max(1, min(30, ra))
                            logger.warning(
                                "Scoring 429 for %s — honoring retry-after=%ss (was %ss)",
                                srt_id, delay, BACKOFF_SECONDS[attempt - 1],
                            )
                    except (AttributeError, ValueError, TypeError):
                        pass  # Fall back to fixed backoff

                logger.warning(
                    "Scoring attempt %d/%d failed for %s (%s: %s) — retrying in %ds",
                    attempt, MAX_ATTEMPTS, srt_id, type(exc).__name__, str(exc)[:120], delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "Scoring attempt %d/%d FINAL failure for %s (%s: %s)",
                    attempt, MAX_ATTEMPTS, srt_id, type(exc).__name__, str(exc)[:200],
                )

        except Exception as exc:
            # Non-retryable — fail fast
            last_exc = exc
            logger.error(
                "Non-retryable scoring error for %s (%s: %s)",
                srt_id, type(exc).__name__, str(exc)[:200], exc_info=True,
            )
            break

    # All attempts exhausted → return zero-payload (preserves old contract for the caller).
    # Truncate at 300 (was 80) so the Diagnose panel actually shows the API's
    # `message` field instead of cutting off mid-dict-repr at the structural keys.
    err_label = type(last_exc).__name__ if last_exc else "Unknown"
    err_text  = str(last_exc)[:300] if last_exc else "no exception captured"
    return {
        "srt_id": srt_id,
        "primary_competency": primary_competency,
        "problem_understanding": 0,
        "primary_depth": 0,
        "secondary_awareness": 0,
        "structure_clarity": 0,
        "total": 0,
        "strengths": [],
        "improvements": [f"Scoring error after {final_attempt} attempt(s) [{err_label}]: {err_text}"],
    }
