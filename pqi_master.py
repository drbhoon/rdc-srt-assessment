"""The PQI master workbook, read by sheet and column NAME and validated before use.

The workbook is the authority for PQI. Its SRTs, competencies, rubric text and
scoring settings are read from it and never restated in code. Loading is
fail-closed: if anything the engine depends on is missing or malformed, PQI is
unavailable and every problem is reported together — and Plant Manager, which
does not read this file, carries on regardless.

The result is a plain JSON-serialisable dict. The same dict is stored with the
database's master registry, so an attempt administered under one version can
still be rescored under that version after a newer workbook is deployed.
"""
from __future__ import annotations

import hashlib
import io
import re
import warnings
from collections import Counter
from pathlib import Path

import openpyxl

SRT_SHEET = "PQI_SRT_Master_100"
METADATA_SHEET = "Master_Metadata"
COMPETENCY_SHEET = "Competency_Framework"
OUTPUT_SPEC_SHEET = "Candidate_Output_Spec"
AFI_SHEET = "AFI_Rubric"

# Given to the evaluator verbatim, in this order.
EVALUATOR_SHEETS = (
    METADATA_SHEET, COMPETENCY_SHEET, "Scoring_Architecture", "AI_Scoring_Framework",
    "SRT_Scoring_Rubric_v1", AFI_SHEET, "AI_Evaluator_Output_Schema", "AI_Evaluation_Sequence",
)
# Given to the report writer verbatim.
REPORT_SHEETS = (COMPETENCY_SHEET, OUTPUT_SPEC_SHEET, AFI_SHEET)

REQUIRED_COLUMNS = (
    "SRT_ID", "Primary_Competency", "Situation", "Model_Response", "AI_Evaluation_Anchors",
    "Serious_Negative_or_Critical_Indicators", "Primary_Discriminator", "Decision_Applicable",
    "Anti_Firefighting_Applicability", "Prevention_Evidence_Expected", "Demand_Type",
    "Critical_Failure_Severity",
)
# Critical_Failure_Type is optional: master 1.0-PILOT predates it.
OPTIONAL_COLUMNS = (
    "Critical_Failure_Type", "Selection_Class", "SRT_Max_Score", "AFI_Max_When_Scored",
    "Critical_Failure_Control", "Scoring_Note", "Selection_Governance_Note",
)

DECISION_VALUES = {"yes": "Yes", "no": "No"}
AFI_VALUES = {"yes": "Yes", "conditional": "Conditional", "no": "No"}
SEVERITY_VALUES = {"none": "None", "major": "Major", "severe": "Severe"}
FAILURE_TYPE_VALUES = {
    "none": "None", "integrity": "Integrity", "technical-safety": "Technical-Safety",
    "technical safety": "Technical-Safety", "other": "Other",
}
# Normalisation the implementation instructions require.
DEMAND_TYPE_ALIASES = {"material": "Materials"}

# Band names the readiness guardrails refer to. A master that renames them
# cannot be scored correctly, so it is refused rather than guessed at.
GUARDRAIL_BANDS = ("Developing", "Ready", "High Readiness")

EXCEL_SHEET_NAME_LIMIT = 31


class PqiMasterError(Exception):
    """The workbook cannot be used. The message lists every problem found."""


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _rows(ws) -> list[list[str]]:
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = [_cell(c) for c in row]
        if any(cells):
            while cells and not cells[-1]:
                cells.pop()
            rows.append(cells)
    return rows


def _range(text: str):
    m = re.search(r"(\d+)\s*[–—-]\s*(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _number(text: str):
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group(0)) if m else None


def _table(sheets, name, required, problems):
    """Rows of a sheet as dicts keyed by header, or [] after recording why not."""
    rows = sheets.get(name)
    if not rows:
        problems.append(f"sheet {name!r} is missing or empty")
        return []
    header = rows[0]
    missing = [c for c in required if c not in header]
    if missing:
        problems.append(f"sheet {name!r} lacks column(s): {', '.join(missing)}")
        return []
    return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header) if h} for r in rows[1:]]


