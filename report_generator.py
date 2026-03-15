import json
import re
from pathlib import Path
import anthropic


def get_system_prompt() -> str:
    skill_path = Path(__file__).parent / "skill" / "RDC-Plant-Incharge-SRT-Assessment.md"
    return skill_path.read_text(encoding="utf-8")


def generate_final_report(
    client: anthropic.Anthropic,
    candidate_name: str,
    plant_location: str,
    assessment_date: str,
    results: list,
) -> dict:
    """Call Claude API in MODE 2 (final_report) and return parsed JSON report."""
    system_prompt = get_system_prompt()

    input_data = {
        "mode": "final_report",
        "candidate_name": candidate_name,
        "plant_location": plant_location,
        "assessment_date": assessment_date,
        "results": results,
    }

    message = client.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=8192,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Generate the final assessment report:\n\n"
                    f"{json.dumps(input_data, indent=2, ensure_ascii=False)}"
                ),
            }
        ],
    )

    response_text = message.content[0].text.strip()

    # Extract JSON block if wrapped in markdown
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if json_match:
        response_text = json_match.group(1)
    else:
        obj_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if obj_match:
            response_text = obj_match.group()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        result = {"raw_response": response_text, "error": "JSON parse failed"}

    return result
