import json
import re
from pathlib import Path
import anthropic


def get_system_prompt() -> str:
    skill_path = Path(__file__).parent / "skill" / "RDC-Plant-Incharge-SRT-Assessment.md"
    return skill_path.read_text(encoding="utf-8")


def score_question(
    client: anthropic.Anthropic,
    srt_id: str,
    situation: str,
    primary_competency: str,
    secondary_competency: str,
    candidate_transcript: str,
) -> dict:
    """Call Claude API in MODE 1 (score_one) and return parsed JSON result."""
    system_prompt = get_system_prompt()

    input_data = {
        "mode": "score_one",
        "srt_id": srt_id,
        "situation": situation,
        "primary_competency": primary_competency,
        "secondary_competency": secondary_competency,
        "candidate_transcript": candidate_transcript,
    }

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": f"Score this SRT response:\n\n{json.dumps(input_data, indent=2, ensure_ascii=False)}",
            }
        ],
    )

    response_text = message.content[0].text.strip()

    # Extract JSON block if wrapped in markdown
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if json_match:
        response_text = json_match.group(1)
    else:
        # Try to find bare JSON object
        obj_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if obj_match:
            response_text = obj_match.group()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        # Fallback: return minimal valid structure
        result = {
            "srt_id": srt_id,
            "primary_competency": primary_competency,
            "problem_understanding": 0,
            "primary_depth": 0,
            "secondary_awareness": 0,
            "structure_clarity": 0,
            "total": 0,
            "strengths": ["Could not parse response"],
            "improvements": ["Please re-score manually"],
        }

    return result