def load_master(path) -> dict:
    path = Path(path)
    if not path.exists():
        raise PqiMasterError(f"PQI master workbook not found: {path}")
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the >31-character sheet name warning; reported below instead
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheets = {ws.title: _rows(ws) for ws in wb.worksheets}
        finally:
            wb.close()

    problems: list[str] = []
    warnings_out: list[str] = []

    for title in sheets:
        if len(title) > EXCEL_SHEET_NAME_LIMIT:
            warnings_out.append(
                f"sheet name {title!r} is {len(title)} characters; Excel allows {EXCEL_SHEET_NAME_LIMIT} "
                "and may truncate it on the next save (the engine does not read that sheet)"
            )

    # ── Master_Metadata ──────────────────────────────────────────────────────
    metadata_rows = sheets.get(METADATA_SHEET) or []
    if not metadata_rows:
        problems.append(f"sheet {METADATA_SHEET!r} is missing or empty")
    metadata = {r[0]: (r[1] if len(r) > 1 else "") for r in metadata_rows[1:] if r and r[0]}

    def setting(field):
        value = metadata.get(field, "")
        if not value:
            problems.append(f"{METADATA_SHEET}: {field} is missing")
        return value

    if metadata_rows and metadata.get("Assessment_Type", "").upper() != "PQI":
        problems.append(f"{METADATA_SHEET}: Assessment_Type must be PQI, found {metadata.get('Assessment_Type')!r}")
    version = setting("Master_Version")
    rubric_version = setting("Scoring_Rubric_Version")
    status = setting("Status")

    def integer(field):
        raw = setting(field)
        number = _number(raw)
        if raw and (number is None or number != int(number)):
            problems.append(f"{METADATA_SHEET}: {field} must be a whole number, found {raw!r}")
            return None
        return int(number) if number is not None else None

    decision_cap = integer("Decision_Cap")
    critical_failure_cap = integer("Critical_Failure_Cap")
    overall_max = integer("Overall_Attainable_Max")
    afi_yes_floor = integer("AFI_Yes_Emergency_Floor")

    scale = _range(setting("Operational_SRT_Scale"))
    if metadata.get("Operational_SRT_Scale") and scale != (0, 9):
        problems.append(f"{METADATA_SHEET}: this engine implements an SRT scale of 0–9, "
                        f"master says {metadata.get('Operational_SRT_Scale')!r}")
    afi_yes_target = _range(setting("AFI_Yes_Target_Per_30"))
    if metadata.get("AFI_Yes_Target_Per_30") and not afi_yes_target:
        problems.append(f"{METADATA_SHEET}: AFI_Yes_Target_Per_30 must be a range such as 18–22")
    proximity_raw = setting("Second_Review_Threshold_Proximity")
    second_review_proximity = _number(proximity_raw)
    if proximity_raw and second_review_proximity is None:
        problems.append(f"{METADATA_SHEET}: Second_Review_Threshold_Proximity has no number")

    # ── Competency_Framework ─────────────────────────────────────────────────
    competencies = []
    for rec in _table(sheets, COMPETENCY_SHEET, ("Code", "Competency", "Definition", "Acumen Lens", "Acumen Weight"), problems):
        if not rec.get("Code"):
            continue
        weight = _number(rec.get("Acumen Weight", ""))
        if weight is None or weight <= 0:
            problems.append(f"{COMPETENCY_SHEET}: {rec['Code']} has no positive Acumen Weight")
        lens = rec.get("Acumen Lens", "")
        if lens not in ("Business", "Technical"):
            problems.append(f"{COMPETENCY_SHEET}: {rec['Code']} Acumen Lens must be Business or Technical, found {lens!r}")
        competencies.append({
            "code": rec["Code"], "name": rec.get("Competency", ""), "definition": rec.get("Definition", ""),
            "lens": lens, "weight": weight,
        })
    codes = [c["code"] for c in competencies]
    if competencies and len(set(codes)) != len(codes):
        problems.append(f"{COMPETENCY_SHEET}: duplicate competency codes")
    for lens in ("Business", "Technical"):
        if competencies and not any(c["lens"] == lens for c in competencies):
            problems.append(f"{COMPETENCY_SHEET}: no {lens} competencies")

    # ── PQI_SRT_Master_100 ───────────────────────────────────────────────────
    srt_rows = sheets.get(SRT_SHEET) or []
    has_failure_type = bool(srt_rows) and "Critical_Failure_Type" in srt_rows[0]
    if srt_rows and not has_failure_type:
        warnings_out.append(
            "Critical_Failure_Type column is not in this master; a confirmed Severe Critical Failure "
            "cannot be assigned to a guardrail and is referred for manual review"
        )
    srts = []
    for n, rec in enumerate(_table(sheets, SRT_SHEET, REQUIRED_COLUMNS, problems), start=2):
        srt_id = rec.get("SRT_ID", "")
        where = f"{SRT_SHEET} row {n} ({srt_id or 'no SRT_ID'})"
        for column in REQUIRED_COLUMNS:
            if not rec.get(column):
                problems.append(f"{where}: {column} is empty")

        def enum(column, allowed, default=None):
            raw = rec.get(column, "")
            if not raw and default is not None:
                return default
            value = allowed.get(raw.strip().lower())
            if value is None:
                problems.append(f"{where}: {column} {raw!r} is not one of {sorted(set(allowed.values()))}")
            return value

        demand = rec.get("Demand_Type", "")
        failure_type = enum("Critical_Failure_Type", FAILURE_TYPE_VALUES, default="") if has_failure_type else None
        for column, expected in (("AFI_Max_When_Scored", 5), ("SRT_Max_Score", 10)):
            if rec.get(column) and _number(rec[column]) != expected:
                problems.append(f"{where}: {column} is {rec[column]!r}; this engine implements {expected}")
        srts.append({
            "srt_id": srt_id,
            "primary_competency": rec.get("Primary_Competency", ""),
            "situation": rec.get("Situation", ""),
            "model_response": rec.get("Model_Response", ""),
            "evaluation_anchors": rec.get("AI_Evaluation_Anchors", ""),
            "serious_negatives": rec.get("Serious_Negative_or_Critical_Indicators", ""),
            "primary_discriminator": rec.get("Primary_Discriminator", ""),
            "decision_applicable": enum("Decision_Applicable", DECISION_VALUES),
            "afi_applicability": enum("Anti_Firefighting_Applicability", AFI_VALUES),
            "prevention_evidence_expected": rec.get("Prevention_Evidence_Expected", ""),
            "demand_type": DEMAND_TYPE_ALIASES.get(demand.lower(), demand),
            "critical_failure_severity": enum("Critical_Failure_Severity", SEVERITY_VALUES),
            "critical_failure_type": failure_type or None,
            "critical_failure_control": rec.get("Critical_Failure_Control", ""),
            "scoring_note": rec.get("Scoring_Note", ""),
            "selection_class": rec.get("Selection_Class", ""),
        })

    ids = Counter(s["srt_id"] for s in srts)
    duplicates = sorted(i for i, count in ids.items() if count > 1 and i)
    if duplicates:
        problems.append(f"{SRT_SHEET}: duplicate SRT_ID(s): {', '.join(duplicates)}")
    per_competency = Counter(s["primary_competency"] for s in srts)
    unknown = sorted(set(per_competency) - set(codes))
    if competencies and unknown:
        problems.append(f"{SRT_SHEET}: Primary_Competency not in {COMPETENCY_SHEET}: {', '.join(unknown)}")
    for code in codes:
        if srts and per_competency.get(code, 0) < 3:
            problems.append(f"{SRT_SHEET}: {code} has {per_competency.get(code, 0)} SRTs; a form needs 3")
        elif srts and per_competency.get(code, 0) != 10:
            warnings_out.append(f"{code} has {per_competency.get(code, 0)} SRTs (the design is 10)")

    # ── Readiness bands (Candidate_Output_Spec) ──────────────────────────────
    readiness_bands = []
    for row in (sheets.get(OUTPUT_SPEC_SHEET) or [])[1:]:
        label_cell, text = row[0], (row[1] if len(row) > 1 else "")
        span = _range(label_cell) if re.fullmatch(r"\d+\s*[–—-]\s*\d+", label_cell) else None
        below = re.fullmatch(r"(?i)below\s+(\d+)", label_cell)
        if span:
            lower, upper = span
        elif below:
            lower, upper = 0, int(below.group(1)) - 1
        else:
            continue
        readiness_bands.append({"lower": lower, "upper": upper, "label": text.split("/")[0].strip(),
                                "description": text})
    readiness_bands.sort(key=lambda b: b["lower"])
    if not readiness_bands:
        problems.append(f"{OUTPUT_SPEC_SHEET}: no readiness bands found")
    else:
        _check_contiguous(readiness_bands, 0, overall_max, OUTPUT_SPEC_SHEET + " readiness bands", problems)
        labels = {b["label"] for b in readiness_bands}
        for name in GUARDRAIL_BANDS:
            if name not in labels:
                problems.append(f"{OUTPUT_SPEC_SHEET}: the guardrails need a band named {name!r}")

    # ── AFI interpretation bands (AFI_Rubric) ────────────────────────────────
    afi_bands = []
    for row in (sheets.get(AFI_SHEET) or [])[1:]:
        m = re.fullmatch(r"AFI INTERPRETATION\s+(\d+)\s*[–—-]\s*(\d+)", row[0])
        if m:
            afi_bands.append({"lower": int(m.group(1)), "upper": int(m.group(2)),
                              "label": row[1] if len(row) > 1 else "", "description": row[2] if len(row) > 2 else ""})
    afi_bands.sort(key=lambda b: b["lower"])
    if not afi_bands:
        problems.append(f"{AFI_SHEET}: no AFI INTERPRETATION bands found")
    else:
        _check_contiguous(afi_bands, 0, 100, AFI_SHEET + " interpretation bands", problems)

    for name in set(EVALUATOR_SHEETS) | set(REPORT_SHEETS):
        if not sheets.get(name):
            problems.append(f"sheet {name!r} (given to the AI verbatim) is missing or empty")

    master = {
        "assessment_type": "PQI",
        "file_name": path.name,
        "sha256": sha256,
        "version": version,
        "rubric_version": rubric_version,
        "status": status,
        "effective_date": metadata.get("Effective_Date", ""),
        "metadata": metadata,
        "decision_cap": decision_cap,
        "critical_failure_cap": critical_failure_cap,
        "overall_max": overall_max,
        "afi_yes_target": list(afi_yes_target) if afi_yes_target else None,
        "afi_yes_floor": afi_yes_floor,
        "second_review_proximity": second_review_proximity,
        "competencies": competencies,
        "srts": srts,
        "readiness_bands": readiness_bands,
        "afi_bands": afi_bands,
        "has_failure_type": has_failure_type,
        "sheets": {name: sheets[name] for name in set(EVALUATOR_SHEETS) | set(REPORT_SHEETS) if sheets.get(name)},
        "warnings": warnings_out,
    }

    if not problems and afi_yes_target:
        from pqi_generator import feasibility

        low, high = feasibility(master)
        if high < afi_yes_floor:
            problems.append(f"no balanced form can reach the AFI=Yes floor of {afi_yes_floor} (best is {high})")
        elif high < afi_yes_target[0] or low > afi_yes_target[1]:
            warnings_out.append(
                f"balanced forms can hold {low}–{high} AFI=Yes SRTs, outside the target "
                f"{afi_yes_target[0]}–{afi_yes_target[1]}; the closest form above the floor is used"
            )

    if problems:
        raise PqiMasterError(f"{path.name}: " + "; ".join(problems))
    return master


def _check_contiguous(bands, low, high, what, problems):
    expected = low
    for band in bands:
        if band["lower"] != expected or band["upper"] < band["lower"]:
            problems.append(f"{what} are not contiguous from {low} to {high}")
            return
        expected = band["upper"] + 1
    if high is not None and expected - 1 != high:
        problems.append(f"{what} end at {expected - 1}, expected {high}")


def srt_index(master) -> dict:
    return {s["srt_id"]: s for s in master["srts"]}


def competency_index(master) -> dict:
    return {c["code"]: c for c in master["competencies"]}
