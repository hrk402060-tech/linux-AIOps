from io import BytesIO
from html import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from docx.shared import RGBColor
from docx.oxml.ns import qn
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.ttfonts import TTFError
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPORT_FONT = "ReportCJK"
SEVERITY_LABELS = {
    "critical": "严重", "high": "高危", "medium": "中危", "low": "低危",
}
DISPOSITION_LABELS = {
    "open": "待处理", "pending_verification": "待验证", "fixed": "已修复",
    "ignored": "已忽略", "risk_accepted": "风险接受",
}


def build_document(
    file_format: str, title: str, metadata: dict[str, str],
    summary: dict[str, int], findings: list[dict[str, object]],
) -> bytes:
    if file_format == "pdf":
        return _build_pdf(title, metadata, summary, findings)
    if file_format == "docx":
        return _build_docx(title, metadata, summary, findings)
    raise ValueError("不支持的报告格式")


def _build_pdf(
    title: str, metadata: dict[str, str], summary: dict[str, int],
    findings: list[dict[str, object]],
) -> bytes:
    font_name = _register_pdf_font()
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ChineseBody", parent=styles["BodyText"], fontName=font_name,
        fontSize=9.5, leading=14, wordWrap="CJK",
    )
    heading = ParagraphStyle(
        "ChineseHeading", parent=body, fontSize=16, leading=22,
        alignment=TA_CENTER, spaceAfter=12,
    )
    story = [Paragraph("Linux 漏洞扫描报告", heading), Paragraph(escape(title), body)]
    story.extend([Spacer(1, 8), _pdf_metadata(metadata, body), Spacer(1, 10)])
    story.extend([Paragraph("风险摘要", body), Spacer(1, 4), _pdf_summary(summary, body)])
    story.extend([Spacer(1, 10), Paragraph("漏洞明细", body), Spacer(1, 4)])
    story.append(_pdf_findings(findings, body))
    story.extend([Spacer(1, 10), Paragraph("证据链", body), Spacer(1, 4)])
    story.extend(_pdf_evidence(findings, body))
    def footer(canvas: object, doc: object) -> None:
        _pdf_footer(canvas, doc, font_name)

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def _pdf_metadata(metadata: dict[str, str], style: ParagraphStyle) -> Table:
    rows = [[Paragraph(escape(key), style), Paragraph(escape(value), style)]
            for key, value in metadata.items()]
    return _styled_table(rows, [35 * mm, 120 * mm], header=False)


def _pdf_summary(summary: dict[str, int], style: ParagraphStyle) -> Table:
    labels = ["严重", "高危", "中危", "低危", "需人工确认"]
    keys = ["critical", "high", "medium", "low", "manual_confirmation"]
    rows = [[Paragraph(item, style) for item in labels], [summary.get(key, 0) for key in keys]]
    return _styled_table(rows, [31 * mm] * 5, header=True)


def _pdf_findings(findings: list[dict[str, object]], style: ParagraphStyle) -> Table:
    header = ["资产", "CVE", "等级", "软件包版本", "处置状态"]
    rows: list[list[object]] = [[Paragraph(item, style) for item in header]]
    for item in findings:
        version = f"{item.get('package_name', '')} {item.get('installed_version', '')} -> {item.get('fixed_version', '')}"
        severity = SEVERITY_LABELS.get(str(item.get("severity")), str(item.get("severity", "")))
        disposition = DISPOSITION_LABELS.get(
            str(item.get("disposition_status", "open")),
            str(item.get("disposition_status", "open")),
        )
        values = [item.get("asset_name"), item.get("cve_id"), severity, version, disposition]
        rows.append([Paragraph(escape(str(value or "")), style) for value in values])
    if len(rows) == 1:
        rows.append([Paragraph("未发现漏洞", style), "", "", "", ""])
    return _styled_table(rows, [27 * mm, 30 * mm, 17 * mm, 58 * mm, 23 * mm], header=True)


