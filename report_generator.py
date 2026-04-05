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
        max_tokens=4096,
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

    # Model continues after our prefilled '{', so prepend it back
    response_text = "{" + message.content[0].text.strip()

    # Strip any trailing markdown fence if model added one
    response_text = re.sub(r"```.*$", "", response_text, flags=re.DOTALL).strip()

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        # Fallback: try to extract the outermost JSON object
        obj_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if obj_match:
            try:
                result = json.loads(obj_match.group())
            except json.JSONDecodeError:
                result = {"raw_response": response_text, "error": "JSON parse failed"}
        else:
            result = {"raw_response": response_text, "error": "JSON parse failed"}

    return result
