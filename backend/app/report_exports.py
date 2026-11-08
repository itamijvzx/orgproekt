"""Two formats, one ordered content stream. No live DB reads or external resources."""

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate

from app.report_service import LABELS


def blocks(content, evidence, manifest):
    issued = datetime.fromisoformat(manifest["issued_at"]).strftime("%d.%m.%Y %H:%M:%S UTC")
    captured = datetime.fromisoformat(evidence["captured_at"]).strftime("%d.%m.%Y %H:%M:%S UTC")
    result = [
        {"heading": content["title"], "level": 0},
        {
            "text": f"OrgProekt · учебные данные\nВыпуск {manifest['release_id']}\nДата выпуска: {issued}\nАвтор: {manifest['author']}\nРецензент и издатель: {manifest['reviewer']}\nАдресаты: {', '.join(manifest['recipients'])}"
        },
    ]
    if manifest.get("previous_id"):
        result.append({"text": "Исправленный выпуск. Предыдущий отчёт: " + manifest["previous_id"]})
    result.append({"heading": LABELS["conclusion"], "text": content["conclusion"]})
    for field, label in LABELS.items():
        if field != "conclusion":
            result.append({"heading": label, "text": content[field]})
    result.extend(evidence["blocks"])
    result.append(
        {
            "heading": "Проверка выпуска",
            "keep": True,
            "text": f"Заключение рецензента: {manifest['review_note']}\nОснование выпуска: {manifest['release_note']}\nКонтрольная сумма содержания: {manifest['content_sha256']}\nСнимок на: {captured}\nСсылки на источники требуют входа и текущих прав. Скачанный файл является копией выпущенного снимка; реестр исполнения ведётся отдельно.",
        }
    )
    return result


def render(content, evidence, manifest):
    stream = blocks(content, evidence, manifest)
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.75)
    section.left_margin = section.right_margin = Inches(0.8)
    for style_name in ["Normal", "Title", "Heading 1"]:
        style = doc.styles[style_name]
        style.font.name = "DejaVu Sans"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.size = Pt(11 if style_name == "Normal" else 20 if style_name == "Title" else 13)
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        fonts.attrib.clear()
        for script in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn("w:" + script), "DejaVu Sans")
        style.font.bold = style_name != "Normal"
        for border in list(style.element.xpath("./w:pPr/w:pBdr")):
            border.getparent().remove(border)
        if style_name != "Normal":
            style.paragraph_format.space_before = Pt(0 if style_name == "Title" else 13)
            style.paragraph_format.space_after = Pt(14 if style_name == "Title" else 7)
            style.paragraph_format.keep_with_next = True
    doc.styles["Normal"].paragraph_format.space_after = Pt(7)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.15
    doc.core_properties.title = content["title"]
    doc.core_properties.author = manifest["author"]
    footer = section.footer.paragraphs[0]
    doc.styles["Footer"].font.name = "DejaVu Sans"
    doc.styles["Footer"].font.size = Pt(8)
    footer.add_run("OrgProekt · ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    for block in stream:
        if block.get("heading"):
            doc.add_heading(block["heading"], level=block.get("level", 1))
        if block.get("text"):
            lines = block["text"].splitlines()
            for index, line in enumerate(lines):
                paragraph = doc.add_paragraph(line)
                if block.get("keep") and index < len(lines) - 1:
                    paragraph.paragraph_format.keep_with_next = True
        if block.get("url"):
            paragraph = doc.add_paragraph()
            link = OxmlElement("w:hyperlink")
            link.set(
                qn("r:id"), paragraph.part.relate_to(block["url"], RT.HYPERLINK, is_external=True)
            )
            run = OxmlElement("w:r")
            properties = OxmlElement("w:rPr")
            color = OxmlElement("w:color")
            color.set(qn("w:val"), "174C69")
            underline = OxmlElement("w:u")
            underline.set(qn("w:val"), "single")
            properties.extend([color, underline])
            run.append(properties)
            text = OxmlElement("w:t")
            text.text = "Открыть сохранённый источник"
            run.append(text)
            link.append(run)
            paragraph._p.append(link)
    docx = BytesIO()
    doc.save(docx)
    if "ReportSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("ReportSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        )
        pdfmetrics.registerFont(
            TTFont("ReportBold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        )
    normal = ParagraphStyle(
        "Body", fontName="ReportSans", fontSize=10.5, leading=15, spaceAfter=7, splitLongWords=True
    )
    heading = ParagraphStyle(
        "Heading",
        parent=normal,
        fontName="ReportBold",
        fontSize=13,
        leading=17,
        spaceBefore=13,
        spaceAfter=7,
        keepWithNext=True,
    )
    title = ParagraphStyle(
        "Title", parent=heading, fontSize=20, leading=25, spaceBefore=0, spaceAfter=14
    )
    story = []
    for block in stream:
        part = []
        if block.get("heading"):
            part.append(
                Paragraph(escape(block["heading"]), title if block.get("level") == 0 else heading)
            )
        if block.get("text"):
            for line in block["text"].splitlines():
                part.append(Paragraph(escape(line) or " ", normal))
        if block.get("url"):
            part.append(
                Paragraph(
                    f'<link href="{escape(block["url"])}" color="#174C69">Открыть сохранённый источник</link>',
                    normal,
                )
            )
        story.extend([KeepTogether(part)] if block.get("keep") else part)

    def footer_pdf(canvas, _document):
        canvas.saveState()
        canvas.setFont("ReportSans", 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(58, 30, "OrgProekt · учебные данные")
        canvas.drawRightString(letter[0] - 58, 30, str(canvas.getPageNumber()))
        canvas.restoreState()

    pdf = BytesIO()
    SimpleDocTemplate(
        pdf,
        pagesize=letter,
        leftMargin=58,
        rightMargin=58,
        topMargin=48,
        bottomMargin=48,
        title=content["title"],
        author=manifest["author"],
    ).build(story, onFirstPage=footer_pdf, onLaterPages=footer_pdf)
    return pdf.getvalue(), docx.getvalue()
