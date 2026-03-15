import io
import datetime
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

RDC_BLUE       = HexColor("#003366")
RDC_GOLD       = HexColor("#C8960C")
RDC_LIGHT_BLUE = HexColor("#E8F0F7")
RDC_DARK_GREY  = HexColor("#333333")
RDC_MID_GREY   = HexColor("#666666")
RDC_LIGHT_GREY = HexColor("#F5F5F5")
WHITE          = colors.white

READINESS_COLORS = {
    "ready for higher responsibility": HexColor("#1A7A1A"),
    "ready with structured support":   HexColor("#CC7700"),
    "not ready yet":                   HexColor("#CC0000"),
}


def _readiness_color(text: str) -> HexColor:
    t = (text or "").lower()
    for key, col in READINESS_COLORS.items():
        if key in t:
            return col
    return RDC_DARK_GREY


def generate_pdf(report_data: dict, candidate: dict, output_path: str = None) -> bytes:
    """Generate PDF and return as bytes. output_path ignored (kept for compat)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm,
    )

    base = getSampleStyleSheet()

    def sty(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    title_sty   = sty("T",  fontName="Helvetica-Bold",    fontSize=18, textColor=WHITE,         alignment=TA_CENTER, spaceAfter=4)
    sub_sty     = sty("Su", fontName="Helvetica",         fontSize=11, textColor=HexColor("#CCE0F5"), alignment=TA_CENTER, spaceAfter=2)
    conf_sty    = sty("C",  fontName="Helvetica-Oblique", fontSize=9,  textColor=HexColor("#FF9999"), alignment=TA_CENTER)
    sec_sty     = sty("S",  fontName="Helvetica-Bold",    fontSize=12, textColor=WHITE,         spaceAfter=6, spaceBefore=4, leftIndent=8)
    body_sty    = sty("B",  fontName="Helvetica",         fontSize=10, textColor=RDC_DARK_GREY, spaceAfter=4, leading=15, alignment=TA_JUSTIFY)
    bullet_sty  = sty("Bu", fontName="Helvetica",         fontSize=10, textColor=RDC_DARK_GREY, spaceAfter=3, leftIndent=15, leading=14)
    label_sty   = sty("L",  fontName="Helvetica-Bold",    fontSize=10, textColor=RDC_BLUE,      spaceAfter=2)
    value_sty   = sty("V",  fontName="Helvetica",         fontSize=10, textColor=RDC_DARK_GREY, spaceAfter=2)
    big_sty     = sty("Bg", fontName="Helvetica-Bold",    fontSize=32, textColor=RDC_BLUE,      alignment=TA_CENTER)
    slbl_sty    = sty("SL", fontName="Helvetica",         fontSize=10, textColor=RDC_MID_GREY,  alignment=TA_CENTER)
    footer_sty  = sty("F",  fontName="Helvetica-Oblique", fontSize=8,  textColor=RDC_MID_GREY,  alignment=TA_CENTER)

    readiness     = report_data.get("overall_readiness", "")
    readiness_col = _readiness_color(readiness)
    read_sty      = sty("R", fontName="Helvetica-Bold", fontSize=13, textColor=readiness_col, alignment=TA_CENTER, spaceAfter=4)

    story = []

    # ── HEADER ──────────────────────────────────────────────────────────
    hdr = Table([
        [Paragraph("RDC Plant Incharge", title_sty)],
        [Paragraph("Competency Assessment Report", title_sty)],
        [Paragraph("SRT – Situation Reaction Test  |  30 Questions  |  10 Competencies", sub_sty)],
        [Paragraph("CONFIDENTIAL – Head Office Use Only", conf_sty)],
    ], colWidths=[17*cm])
    hdr.setStyle(TableStyle([
        ("BACKGROUND", (0,0),(-1,-1), RDC_BLUE),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),10), ("RIGHTPADDING",(0,0),(-1,-1),10),
    ]))
    story += [hdr, Spacer(1, 0.5*cm)]

    # ── 1. CANDIDATE INFORMATION ─────────────────────────────────────────
    story.append(_sec("1.  Candidate Information", sec_sty))
    ci = Table([
        [Paragraph("Name:",            label_sty), Paragraph(candidate.get("candidate_name",""),  value_sty)],
        [Paragraph("Plant Location:",  label_sty), Paragraph(candidate.get("plant_location",""),  value_sty)],
        [Paragraph("Assessment Date:", label_sty), Paragraph(candidate.get("assessment_date",""), value_sty)],
        [Paragraph("Report Generated:",label_sty), Paragraph(datetime.date.today().strftime("%d %B %Y"), value_sty)],
    ], colWidths=[5*cm, 12*cm])
    ci.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),RDC_LIGHT_GREY),
        ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),10),
    ]))
    story += [ci, Spacer(1, 0.4*cm)]

    # ── 2. OVERALL PERFORMANCE ──────────────────────────────────────────
    story.append(_sec("2.  Overall Performance Summary", sec_sty))
    total      = report_data.get("overall_score_out_of_300", 0)
    normalized = report_data.get("normalized_score_out_of_100", 0.0)
    perf = Table([
        [Paragraph(str(total), big_sty), Paragraph(f"{float(normalized):.1f}%", big_sty), Paragraph(readiness, read_sty)],
        [Paragraph("Total Score (out of 300)", slbl_sty), Paragraph("Normalized Score", slbl_sty), Paragraph("Overall Readiness", slbl_sty)],
    ], colWidths=[5.5*cm, 5.5*cm, 6*cm])
    perf.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),RDC_LIGHT_BLUE),
        ("BOX",(0,0),(-1,-1),1,RDC_BLUE),
        ("LINEAFTER",(0,0),(1,-1),0.5,colors.lightgrey),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
    ]))
    story += [perf, Spacer(1, 0.4*cm)]

    # ── 3. COMPETENCY HIGHLIGHTS ─────────────────────────────────────────
    story.append(_sec("3.  Competency Performance Highlights", sec_sty))
    comp_rows = [[Paragraph("<b>Competency</b>", label_sty), Paragraph("<b>Score (Avg/10)</b>", label_sty), Paragraph("<b>Rating</b>", label_sty)]]
    for comp, score in (report_data.get("competency_summary") or {}).items():
        rating = _rating_label(score)
        comp_rows.append([
            Paragraph(comp, body_sty),
            Paragraph(f"{float(score):.1f} / 10", body_sty),
            Paragraph(rating, sty(f"Rt{rating}", fontName="Helvetica-Bold", fontSize=10, textColor=_rating_color(rating))),
        ])
    ct = Table(comp_rows, colWidths=[9*cm, 3.5*cm, 4.5*cm])
    ct.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),RDC_LIGHT_BLUE),
        ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, RDC_LIGHT_GREY]),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [ct, Spacer(1, 0.4*cm)]

    # ── 4. KEY STRENGTHS ────────────────────────────────────────────────
    story.append(_sec("4.  Key Strengths", sec_sty))
    for s in (report_data.get("top_strengths") or []):
        story.append(Paragraph(f"&#10003;  {s}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 5. DEVELOPMENT AREAS ────────────────────────────────────────────
    story.append(_sec("5.  Key Development Areas", sec_sty))
    for d in (report_data.get("development_areas") or []):
        story.append(Paragraph(f"&#9658;  {d}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 6. RECOMMENDED ACTIONS ──────────────────────────────────────────
    story.append(_sec("6.  Recommended Development Actions", sec_sty))
    for i, a in enumerate((report_data.get("development_actions") or []), 1):
        story.append(Paragraph(f"{i}.  {a}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 7. 30-60-90 COACHING PLAN ───────────────────────────────────────
    story.append(_sec("7.  30 – 60 – 90 Day Coaching Plan", sec_sty))
    coaching = report_data.get("coaching_plan_30_60_90") or {}
    coach_rows = [[Paragraph("<b>Phase</b>", label_sty), Paragraph("<b>Focus Areas</b>", label_sty)]]
    for phase, lbl in [("30_days","First 30 Days"),("60_days","31 – 60 Days"),("90_days","61 – 90 Days")]:
        items = coaching.get(phase) or []
        coach_rows.append([
            Paragraph(f"<b>{lbl}</b>", label_sty),
            Paragraph("<br/>".join(f"• {x}" for x in items), body_sty),
        ])
    cot = Table(coach_rows, colWidths=[4*cm, 13*cm])
    cot.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),RDC_LIGHT_BLUE),
        ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, RDC_LIGHT_GREY]),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [cot, Spacer(1, 0.4*cm)]

    # ── 8. FINAL READINESS STATEMENT ────────────────────────────────────
    story.append(_sec("8.  Final Readiness Statement", sec_sty))
    rbox = Table([[Paragraph(readiness, sty("RB", fontName="Helvetica-Bold", fontSize=14,
                                             textColor=readiness_col, alignment=TA_CENTER))]],
                 colWidths=[17*cm])
    rbox.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),2,readiness_col),
        ("BACKGROUND",(0,0),(-1,-1),RDC_LIGHT_GREY),
        ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12),
    ]))
    story.append(rbox)

    # Narrative text if present
    pdf_text = report_data.get("pdf_report_text","")
    if pdf_text:
        story += [Spacer(1,0.5*cm), HRFlowable(width="100%",thickness=1,color=RDC_BLUE),
                  Spacer(1,0.2*cm), Paragraph("<b>Assessor Notes</b>", label_sty)]
        for line in pdf_text.split("\n"):
            line = line.strip()
            if line:
                story.append(Paragraph(line, body_sty))

    # ── FOOTER ──────────────────────────────────────────────────────────
    story += [
        Spacer(1, 0.5*cm),
        HRFlowable(width="100%", thickness=0.5, color=RDC_MID_GREY),
        Paragraph(
            "This report is confidential and intended for authorised RDC management use only. "
            "Powered by RDC SRT Assessment Engine v2.0",
            footer_sty
        ),
    ]

    doc.build(story)
    return buf.getvalue()


def _sec(text, style):
    t = Table([[Paragraph(text, style)]], colWidths=[17*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),RDC_BLUE),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(-1,-1),10),
    ]))
    return t


def _rating_label(score: float) -> str:
    s = float(score)
    if s >= 8: return "Excellent"
    if s >= 6: return "Good"
    if s >= 4: return "Developing"
    return "Needs Improvement"


def _rating_color(rating: str) -> HexColor:
    return {
        "Excellent":         HexColor("#1A7A1A"),
        "Good":              HexColor("#0055AA"),
        "Developing":        HexColor("#CC7700"),
        "Needs Improvement": HexColor("#CC0000"),
    }.get(rating, RDC_DARK_GREY)
