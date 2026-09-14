"""The PQI assessment report as a PDF, for HR and authorised assessors only.

Plant Manager's PDF (pdf_generator.py) is left exactly as it is; this module
borrows only its colours and small helpers.
"""
import datetime
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pdf_generator import (
    RDC_BLUE, RDC_DARK_GREY, RDC_LIGHT_BLUE, RDC_LIGHT_GREY, RDC_MID_GREY, WHITE, HexColor, _esc, _sec,
)

BAND_COLOURS = {
    "High Readiness": HexColor("#0F5A0F"),
    "Ready":          HexColor("#1A7A1A"),
    "Developing":     HexColor("#CC7700"),
    "Not Yet Ready":  HexColor("#CC0000"),
}
AMBER_BG = HexColor("#FFF4E0")
RED_BG = HexColor("#FDECEC")


def _num(value, digits=1):
    return "—" if value is None else f"{value:.{digits}f}"


def generate_pqi_pdf(report: dict, candidate: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm,
                            topMargin=2.2 * cm, bottomMargin=2 * cm,
                            title="RDC PQI Competency Assessment Report")
    base = getSampleStyleSheet()

    def sty(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    title = sty("PT", fontName="Helvetica-Bold", fontSize=17, textColor=WHITE, alignment=TA_CENTER, spaceAfter=3)
    sub = sty("PSu", fontName="Helvetica", fontSize=10, textColor=HexColor("#CCE0F5"), alignment=TA_CENTER)
    conf = sty("PC", fontName="Helvetica-Oblique", fontSize=9, textColor=HexColor("#FF9999"), alignment=TA_CENTER)
    sec = sty("PS", fontName="Helvetica-Bold", fontSize=12, textColor=WHITE, leftIndent=8)
    body = sty("PB", fontName="Helvetica", fontSize=10, textColor=RDC_DARK_GREY, leading=14, spaceAfter=4,
               alignment=TA_JUSTIFY)
    small = sty("PSm", fontName="Helvetica", fontSize=8.5, textColor=RDC_DARK_GREY, leading=11.5)
    label = sty("PL", fontName="Helvetica-Bold", fontSize=9.5, textColor=RDC_BLUE)
    value = sty("PV", fontName="Helvetica", fontSize=9.5, textColor=RDC_DARK_GREY)
    big = sty("PBig", fontName="Helvetica-Bold", fontSize=22, textColor=RDC_BLUE, alignment=TA_CENTER, leading=26)
    big_lbl = sty("PBl", fontName="Helvetica", fontSize=8.5, textColor=RDC_MID_GREY, alignment=TA_CENTER, leading=11)
    bullet = sty("PBu", fontName="Helvetica", fontSize=10, textColor=RDC_DARK_GREY, leftIndent=12, leading=14,
                 spaceAfter=2)
    ital = sty("PI", fontName="Helvetica-Oblique", fontSize=9, textColor=RDC_MID_GREY, leftIndent=12, leading=12,
               spaceAfter=4)
    footer = sty("PF", fontName="Helvetica-Oblique", fontSize=8, textColor=RDC_MID_GREY, alignment=TA_CENTER)

    band = report.get("overall_readiness") or "—"
    band_colour = BAND_COLOURS.get(band, RDC_DARK_GREY)
    master = report.get("master") or {}
    readiness = report.get("readiness") or {}
    story = []

    header = Table([
        [Paragraph("RDC Plant Quality Incharge (PQI)", title)],
        [Paragraph("Competency Assessment Report", title)],
        [Paragraph(f"SRT – Situation Reaction Test  |  30 Situations  |  10 Competencies  |  "
                   f"Master {_esc(master.get('version', ''))} ({_esc(master.get('status', ''))})", sub)],
        [Paragraph("CONFIDENTIAL – HR / Authorised Assessors Only", conf)],
    ], colWidths=[17 * cm])
    header.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RDC_BLUE),
                                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [header, Spacer(1, 0.4 * cm)]

    # 1. Candidate
    story.append(_sec("1.  Candidate Information", sec))
    info = [
        ("Name:", candidate.get("candidate_name", "")),
        ("Plant / Location:", candidate.get("plant_location", "")),
        ("Assessment Date:", candidate.get("assessment_date", "")),
        ("Report Generated:", datetime.date.today().strftime("%d %B %Y")),
        ("Master / Rubric:", f"{master.get('version', '')} · {master.get('rubric_version', '')} · "
                             f"file hash {str(master.get('sha256', ''))[:12]}…"),
        ("Evaluator:", f"{report.get('evaluator_model', '')} · prompt {report.get('prompt_version', '')}"),
    ]
    table = Table([[Paragraph(k, label), Paragraph(_esc(v), value)] for k, v in info], colWidths=[4.2 * cm, 12.8 * cm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_GREY),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [table, Spacer(1, 0.35 * cm)]

    # 2. Headline scores and readiness
    story.append(_sec("2.  Headline Scores", sec))
    afi_label = "Anti-Firefighting Index /100"
    if report.get("afi_band"):
        afi_label += f"<br/>{_esc(report['afi_band'])}"
    scores = Table([
        [Paragraph(_num(report.get("overall_pqi_score")), big), Paragraph(_num(report.get("technical_acumen")), big),
         Paragraph(_num(report.get("business_acumen")), big), Paragraph(_num(report.get("anti_firefighting_index")), big)],
        [Paragraph(f"Overall PQI Score /100<br/>(attainable maximum {report.get('overall_attainable_max', 90)})", big_lbl),
         Paragraph("Technical Acumen /100", big_lbl), Paragraph("Business Acumen /100", big_lbl),
         Paragraph(afi_label, big_lbl)],
    ], colWidths=[4.25 * cm] * 4)
    scores.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_BLUE), ("BOX", (0, 0), (-1, -1), 1, RDC_BLUE),
                                ("LINEAFTER", (0, 0), (2, -1), 0.5, colors.lightgrey),
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [scores, Spacer(1, 0.25 * cm)]

    ready_rows = [[Paragraph(f"Readiness: {_esc(band)}", sty("PR", fontName="Helvetica-Bold", fontSize=14,
                                                              textColor=band_colour, alignment=TA_CENTER))]]
    if readiness.get("score_band") and readiness.get("score_band") != band:
        ready_rows.append([Paragraph(f"Score band {_esc(readiness['score_band'])}, capped by guardrail.", small)])
    for cap in readiness.get("caps") or []:
        ready_rows.append([Paragraph("Guardrail: " + _esc(cap.get("reason", "")), small)])
    ready_rows.append([Paragraph(_esc(readiness.get("provisional_note", "")), small)])
    ready_box = Table(ready_rows, colWidths=[17 * cm])
    ready_box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.5, band_colour),
                                   ("BACKGROUND", (0, 0), (-1, -1), RDC_LIGHT_GREY),
                                   ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(ready_box)
    if readiness.get("manual_review_required"):
        review = Table([[Paragraph("<b>MANUAL REVIEW REQUIRED</b><br/>" + "<br/>".join(
            _esc(r) for r in readiness.get("manual_review_reasons") or []), small)]], colWidths=[17 * cm])
        review.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AMBER_BG),
                                    ("BOX", (0, 0), (-1, -1), 1, HexColor("#CC7700")),
                                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [Spacer(1, 0.15 * cm), review]
    story.append(Spacer(1, 0.35 * cm))

    # 3. Competency profile
    story.append(_sec("3.  Competency Profile", sec))
    rows = [[Paragraph("<b>Code</b>", label), Paragraph("<b>Competency</b>", label),
             Paragraph("<b>Lens</b>", label), Paragraph("<b>Score /10</b>", label)]]
    for c in report.get("competency_scores") or []:
        rows.append([Paragraph(_esc(c["code"]), value), Paragraph(_esc(c["name"]), value),
                     Paragraph(_esc(f"{c['lens']} ×{c['weight']:g}"), value), Paragraph(_num(c["score"]), value)])
    table = Table(rows, colWidths=[1.5 * cm, 10.3 * cm, 2.9 * cm, 2.3 * cm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), RDC_LIGHT_BLUE),
                               ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, RDC_LIGHT_GREY]),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [table, Spacer(1, 0.35 * cm)]

    # 4. Critical flags
    story.append(_sec("4.  Confirmed Critical Flags", sec))
    flags = report.get("critical_flags") or []
    if flags:
        rows = [[Paragraph("<b>SRT</b>", label), Paragraph("<b>Severity / Type</b>", label),
                 Paragraph("<b>Reason</b>", label)]]
        for f in flags:
            rows.append([Paragraph(_esc(f"{f['srt_id']} (Q{f['question_number']})"), value),
                         Paragraph(_esc(f"{f['severity']} / {f['type']}"), value), Paragraph(_esc(f["reason"]), value)])
        table = Table(rows, colWidths=[3 * cm, 3.6 * cm, 10.4 * cm])
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), RED_BG),
                                   ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
    else:
        story.append(Paragraph("No Critical Failure was confirmed.", body))
    story.append(Spacer(1, 0.35 * cm))

    # 5–7. Narrative sections
    story.append(_sec("5.  Top Strengths", sec))
    for s in report.get("top_strengths") or []:
        story.append(Paragraph(f"&#10003;  <b>{_esc(s.get('title', ''))}</b>", bullet))
        story.append(Paragraph(_esc(s.get("evidence", "")) + (f"  [{_esc(', '.join(s['srt_ids']))}]" if s.get("srt_ids") else ""), ital))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_sec("6.  Development Priorities", sec))
    for p in report.get("development_priorities") or []:
        kind = f" [{p['gap_type'].upper()}]" if p.get("gap_type") else ""
        story.append(Paragraph(f"&#9658;  <b>{_esc(p.get('title', ''))}{_esc(kind)}</b>", bullet))
        story.append(Paragraph(_esc(p.get("evidence", "")) + (f"  [{_esc(', '.join(p['srt_ids']))}]" if p.get("srt_ids") else ""), ital))
    story.append(Spacer(1, 0.2 * cm))
    story.append(_sec("7.  Development Narrative", sec))
    for para in str(report.get("development_narrative") or "").split("\n"):
        if para.strip():
            story.append(Paragraph(_esc(para.strip()), body))

    # 8. Review trail
    review = report.get("second_review") or {}
    story += [Spacer(1, 0.2 * cm), _sec("8.  Evaluation Quality Controls", sec)]
    triggers = ", ".join(t.replace("_", " ") for t in review.get("triggers") or []) or "none"
    story.append(Paragraph(f"Second review triggers: {_esc(triggers)}. SRTs reviewed: "
                           f"{len(review.get('reviewed_srts') or [])}. Score changes on review: "
                           f"{len(review.get('changes') or [])}.", body))
    for change in review.get("changes") or []:
        story.append(Paragraph(_esc(f"{change['srt_id']}: {change['from_score']} → {change['to_score']}"), ital))
    consistency = report.get("consistency_check") or {}
    for item in consistency.get("contradictions") or []:
        story.append(Paragraph(_esc(f"Contradiction noted ({', '.join(item['srt_ids'])}): {item['description']}"), ital))

    # Appendix
    story.append(PageBreak())
    story.append(_sec("Appendix — SRT Responses and Evaluations", sec))
    story.append(Paragraph("Each SRT is scored 0–9 (10 is a reserved ceiling). AFI evidence is scored 0–5 separately "
                           "and never added to the SRT score.", ital))
    q_hdr = sty("PQH", fontName="Helvetica-Bold", fontSize=9.5, textColor=WHITE, leading=12)
    q_score = sty("PQS", fontName="Helvetica-Bold", fontSize=9.5, textColor=WHITE, alignment=TA_CENTER)
    for r in report.get("srt_results") or []:
        head = Table([[Paragraph(_esc(f"Q{r['question_number']} · {r['srt_id']} · {r['primary_competency']} "
                                      f"{r.get('competency_name', '')}"), q_hdr),
                       Paragraph(f"Score {r['final_score']}", q_score)]], colWidths=[13.8 * cm, 3.2 * cm])
        head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RDC_BLUE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        response = r.get("response") or ""
        response_html = _esc(response).replace("\n", "<br/>") if response.strip() else "<i>(no response)</i>"
        decision = r["decision_made"] + (" — decision cap applied" if "decision" in (r.get("caps_applied") or []) else "")
        afi = ("not scored" if not r["afi_activated"]
               else f"level {r['afi_evidence_level']} of 5") + f" (applicability {r['afi_applicability']})"
        lines = [
            f"<b>Core level</b> {r['core_response_level']}  ·  <b>Lift</b> {r['excellence_lift']}  ·  "
            f"<b>Decision made</b> {_esc(decision)}  ·  <b>AFI</b> {_esc(afi)}",
        ]
        if r["critical_failure"]:
            lines.append(f"<b>Critical Failure</b> ({_esc(r['critical_failure_severity'])} / "
                         f"{_esc(r['critical_failure_type'])}): {_esc(r['critical_failure_reason'])}")
        if r.get("first_pass") and r["first_pass"].get("final_score") != r["final_score"]:
            lines.append(f"<b>Second review</b> changed the score from {r['first_pass']['final_score']}.")
        block = [
            head,
            Paragraph(f"<b>Situation:</b> {_esc(r['situation'])}", small),
            Paragraph(f"<b>Response</b>{' (' + _esc(r['response_capture']) + ')' if r.get('response_capture') else ''}:"
                      f" {response_html}", small),
            Paragraph("<br/>".join(lines), small),
            Paragraph("<b>Justification:</b> " + _esc(" ".join(r.get("score_justification") or [])), small),
            Paragraph("<b>Primary gap:</b> " + _esc(r.get("primary_gap") or ""), small),
            Spacer(1, 0.25 * cm),
        ]
        story.append(KeepTogether(block))

    story += [Spacer(1, 0.4 * cm), Paragraph(
        "Confidential. For HR and authorised assessors only. Readiness bands are provisional until pilot calibration. "
        "RDC SRT Assessment Engine — PQI.", footer)]
    doc.build(story)
    return buf.getvalue()
