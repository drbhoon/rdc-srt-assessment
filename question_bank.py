import openpyxl
import random
from collections import defaultdict
from pathlib import Path

# Normalize Excel competency names to match requirement spec
COMPETENCY_MAP = {
    "Safety, Operational Discipline & SARTAJ Ownership": "Operational Discipline & SARTAJ Ownership",
    "Operational Discipline & SARTAJ Ownership": "Operational Discipline & SARTAJ Ownership",
}

REQUIRED_COMPETENCIES = [
    "Integrity & Trust",
    "Preventive Maintenance & Asset Care",
    "Planning, Organizing & Coordination",
    "Operational Discipline & SARTAJ Ownership",
    "Communication & Assertiveness",
    "Team Orientation & Delegation",
    "Customer Orientation & Relationship Handling",
    "Vendor & External Stakeholder Management",
    "Cost & Resource Responsibility",
    "Functional Knowledge & Multiskilling",
]


def load_questions(excel_path: str) -> dict:
    """Load all questions from Excel, grouped by normalized competency."""
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    questions_by_competency = defaultdict(list)

    for row in rows[1:]:  # skip header
        if not row[0]:
            continue

        srt_id = str(row[0]).strip()
        primary_comp = str(row[1]).strip() if row[1] else ""
        secondary_comp = str(row[2]).strip() if row[2] else ""
        situation = str(row[3]).strip() if row[3] else ""

        # Normalize competency names
        primary_comp = COMPETENCY_MAP.get(primary_comp, primary_comp)
        secondary_comp = COMPETENCY_MAP.get(secondary_comp, secondary_comp)

        if primary_comp and situation:
            questions_by_competency[primary_comp].append({
                "srt_id": srt_id,
                "primary_competency": primary_comp,
                "secondary_competency": secondary_comp,
                "situation": situation,
            })

    wb.close()
    return dict(questions_by_competency)


def get_session_questions(questions_by_competency: dict, per_competency: int = 3) -> list:
    """Randomly select `per_competency` questions per competency and return shuffled list."""
    selected = []
    for comp in REQUIRED_COMPETENCIES:
        pool = questions_by_competency.get(comp, [])
        if not pool:
            continue
        chosen = random.sample(pool, min(per_competency, len(pool)))
        selected.extend(chosen)

    random.shuffle(selected)

    for i, q in enumerate(selected):
        q["question_number"] = i + 1

    return selected
