from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.colors import HexColor
import datetime

# RDC Brand Colors
RDC_BLUE = HexColor("#003366")
RDC_GOLD = HexColor("#C8960C")
RDC_LIGHT_BLUE = HexColor("#E8F0F7")
RDC_DARK_GREY = HexColor("#333333")
RDC_MID_GREY = HexColor("#666666")
RDC_LIGHT_GREY = HexColor("#F5F5F5")
WHITE = colors.white
BLACK = colors.black

READINESS_COLORS = {
    "Ready for higher responsibility": HexColor("#1A7A1A"),
    "Ready with structured support": HexColor("#CC7700"),
    "Not ready yet": HexColor("#CC0000"),
}


def _get_readiness_color(readiness: str) -> HexColor:
    for key, color in READINESS_COLORS.items():
        if key.lower() in readiness.lower():
            return color
    return RDC_DARK_GREY


def generate_pdf(report_data: dict, candidate: dict, output_path: str):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "Title", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=18,
        textColor=WHITE, alignment=TA_CENTER,
        spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontName="Helvetica", fontSize=11,
        textColor=HexColor("#CCE0F5"), alignment=TA_CENTER,
        spaceAfter=2
    )
    confidential_style = ParagraphStyle(
        "Confidential", parent=styles["Normal"],
        fontName="Helvetica-Oblique", fontSize=9,
        textColor=HexColor("#FF9999"), alignment=TA_CENTER,
    )
    section_header_style = ParagraphStyle(
        "SectionHeader", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=12,
        textColor=WHITE, spaceAfter=6, spaceBefore=4,
        leftIndent=8
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=RDC_DARK_GREY, spaceAfter=4,
        leading=15, alignment=TA_JUSTIFY
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=RDC_DARK_GREY, spaceAfter=3,
        leftIndent=15, leading=14
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10,
        textColor=RDC_BLUE, spaceAfter=2
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=RDC_DARK_GREY, spaceAfter=2
    )
    score_big_style = ParagraphStyle(
        "ScoreBig", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=32,
        textColor=RDC_BLUE, alignment=TA_CENTER
    )
    score_label_style = ParagraphStyle(
        "ScoreLabel", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10,
        textColor=RDC_MID_GREY, alignment=TA_CENTER
    )
    readiness_style = ParagraphStyle(
        "Readiness", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=13,
        textColor=_get_readiness_color(report_data.get("overall_readiness", "")),
        alignment=TA_CENTER, spaceAfter=4
    )

    story = []

    # ─── HEADER BANNER ───────────────────────────────────────────────
    header_data = [
        [Paragraph("RDC Plant Incharge", title_style)],
        [Paragraph("Competency Assessment Report", title_style)],
        [Paragraph("SRT – Situation Reaction Test | 30 Questions | 10 Competencies", subtitle_style)],
        [Paragraph("CONFIDENTIAL – Head Office Use Only", confidential_style)],
    ]
    header_table = Table(header_data, colWidths=[17 * cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RDC_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6]),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.5 * cm))

    # ─── CANDIDATE INFORMATION ───────────────────────────────────────
    story.append(_section_header("1.  Candidate Information", section_header_style))

    cand_data = [
        [Paragraph("Name:", label_style), Paragraph(candidate.get("candidate_name", ""), value_style)],
        [Paragraph("Plant Location:", label_style), Paragraph(candidate.get("plant_location", ""), value_style)],
        [Paragraph("Assessment Date:", label_style), Paragraph(candidate.get("assessment_date", ""), value_style)],
        [Paragraph("Report Generated:", label_style), Paragraph(datetime.date.today().strftime("%d %B %Y"), value_style)],
    ]
    cand_table = Table(cand_data, colWidths=[5 * cm, 12 * cm])
    cand_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_GREY),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(cand_table)
    story.append(Spacer(1, 0.4 * cm))

    # ─── OVERALL PERFORMANCE SUMMARY ─────────────────────────────────
    story.append(_section_header("2.  Overall Performance Summary", section_header_style))

    total = report_data.get("overall_score_out_of_300", 0)
    normalized = report_data.get("normalized_score_out_of_100", 0)
    readiness = report_data.get("overall_readiness", "")

    perf_data = [
        [
            Paragraph(str(total), score_big_style),
            Paragraph(f"{normalized:.1f}%", score_big_style),
            Paragraph(readiness, readiness_style),
        ],
        [
            Paragraph("Total Score (out of 300)", score_label_style),
            Paragraph("Normalized Score", score_label_style),
            Paragraph("Overall Readiness", score_label_style),
        ],
    ]
    perf_table = Table(perf_data, colWidths=[5.5 * cm, 5.5 * cm, 6 * cm])
    perf_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_BLUE),
        ("BOX", (0, 0), (-1, -1), 1, RDC_BLUE),
        ("LINEAFTER", (0, 0), (1, -1), 0.5, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(perf_table)
    story.append(Spacer(1, 0.4 * cm))

    # ─── COMPETENCY PERFORMANCE ───────────────────────────────────────
    story.append(_section_header("3.  Competency Performance Highlights", section_header_style))

    comp_summary = report_data.get("competency_summary", {})
    comp_rows = [
        [
            Paragraph("<b>Competency</b>", label_style),
            Paragraph("<b>Score (Avg/10)</b>", label_style),
            Paragraph("<b>Rating</b>", label_style),
        ]
    ]
    for comp, score in comp_summary.items():
        rating = _get_rating(score)
        comp_rows.append([
            Paragraph(comp, body_style),
            Paragraph(f"{score:.1f} / 10", body_style),
            Paragraph(rating, _rating_style(rating, styles)),
        ])

    comp_table = Table(comp_rows, colWidths=[9 * cm, 3.5 * cm, 4.5 * cm])
    comp_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), RDC_LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, RDC_LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 0.4 * cm))

    # ─── KEY STRENGTHS ────────────────────────────────────────────────
    story.append(_section_header("4.  Key Strengths", section_header_style))
    for s in report_data.get("top_strengths", []):
        story.append(Paragraph(f"✓  {s}", bullet_style))
    story.append(Spacer(1, 0.3 * cm))

    # ─── KEY DEVELOPMENT AREAS ───────────────────────────────────────
    story.append(_section_header("5.  Key Development Areas", section_header_style))
    for d in report_data.get("development_areas", []):
        story.append(Paragraph(f"▶  {d}", bullet_style))
    story.append(Spacer(1, 0.3 * cm))

    # ─── RECOMMENDED DEVELOPMENT ACTIONS ─────────────────────────────
    story.append(_section_header("6.  Recommended Development Actions", section_header_style))
    for i, a in enumerate(report_data.get("development_actions", []), 1):
        story.append(Paragraph(f"{i}.  {a}", bullet_style))
    story.append(Spacer(1, 0.3 * cm))

    # ─── 30-60-90 DAY COACHING PLAN ──────────────────────────────────
    story.append(_section_header("7.  30 – 60 – 90 Day Coaching Plan", section_header_style))

    coaching = report_data.get("coaching_plan_30_60_90", {})
    coaching_data = [
        [Paragraph("<b>Phase</b>", label_style), Paragraph("<b>Focus Areas</b>", label_style)],
    ]
    for phase, label in [("30_days", "First 30 Days"), ("60_days", "31 – 60 Days"), ("90_days", "61 – 90 Days")]:
        items = coaching.get(phase, [])
        text = "<br/>".join([f"• {item}" for item in items])
        coaching_data.append([
            Paragraph(f"<b>{label}</b>", label_style),
            Paragraph(text, body_style),
        ])

    coaching_table = Table(coaching_data, colWidths=[4 * cm, 13 * cm])
    coaching_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), RDC_LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, RDC_LIGHT_GREY]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(coaching_table)
    story.append(Spacer(1, 0.4 * cm))

    # ─── FINAL READINESS STATEMENT ───────────────────────────────────
    story.append(_section_header("8.  Final Readiness Statement", section_header_style))

    readiness_color = _get_readiness_color(readiness)
    readiness_box_data = [[Paragraph(readiness, ParagraphStyle(
        "ReadinessBox", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=14,
        textColor=readiness_color, alignment=TA_CENTER
    ))]]
    readiness_table = Table(readiness_box_data, colWidths=[17 * cm])
    readiness_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 2, readiness_color),
        ("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(readiness_table)

    # Add PDF report narrative text if present
    pdf_text = report_data.get("pdf_report_text", "")
    if pdf_text:
        story.append(Spacer(1, 0.5 * cm))
        story.append(HRFlowable(width="100%", thickness=1, color=RDC_BLUE))
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("<b>Assessor Notes</b>", label_style))
        for line in pdf_text.split("\n"):
            line = line.strip()
            if line:
                story.append(Paragraph(line, body_style))

    # ─── FOOTER ──────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RDC_MID_GREY))
    story.append(Paragraph(
        "This report is confidential and intended for authorized RDC management use only. "
        "Powered by RDC SRT Assessment Engine.",
        ParagraphStyle("Footer", parent=styles["Normal"],
                       fontName="Helvetica-Oblique", fontSize=8,
                       textColor=RDC_MID_GREY, alignment=TA_CENTER)
    ))

    doc.build(story)


def _section_header(text: str, style: ParagraphStyle):
    """Return a section header table with blue background."""
    data = [[Paragraph(text, style)]]
    t = Table(data, colWidths=[17 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RDC_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def _get_rating(score: float) -> str:
    if score >= 8:
        return "Excellent"
    elif score >= 6:
        return "Good"
    elif score >= 4:
        return "Developing"
    else:
        return "Needs Improvement"


def _rating_style(rating: str, styles) -> ParagraphStyle:
    color_map = {
        "Excellent": HexColor("#1A7A1A"),
        "Good": HexColor("#0055AA"),
        "Developing": HexColor("#CC7700"),
        "Needs Improvement": HexColor("#CC0000"),
    }
    return ParagraphStyle(
        f"Rating_{rating}",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=color_map.get(rating, RDC_DARK_GREY),
    )
