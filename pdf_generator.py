import io
import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak
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


def _esc(text: str) -> str:
    """Escape XML special characters for ReportLab Paragraph."""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_pdf(report_data: dict, candidate: dict, output_path: str = None) -> bytes:
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
    ital_sty    = sty("It", fontName="Helvetica-Oblique", fontSize=9,  textColor=RDC_MID_GREY,  spaceAfter=2, leftIndent=15, leading=13)
    narr_sty    = sty("Nr", fontName="Helvetica",         fontSize=9,  textColor=RDC_DARK_GREY, spaceAfter=6, leading=13, leftIndent=10)
    sublbl_sty  = sty("SbL",fontName="Helvetica-Bold",    fontSize=10, textColor=RDC_DARK_GREY, spaceAfter=2, leftIndent=5)

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
        [Paragraph("Name:",            label_sty), Paragraph(_esc(candidate.get("candidate_name","")),  value_sty)],
        [Paragraph("Plant Location:",  label_sty), Paragraph(_esc(candidate.get("plant_location","")),  value_sty)],
        [Paragraph("Assessment Date:", label_sty), Paragraph(_esc(candidate.get("assessment_date","")), value_sty)],
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
        [Paragraph(str(total), big_sty), Paragraph(f"{float(normalized):.1f}%", big_sty), Paragraph(_esc(readiness), read_sty)],
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
            Paragraph(_esc(comp), body_sty),
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

    # ── 4. COMPETENCY NARRATIVES (NEW) ──────────────────────────────────
    narratives = report_data.get("competency_narratives") or {}
    if narratives:
        story.append(_sec("4.  Competency-wise Assessment Narrative", sec_sty))
        for comp_name, narrative in narratives.items():
            story.append(Paragraph(f"<b>{_esc(comp_name)}</b>", sublbl_sty))
            story.append(Paragraph(_esc(narrative), narr_sty))
        story.append(Spacer(1, 0.3*cm))

    # ── 5. BEHAVIORAL PROFILE (NEW) ─────────────────────────────────────
    profile = report_data.get("behavioral_profile") or {}
    if profile:
        story.append(_sec("5.  Behavioral Profile", sec_sty))
        label_map = {
            "communication_style":      "Communication Style",
            "decision_making_approach":  "Decision-Making Approach",
            "leadership_orientation":    "Leadership Orientation",
            "stress_response_pattern":   "Stress Response Pattern",
            "accountability_stance":     "Accountability Stance",
        }
        for key, display in label_map.items():
            text = profile.get(key, "")
            if text:
                story.append(Paragraph(f"<b>{display}:</b>", sublbl_sty))
                story.append(Paragraph(_esc(text), narr_sty))
        story.append(Spacer(1, 0.3*cm))

    # ── 6. KEY STRENGTHS (enhanced) ─────────────────────────────────────
    sec_num = 6
    story.append(_sec(f"{sec_num}.  Key Strengths", sec_sty))
    for s in (report_data.get("top_strengths") or []):
        if isinstance(s, dict):
            strength = _esc(s.get("strength", ""))
            evidence = _esc(s.get("evidence", ""))
            relevance = _esc(s.get("rmc_relevance", ""))
            story.append(Paragraph(f"&#10003;  <b>{strength}</b>", bullet_sty))
            if evidence:
                story.append(Paragraph(f"<i>Evidence: {evidence}</i>", ital_sty))
            if relevance:
                story.append(Paragraph(f"RMC Relevance: {relevance}", ital_sty))
            story.append(Spacer(1, 0.15*cm))
        else:
            story.append(Paragraph(f"&#10003;  {_esc(s)}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 7. DEVELOPMENT AREAS (enhanced) ─────────────────────────────────
    sec_num = 7
    story.append(_sec(f"{sec_num}.  Key Development Areas", sec_sty))
    for d in (report_data.get("development_areas") or []):
        if isinstance(d, dict):
            area     = _esc(d.get("area", ""))
            evidence = _esc(d.get("evidence", ""))
            context  = _esc(d.get("rmc_context", ""))
            priority = d.get("priority", "").lower()
            pri_tag  = f" [{priority.upper()}]" if priority else ""
            story.append(Paragraph(f"&#9658;  <b>{area}{pri_tag}</b>", bullet_sty))
            if evidence:
                story.append(Paragraph(f"<i>Evidence: {evidence}</i>", ital_sty))
            if context:
                story.append(Paragraph(f"RMC Context: {context}", ital_sty))
            story.append(Spacer(1, 0.15*cm))
        else:
            story.append(Paragraph(f"&#9658;  {_esc(d)}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 8. CROSS-COMPETENCY INSIGHTS (NEW) ──────────────────────────────
    insights = report_data.get("cross_competency_insights") or []
    if insights:
        story.append(_sec("8.  Cross-Competency Behavioral Insights", sec_sty))
        ins_rows = [[
            Paragraph("<b>Pattern</b>", label_sty),
            Paragraph("<b>Evidence</b>", label_sty),
            Paragraph("<b>Implication</b>", label_sty),
        ]]
        for item in insights:
            if isinstance(item, dict):
                ins_rows.append([
                    Paragraph(_esc(item.get("pattern", "")), body_sty),
                    Paragraph(f"<i>{_esc(item.get('evidence', ''))}</i>", body_sty),
                    Paragraph(_esc(item.get("implication", "")), body_sty),
                ])
        if len(ins_rows) > 1:
            ins_t = Table(ins_rows, colWidths=[4.5*cm, 6.5*cm, 6*cm])
            ins_t.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),RDC_LIGHT_BLUE),
                ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, RDC_LIGHT_GREY]),
                ("VALIGN",(0,0),(-1,-1),"TOP"),
                ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
                ("LEFTPADDING",(0,0),(-1,-1),6),
            ]))
            story.append(ins_t)
        story.append(Spacer(1, 0.3*cm))

    # ── 9. RECOMMENDED ACTIONS ──────────────────────────────────────────
    story.append(_sec("9.  Recommended Development Actions", sec_sty))
    for i, a in enumerate((report_data.get("development_actions") or []), 1):
        story.append(Paragraph(f"{i}.  {_esc(a)}", bullet_sty))
    story.append(Spacer(1, 0.3*cm))

    # ── 10. 30-60-90 COACHING PLAN ──────────────────────────────────────
    story.append(_sec("10.  30 – 60 – 90 Day Coaching Plan", sec_sty))
    coaching = report_data.get("coaching_plan_30_60_90") or {}
    coach_rows = [[Paragraph("<b>Phase</b>", label_sty), Paragraph("<b>Focus Areas</b>", label_sty)]]
    for phase, lbl in [("30_days","First 30 Days"),("60_days","31 – 60 Days"),("90_days","61 – 90 Days")]:
        items = coaching.get(phase) or []
        coach_rows.append([
            Paragraph(f"<b>{lbl}</b>", label_sty),
            Paragraph("<br/>".join(f"&bull; {_esc(x)}" for x in items), body_sty),
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

    # ── 11. FINAL READINESS STATEMENT ───────────────────────────────────
    story.append(_sec("11.  Final Readiness Statement", sec_sty))
    rbox = Table([[Paragraph(_esc(readiness), sty("RB", fontName="Helvetica-Bold", fontSize=14,
                                             textColor=readiness_col, alignment=TA_CENTER))]],
                 colWidths=[17*cm])
    rbox.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),2,readiness_col),
        ("BACKGROUND",(0,0),(-1,-1),RDC_LIGHT_GREY),
        ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12),
    ]))
    story.append(rbox)

    # ── 12. CANDIDATE RESPONSE APPENDIX (NEW) ───────────────────────────
    appendix = report_data.get("transcript_appendix") or []
    if appendix:
        story.append(PageBreak())
        story.append(_sec("12.  Candidate Response Appendix", sec_sty))
        story.append(Paragraph(
            "Verbatim responses captured during the assessment. Each block shows the "
            "situation presented, the candidate's own words, and the score awarded.",
            ital_sty,
        ))
        story.append(Spacer(1, 0.3*cm))

        sit_sty = sty("QS", fontName="Helvetica-Oblique", fontSize=9,
                      textColor=RDC_DARK_GREY, leading=13, leftIndent=6, rightIndent=6,
                      spaceAfter=2)
        trans_sty = sty("QT", fontName="Helvetica", fontSize=10,
                        textColor=RDC_DARK_GREY, leading=14, leftIndent=6, rightIndent=6,
                        spaceAfter=2, alignment=TA_LEFT)
        qhdr_sty = sty("QH", fontName="Helvetica-Bold", fontSize=10,
                       textColor=WHITE, leading=13)
        score_sty = sty("QScore", fontName="Helvetica-Bold", fontSize=10,
                        textColor=WHITE, alignment=TA_CENTER)
        flag_sty = sty("QF", fontName="Helvetica-Oblique", fontSize=8,
                       textColor=RDC_MID_GREY, leftIndent=6, spaceAfter=6)
        resp_lbl = sty("QL", fontName="Helvetica-Bold", fontSize=9,
                       textColor=RDC_BLUE, leftIndent=6, spaceAfter=2, spaceBefore=2)

        for item in appendix:
            qn        = item.get("question_number", "?")
            comp      = _esc(item.get("competency", ""))
            situation = _esc(item.get("situation", ""))
            transcript= (item.get("transcript") or "").strip()
            score     = item.get("score", 0)

            if not transcript:
                flag = "Not answered"
            elif len(transcript) < 30:
                flag = f"Partial response ({len(transcript)} chars)"
            else:
                flag = "Answered"

            # Preserve paragraph breaks in the transcript (reportlab HTML)
            transcript_html = _esc(transcript).replace("\n", "<br/>") if transcript else "<i>(no response)</i>"

            # Header row: Q# + competency | score
            q_hdr = Table([
                [
                    Paragraph(f"Q{qn} &nbsp; &mdash; &nbsp; {comp}", qhdr_sty),
                    Paragraph(f"{int(score)} / 10", score_sty),
                ]
            ], colWidths=[13.5*cm, 3.5*cm])
            q_hdr.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), RDC_BLUE),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
                ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ]))

            # Situation box (shaded)
            sit_box = Table(
                [[Paragraph(f"<b>Situation:</b> {situation}", sit_sty)]],
                colWidths=[17*cm],
            )
            sit_box.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1), RDC_LIGHT_GREY),
                ("BOX",(0,0),(-1,-1),0.25,colors.lightgrey),
                ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
                ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
            ]))

            # Keep each Q-block together when possible
            block = KeepTogether([
                q_hdr,
                sit_box,
                Paragraph("Candidate Response:", resp_lbl),
                Paragraph(transcript_html, trans_sty),
                Paragraph(f"Status: {flag}", flag_sty),
                Spacer(1, 0.25*cm),
            ])
            story.append(block)

    # ── FOOTER ──────────────────────────────────────────────────────────
    story += [
        Spacer(1, 0.5*cm),
        HRFlowable(width="100%", thickness=0.5, color=RDC_MID_GREY),
        Paragraph(
            "This report is confidential and intended for authorised RDC management use only. "
            "Powered by RDC SRT Assessment Engine v4.0",
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
