"""Builder for `create_pdf`. It takes the same document spec as `create_document`."""

import re
from collections.abc import Callable
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from onyx.tools.tool_implementations.office.branding import OfficeBrand
from onyx.tools.tool_implementations.office.docx_builder import (
    BulletListBlock,
    DocumentBlock,
    DocumentSpec,
    HeadingBlock,
    NumberedListBlock,
    ParagraphBlock,
    TableBlock,
)
from onyx.tools.tool_implementations.office.models import (
    clean_text,
    parse_inline_runs,
)

PDF_EXTENSION = ".pdf"

_MARGIN = 2 * cm
_DEFAULT_ACCENT = "1F2937"
_DEFAULT_RULE = "D1D5DB"
_DEFAULT_TINT = "F3F4F6"
_LATIN_FONT = "Helvetica"
_LATIN_BOLD_FONT = "Helvetica-Bold"
# Built-in Adobe CID fonts; they need no font files on disk.
_CHINESE_FONT = "STSong-Light"
_JAPANESE_FONT = "HeiseiKakuGo-W5"
_KOREAN_FONT = "HYGothic-Medium"
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[ᄀ-ᇿ가-힯]")
_WIDE_TABLE_COLUMNS = 8


def _spec_text(spec: DocumentSpec) -> str:
    parts = [spec.title, spec.subtitle or ""]
    for block in spec.blocks:
        if isinstance(block, HeadingBlock | ParagraphBlock):
            parts.append(block.text)
        elif isinstance(block, BulletListBlock | NumberedListBlock):
            parts.extend(block.items)
        elif isinstance(block, TableBlock):
            parts.extend(block.header)
            parts.extend(cell for row in block.rows for cell in row)
    return "\n".join(parts)


def _fonts_for(text: str) -> tuple[str, str]:
    """The (regular, bold) fonts that can draw every character in `text`."""
    try:
        text.encode("cp1252")
        return _LATIN_FONT, _LATIN_BOLD_FONT
    except UnicodeEncodeError:
        pass
    if _KANA.search(text):
        name = _JAPANESE_FONT
    elif _HANGUL.search(text):
        name = _KOREAN_FONT
    else:
        name = _CHINESE_FONT
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
        # A CID font has no bold or italic face, so <b> and <i> reuse it.
        pdfmetrics.registerFontFamily(
            name, normal=name, bold=name, italic=name, boldItalic=name
        )
    return name, name


_BODY_FONT_SIZE = 10.5


class _Styles:
    def __init__(self, font: str, bold_font: str, accent: str) -> None:
        self.font = font
        accent_color = colors.HexColor(f"#{accent}")
        self.title = ParagraphStyle(
            "Title",
            fontName=bold_font,
            fontSize=22,
            leading=27,
            textColor=accent_color,
            spaceAfter=6,
        )
        self.subtitle = ParagraphStyle(
            "Subtitle",
            fontName=font,
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#4B5563"),
            spaceAfter=14,
        )
        self.headings = {
            level: ParagraphStyle(
                f"Heading{level}",
                fontName=bold_font,
                fontSize=size,
                leading=size + 4,
                textColor=accent_color,
                spaceBefore=12,
                spaceAfter=6,
            )
            for level, size in ((1, 16), (2, 13.5), (3, 12))
        }
        self.body = ParagraphStyle(
            "Body",
            fontName=font,
            fontSize=_BODY_FONT_SIZE,
            leading=15,
            alignment=TA_LEFT,
            spaceAfter=6,
        )
        self.cell = ParagraphStyle("Cell", parent=self.body, fontSize=9, leading=12)
        self.header_cell = ParagraphStyle(
            "HeaderCell", parent=self.cell, fontName=bold_font
        )


def _markup(text: str) -> str:
    """Paragraph markup with **bold** and *italic* runs and escaped text."""
    pieces: list[str] = []
    for run in parse_inline_runs(text):
        piece = escape(run.text).replace("\n", "<br/>")
        if run.bold:
            piece = f"<b>{piece}</b>"
        if run.italic:
            piece = f"<i>{piece}</i>"
        pieces.append(piece)
    return "".join(pieces)


def _plain(text: str) -> str:
    return escape(clean_text(text)).replace("\n", "<br/>")


def _list(items: list[str], styles: _Styles, numbered: bool) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(_markup(item), styles.body)) for item in items],
        bulletType="1" if numbered else "bullet",
        bulletFontName=styles.font,
        bulletFontSize=_BODY_FONT_SIZE,
        leftIndent=14,
    )


