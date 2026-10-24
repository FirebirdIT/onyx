from io import BytesIO
from typing import Any

from pypdf import PdfReader

from onyx.tools.tool_implementations.office.branding import DEEPX_OFFICE_BRAND
from onyx.tools.tool_implementations.office.docx_builder import DocumentSpec
from onyx.tools.tool_implementations.office.pdf_builder import build_pdf, summarize_pdf


def _spec(**overrides: Any) -> DocumentSpec:
    payload: dict[str, Any] = {
        "file_name": "Quarterly Report",
        "title": "Quarterly Report",
        "subtitle": "Q3 2026",
        "blocks": [
            {"type": "heading", "text": "Summary", "level": 1},
            {"type": "paragraph", "text": "Revenue grew **12%** in *Q3* & <more>."},
            {"type": "bullet_list", "items": ["New region", "New product"]},
            {"type": "numbered_list", "items": ["Hire", "Ship"]},
            {"type": "page_break"},
            {"type": "heading", "text": "Numbers", "level": 2},
            {
                "type": "table",
                "header": ["Region", "Revenue"],
                "rows": [["North", "1.2M"], ["South", "0.8M"], ["East", "0.5M"]],
            },
        ],
    }
    payload.update(overrides)
    return DocumentSpec.model_validate(payload)


def _pages_text(content: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(BytesIO(content)).pages]


def test_builds_pdf_with_every_block() -> None:
    content = build_pdf(_spec(), brand=None)
    assert content.startswith(b"%PDF")
    pages = _pages_text(content)
    # The page break starts a second page.
    assert len(pages) == 2
    assert "Quarterly Report" in pages[0]
    assert "Q3 2026" in pages[0]
    assert "Revenue grew 12% in Q3 & <more>." in pages[0]
    assert "**" not in pages[0]
    assert "New product" in pages[0]
    assert "Numbers" in pages[1]
    assert "South" in pages[1]
    metadata = PdfReader(BytesIO(content)).metadata
    assert metadata is not None
    assert metadata.title == "Quarterly Report"


def test_brand_adds_footer_and_author() -> None:
    branded = build_pdf(_spec(), brand=DEEPX_OFFICE_BRAND)
    assert "Deep X" in _pages_text(branded)[0]
    metadata = PdfReader(BytesIO(branded)).metadata
    assert metadata is not None
    assert metadata.author == "Deep X"

    plain = build_pdf(_spec(), brand=None)
    assert "Deep X" not in _pages_text(plain)[0]


def test_cjk_text_uses_a_cid_font() -> None:
    spec = _spec(
        title="季度报告",
        subtitle=None,
        blocks=[{"type": "paragraph", "text": "收入增长 **12%**"}],
    )
    content = build_pdf(spec, brand=None)
    assert b"STSong-Light" in content
    assert "季度报告" in _pages_text(content)[0]


def test_latin_text_keeps_the_standard_font() -> None:
    content = build_pdf(_spec(), brand=None)
    assert b"Helvetica" in content
    assert b"STSong-Light" not in content


def test_wide_table_builds() -> None:
    header = [f"C{i}" for i in range(20)]
    spec = _spec(blocks=[{"type": "table", "header": header, "rows": [header] * 50}])
    assert build_pdf(spec, brand=None).startswith(b"%PDF")


def test_summary() -> None:
    assert summarize_pdf(_spec()) == (
        "PDF document 'Quarterly Report' with 7 blocks (2 headings, 1 tables)."
    )
