import copy
import random
from collections import defaultdict

import openpyxl

# Normalize Excel competency names to match requirement spec
COMPETENCY_MAP = {
    "Safety, Operational Discipline & SARTAJ Ownership": "Operational Discipline & SARTAJ Ownership",
    "Operational Discipline & SARTAJ Ownership":         "Operational Discipline & SARTAJ Ownership",
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
    """Load all questions from Excel, grouped by normalised competency.
    Returns a plain dict: { competency_name: [question_dict, ...] }
    The returned dicts are never mutated — callers must copy before modifying.
    """
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    questions_by_competency: dict = defaultdict(list)

    for row in rows[1:]:          # skip header
        if not row[0]:
            continue

        srt_id         = str(row[0]).strip()
        primary_comp   = str(row[1]).strip() if row[1] else ""
        secondary_comp = str(row[2]).strip() if row[2] else ""
        situation      = str(row[3]).strip() if row[3] else ""

        primary_comp   = COMPETENCY_MAP.get(primary_comp,   primary_comp)
        secondary_comp = COMPETENCY_MAP.get(secondary_comp, secondary_comp)

        if primary_comp and situation:
            questions_by_competency[primary_comp].append({
                "srt_id":              srt_id,
                "primary_competency":  primary_comp,
                "secondary_competency":secondary_comp,
                "situation":           situation,
            })

    wb.close()
    return dict(questions_by_competency)


def get_session_questions(questions_by_competency: dict, per_competency: int = 3) -> list:
    """Return a shuffled list of `per_competency` questions per competency.

    Key guarantees:
    1. Each returned question is a *deep copy* — the global pool is never mutated.
    2. No SRT_ID appears twice in the returned list (cross-competency dedup guard).
    3. `random.sample` already prevents intra-competency duplicates; the dedup set
       provides belt-and-braces protection against any edge-case pool overlap.
    """
    selected  = []
    used_ids  = set()   # track SRT IDs already chosen this session

    for comp in REQUIRED_COMPETENCIES:
        pool = questions_by_competency.get(comp, [])
        if not pool:
            continue

        # Exclude any SRT_IDs already picked (shouldn't happen but guarantees uniqueness)
        available = [q for q in pool if q["srt_id"] not in used_ids]
        n = min(per_competency, len(available))
        if n == 0:
            continue

        chosen = random.sample(available, n)

        for q in chosen:
            used_ids.add(q["srt_id"])
            # Deep-copy so we never mutate the shared global pool
            selected.append(copy.deepcopy(q))

    random.shuffle(selected)

    # Assign question numbers on the copies, not the originals
    for i, q in enumerate(selected):
        q["question_number"] = i + 1

    return selected