def _table(
    block: TableBlock,
    styles: _Styles,
    width: float,
    header_fill: str,
    header_text: str,
    rule: str,
    tint: str,
) -> Table:
    cell_style = styles.cell
    header_style = ParagraphStyle(
        "BrandHeaderCell",
        parent=styles.header_cell,
        textColor=colors.HexColor(f"#{header_text}"),
    )
    if len(block.header) > _WIDE_TABLE_COLUMNS:
        cell_style = ParagraphStyle("NarrowCell", parent=cell_style, fontSize=7.5)
        header_style = ParagraphStyle(
            "NarrowHeaderCell", parent=header_style, fontSize=7.5
        )
    data: list[list[Paragraph]] = [
        [Paragraph(_plain(text), header_style) for text in block.header],
        *[[Paragraph(_markup(text), cell_style) for text in row] for row in block.rows],
    ]
    table = Table(
        data, colWidths=[width / len(block.header)] * len(block.header), repeatRows=1
    )
    commands: list[tuple[object, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{header_fill}")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(f"#{rule}")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    band = colors.HexColor(f"#{tint}")
    commands.extend(
        ("BACKGROUND", (0, row_index), (-1, row_index), band)
        for row_index in range(2, len(data), 2)
    )
    table.setStyle(TableStyle(commands))
    return table


def _flowables(
    spec: DocumentSpec,
    styles: _Styles,
    width: float,
    brand: OfficeBrand | None,
) -> list[Flowable]:
    story: list[Flowable] = [Paragraph(_plain(spec.title), styles.title)]
    if spec.subtitle:
        story.append(Paragraph(_plain(spec.subtitle), styles.subtitle))
    else:
        story.append(Spacer(1, 8))
    for block in spec.blocks:
        story.extend(_block_flowables(block, styles, width, brand))
    return story


def _block_flowables(
    block: DocumentBlock,
    styles: _Styles,
    width: float,
    brand: OfficeBrand | None,
) -> list[Flowable]:
    if isinstance(block, HeadingBlock):
        return [Paragraph(_plain(block.text), styles.headings[block.level])]
    if isinstance(block, ParagraphBlock):
        return [Paragraph(_markup(block.text), styles.body)]
    if isinstance(block, BulletListBlock | NumberedListBlock):
        numbered = isinstance(block, NumberedListBlock)
        return [_list(block.items, styles, numbered), Spacer(1, 4)]
    if isinstance(block, TableBlock):
        table = _table(
            block,
            styles,
            width,
            header_fill=brand.primary if brand else _DEFAULT_TINT,
            header_text=brand.on_primary if brand else _DEFAULT_ACCENT,
            rule=brand.rule if brand else _DEFAULT_RULE,
            tint=brand.tint if brand else "FAFAFA",
        )
        return [table, Spacer(1, 8)]
    return [PageBreak()]


_PageCallback = Callable[[Canvas, SimpleDocTemplate], None]


def _footer(font: str, brand: OfficeBrand | None) -> _PageCallback:
    def draw(canvas: Canvas, doc: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawRightString(A4[0] - _MARGIN, _MARGIN / 2, str(doc.page))
        if brand is not None:
            canvas.drawString(_MARGIN, _MARGIN / 2, brand.name)
            canvas.setFillColor(colors.HexColor(f"#{brand.primary}"))
            canvas.rect(0, A4[1] - 6, A4[0], 6, stroke=0, fill=1)
        canvas.restoreState()

    return draw


def build_pdf(spec: DocumentSpec, brand: OfficeBrand | None = None) -> bytes:
    font, bold_font = _fonts_for(_spec_text(spec))
    styles = _Styles(font, bold_font, brand.primary if brand else _DEFAULT_ACCENT)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=clean_text(spec.title),
        author=brand.name if brand else "",
    )
    footer = _footer(font, brand)
    doc.build(
        _flowables(spec, styles, doc.width, brand),
        onFirstPage=footer,
        onLaterPages=footer,
    )
    return buffer.getvalue()


def summarize_pdf(spec: DocumentSpec) -> str:
    headings = sum(isinstance(block, HeadingBlock) for block in spec.blocks)
    tables = sum(isinstance(block, TableBlock) for block in spec.blocks)
    return (
        f"PDF document '{spec.title}' with {len(spec.blocks)} blocks "
        f"({headings} headings, {tables} tables)."
    )