def _pdf_evidence(
    findings: list[dict[str, object]], style: ParagraphStyle
) -> list[Paragraph]:
    if not findings:
        return [Paragraph("未发现漏洞，无证据链。", style)]
    rows: list[Paragraph] = []
    for item in findings:
        steps = item.get("evidence_chain", [])
        values = [str(step.get("value", "")) for step in steps if isinstance(step, dict)]
        fallback = [
            str(item.get("source_name", "")), str(item.get("advisory_id", "")),
            str(item.get("installed_version", "")), str(item.get("fixed_version", "")),
            str(item.get("conclusion", "")),
        ]
        chain = " -> ".join(value for value in (values or fallback) if value)
        rows.append(Paragraph(escape(f"{item.get('cve_id', '')}: {chain}"), style))
    return rows


def _styled_table(rows: list[list[object]], widths: list[float], header: bool) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands.extend([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#285D49")),
                         ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)])
    table.setStyle(TableStyle(commands))
    return table


def _pdf_footer(canvas: object, document: object, font_name: str) -> None:
    canvas.saveState()
    canvas.setFont(font_name, 8)
    canvas.setFillColor(colors.HexColor("#66716B"))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, f"第 {document.page} 页")
    canvas.restoreState()


def _register_pdf_font() -> str:
    font_path = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    try:
        pdfmetrics.registerFont(TTFont(REPORT_FONT, font_path, subfontIndex=2))
        return REPORT_FONT
    except (OSError, TTFError):
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"


def _build_docx(
    title: str, metadata: dict[str, str], summary: dict[str, int],
    findings: list[dict[str, object]],
) -> bytes:
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.7)
    section.left_margin = section.right_margin = Inches(0.75)
    first = document.add_paragraph("Linux 漏洞扫描报告")
    first.style = document.styles["Title"]
    first.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_style = document.styles["Title"]
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    title_properties = title_style.element.get_or_add_pPr()
    border = title_properties.find(qn("w:pBdr"))
    if border is not None:
        title_properties.remove(border)
    document.add_paragraph(title)
    _docx_table(document, [[key, value] for key, value in metadata.items()], [1.5, 5.2])
    document.add_heading("风险摘要", level=1)
    _docx_table(document, [["严重", "高危", "中危", "低危", "需人工确认"], [
        str(summary.get(key, 0)) for key in
        ("critical", "high", "medium", "low", "manual_confirmation")
    ]], [1.25] * 5, header=True)
    document.add_heading("漏洞明细", level=1)
    rows = [["资产", "CVE", "等级", "软件包版本", "处置状态"]]
    for item in findings:
        version = f"{item.get('package_name', '')} {item.get('installed_version', '')} -> {item.get('fixed_version', '')}"
        severity = SEVERITY_LABELS.get(str(item.get("severity")), str(item.get("severity", "")))
        disposition = DISPOSITION_LABELS.get(
            str(item.get("disposition_status", "open")),
            str(item.get("disposition_status", "open")),
        )
        rows.append([str(item.get(key, "")) for key in ("asset_name", "cve_id")]
                    + [severity, version, disposition])
    _docx_table(document, rows if len(rows) > 1 else [rows[0], ["未发现漏洞", "", "", "", ""]],
                [1.1, 1.25, 0.65, 2.25, 0.95], header=True)
    document.add_heading("证据链", level=1)
    if not findings:
        document.add_paragraph("未发现漏洞，无证据链。")
    for item in findings:
        steps = item.get("evidence_chain", [])
        values = [str(step.get("value", "")) for step in steps if isinstance(step, dict)]
        fallback = [str(item.get(key, "")) for key in (
            "source_name", "advisory_id", "installed_version",
            "fixed_version", "conclusion",
        )]
        chain = " -> ".join(value for value in (values or fallback) if value)
        document.add_paragraph(f"{item.get('cve_id', '')}: {chain}", style="List Bullet")
    for style_name in ("Normal", "Title", "Heading 1"):
        document.styles[style_name].font.name = "SimSun"
        document.styles[style_name].font.color.rgb = RGBColor(0, 0, 0)
        document.styles[style_name].font.size = Pt(10.5 if style_name == "Normal" else 14)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _docx_table(
    document: Document, rows: list[list[str]], widths: list[float], header: bool = False,
) -> None:
    table = document.add_table(rows=len(rows), cols=len(widths))
    table.style = "Table Grid"
    for row_index, values in enumerate(rows):
        for index, value in enumerate(values):
            cell = table.cell(row_index, index)
            cell.text = value
            cell.width = Inches(widths[index])
            if header and row_index == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True
