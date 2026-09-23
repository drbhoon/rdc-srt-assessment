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


# ─── Presentation ───────────────────────────────────────────────────────────
# One engine, several assessments under it. The engine name is fixed and never
# varies; what varies is the ROLE being assessed, and it varies in a handful of
# predictable places — a hero line, a page title, a stat tile, an instruction
# list. Those live here rather than in the pages, because the pages used to
# hardcode Plant Manager and then patch themselves for PQI, which meant every
# new assessment edited four HTML files and was one forgotten `if` away from
# telling a candidate they were sitting somebody else's paper.
#
# Adding an assessment is now: a row in LABELS, an alias, and an entry here.
#
# PLANT-PRO survives on the Plant Manager PDF for continuity with reports
# already issued (2026-09-23 decision) — the screens all say RDC-KYS.
ENGINE_NAME = "RDC – Situation Based Competency Assessment Engine (RDC-KYS)"

# What the start page shows before an access code has been validated. The
# candidate has not yet told us which assessment they are here for, so nothing
# on the page may claim a role. Values common to every assessment are safe.
DEFAULT_PRESENTATION = {
    "role": "",
    # A short form for places with no room for the full role name,
    # such as the badge beside an access code.
    "short": "",
    "tagline": "AI-assisted competency evaluation for RMC operations roles.",
    "page_title": "RDC – Situation Based Competency Assessment",
    "exam_title": "Situation Based Competency Assessment | RDC",
    "assessment_name": "Situation Based Competency Assessment",
    "stats": [
        {"num": "30", "label": "Questions"},
        {"num": "10", "label": "Competencies"},
        {"num": "{minutes}", "label": "Minutes"},
    ],
    "instructions": [
        "You will be presented with <strong>30 real-world situations</strong> one at a time.",
        "Read each situation carefully and respond as you would in the actual role.",
        "You may <strong>type your response</strong> or use <strong>voice dictation</strong>.",
        "Be specific — state your decision, the evidence you rely on, and who does what.",
        "Total time allowed: <strong>{minutes} minutes</strong>. A countdown timer is shown at the top.",
        "Do not skip questions. All 30 must be answered for a complete report.",
        "After submission, HR will review your results and revert back to you with the outcome.",
    ],
    # Whether the exam page offers the English/Hindi speech-recognition switch.
    "voice_languages": False,
    # Which console shape this assessment reports in. Scoring differs in kind,
    # not just in wording — /300 across 10 competencies is not /100 with
    # Technical, Business and AFI sub-scores — so a new assessment declares
    # which of the two it follows instead of the console testing for "pqi".
    "report_style": PLANT_MANAGER,
    "table_headers": [],
    "avg_label": "Avg Score",
}

PRESENTATION = {
    PLANT_MANAGER: {
        "short": "Plant Manager",
        "tagline": "AI-assisted competency evaluation for RMC Plant Manager candidates.",
        "page_title": "RDC – Plant Manager Situation Based Competency Assessment",
        "exam_title": "Plant Manager – SBCA Assessment | RDC",
        "assessment_name": "Plant Manager Situation Based Competency Assessment",
        "stats": [
            {"num": "30", "label": "Questions"},
            {"num": "10", "label": "Competencies"},
            {"num": "300", "label": "Max Score"},
            {"num": "{minutes}", "label": "Minutes"},
        ],
        "instructions": [
            "You will be presented with <strong>30 real-world plant situations</strong> one at a time.",
            "Read each situation carefully and respond as you would in the actual role.",
            "You may <strong>type your response</strong> or use the <strong>voice dictation</strong> feature.",
            "Be specific — mention data, systems, team actions, root causes where relevant.",
            "Total time allowed: <strong>{minutes} minutes</strong>. A countdown timer is shown at the top.",
            "Do not skip questions. All 30 must be answered for a complete report.",
            "Your responses are evaluated on: Problem Understanding, Depth, Secondary Awareness, and Structure.",
            "After submission, HR will review your results and revert back to you with the outcome.",
        ],
        "report_style": PLANT_MANAGER,
        "table_headers": ["#", "Candidate Name", "Plant Location", "Date", "Created", "Status",
                          "Score /300", "Normalised %", "Readiness", "Answered", "Actions", "Delete"],
        "avg_label": "Avg Score (/300)",
    },
    PQI: {
        "short": "PQI",
        "tagline": "AI-assisted competency evaluation for RMC Plant Quality Incharge candidates.",
        "page_title": "RDC – Plant Quality Incharge (PQI) Situation Based Competency Assessment",
        "exam_title": "Plant Quality Incharge (PQI) – SRT Assessment | RDC",
        "assessment_name": "Plant Quality Incharge (PQI) Situation Based Competency Assessment",
        "stats": [
            {"num": "30", "label": "Questions"},
            {"num": "10", "label": "Competencies"},
            {"num": "EN / हिं", "label": "Voice Languages"},
            {"num": "{minutes}", "label": "Minutes"},
        ],
        "instructions": [
            "You will be presented with <strong>30 real-world quality situations</strong> one at a time.",
            "Read each situation carefully and respond as you would as the Plant Quality Incharge.",
            "You may <strong>type your response</strong> or use <strong>voice dictation</strong>. English, "
            "Hindi and Hinglish are all fine — for voice, choose English or Hindi speech recognition.",
            "Be specific: state your decision, the evidence you rely on, who does what, and how you would "
            "stop the problem recurring.",
            "Total time allowed: <strong>{minutes} minutes</strong>. A countdown timer is shown at the top.",
            "Your answers are saved as you go. If your connection drops, reopen this page and enter the same "
            "access code and details to continue where you left off — the timer keeps running from your "
            "original start.",
            "Unanswered questions are scored as zero.",
            "After submission, HR will review your results and revert back to you with the outcome.",
        ],
        "voice_languages": True,
        "report_style": PQI,
        "table_headers": ["#", "Candidate Name", "Plant Location", "Date", "Created", "Status",
                          "Overall /100", "Technical", "Business", "AFI", "Readiness", "Answered",
                          "Actions", "Delete"],
        "avg_label": "Avg Overall (/100)",
    },
}

# Tab order in the console, and the order any future picker lists them in.
ORDER = [PLANT_MANAGER, PQI]


def presentation(value=None) -> dict:
    """How `value` presents itself, with anything it does not state defaulted.

    An unknown or absent type gets the role-free default, which is what the
    start page shows before an access code has named the assessment.
    """
    kind = normalise(value) if value else None
    merged = dict(DEFAULT_PRESENTATION)
    if kind:
        merged["role"] = LABELS[kind]
        merged.update(PRESENTATION.get(kind, {}))
    return merged


def presentation_map() -> dict:
    """Every assessment's presentation, plus the default, for the front end."""
    data = {kind: presentation(kind) for kind in ORDER}
    data["default"] = presentation()
    return data
