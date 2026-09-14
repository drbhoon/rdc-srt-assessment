"""The assessments this app administers.

Every session and every access code belongs to exactly one type. Rows written
before PQI existed carry no type and are Plant Manager — which is what the
column default says, so their meaning never changes.
"""
PLANT_MANAGER = "plant_manager"
PQI = "pqi"

LABELS = {
    PLANT_MANAGER: "Plant Manager",
    PQI: "Plant Quality Incharge (PQI)",
}

_ALIASES = {
    "": PLANT_MANAGER,
    "pm": PLANT_MANAGER,
    "plant_manager": PLANT_MANAGER,
    "plant manager": PLANT_MANAGER,
    "pqi": PQI,
}


def normalise(value) -> str | None:
    """The canonical type for `value`, or None if it names no known assessment.

    Blank means Plant Manager, so legacy rows and callers that predate PQI keep
    working without knowing types exist.
    """
    return _ALIASES.get(str(value or "").strip().lower())
