import json
import re
import logging
from pathlib import Path
import anthropic

logger = logging.getLogger(__name__)


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


def score_question(
    client: anthropic.Anthropic,
    srt_id: str,
    situation: str,
    primary_competency: str,
    secondary_competency: str,
    candidate_transcript: str,
) -> dict:
    """Call Claude API in MODE 1 (score_one) and return parsed JSON result."""

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

    try:
        message = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1024,
            system=system_prompt,
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
        # Prepend the prefilled '{' back before parsing
        response_text = "{" + message.content[0].text.strip()
        logger.info("Scorer raw response for %s: %s", srt_id, response_text[:200])
        json_str = _extract_json(response_text)
        result = json.loads(json_str)
        # Ensure total is an int
        result["total"] = int(result.get("total", 0))
        return result

    except Exception as exc:
        logger.error("Scoring error for %s: %s", srt_id, exc)
        return {
            "srt_id": srt_id,
            "primary_competency": primary_competency,
            "problem_understanding": 0,
            "primary_depth": 0,
            "secondary_awareness": 0,
            "structure_clarity": 0,
            "total": 0,
            "strengths": [],
            "improvements": [f"Scoring error: {str(exc)[:80]}"],
        }
