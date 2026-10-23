"""Shared limits, text helpers and result model for the Office file tools."""

import re
from typing import Literal

from pydantic import BaseModel, Field

# Input bounds shared by every format. Format-specific bounds live beside
# each builder.
MAX_FILE_NAME_LENGTH = 120
MAX_TITLE_LENGTH = 300
MAX_TEXT_LENGTH = 10_000
MAX_CELL_TEXT_LENGTH = 2_000
MAX_NOTES_LENGTH = 5_000
# Input file names longer than this are refused; saved names are shorter.
MAX_FILE_NAME_INPUT_LENGTH = 2 * MAX_FILE_NAME_LENGTH
# Upper bound for the built file. A larger file is not saved.
MAX_OUTPUT_BYTES = 20 * 1024 * 1024

DEFAULT_FILE_STEM = "file"

# Characters that are unsafe in a download name on common file systems.
_UNSAFE_FILE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\:\*\?\"<>\|]+")
_WHITESPACE_RUN = re.compile(r"\s+")
# XML 1.0 forbids these control characters; python-docx, python-pptx and
# openpyxl raise on them, so they are removed before building.
_XML_INVALID_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
# **bold** or *italic*, non-greedy, no nesting.
_INLINE_MARKUP = re.compile(r"\*\*(.+?)\*\*|\*(?!\s)(.+?)(?<!\s)\*")


def sanitize_file_name(file_name: str, extension: str) -> str:
    """Return a safe download name that ends with `extension` (e.g. ".docx").

    Path parts are dropped, unsafe characters are replaced, and any extension
    the model supplied is replaced with the format's own one."""
    base = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_FILE_NAME_CHARS.sub("_", base)
    base = _WHITESPACE_RUN.sub(" ", base).strip().strip(".").strip()
    stem, dot, suffix = base.rpartition(".")
    if dot and stem and len(suffix) <= 5 and suffix.isalnum():
        base = stem.rstrip(". ")
    if not base:
        base = DEFAULT_FILE_STEM
    base = base[: MAX_FILE_NAME_LENGTH - len(extension)].rstrip(". ")
    return f"{base or DEFAULT_FILE_STEM}{extension}"


def clean_text(text: str) -> str:
    """Remove characters that cannot be stored in Office XML."""
    return _XML_INVALID_CHARS.sub("", text)


class TextRun(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False


def parse_inline_runs(text: str) -> list[TextRun]:
    """Split simple **bold** and *italic* markup into styled runs."""
    cleaned = clean_text(text)
    runs: list[TextRun] = []
    position = 0
    for match in _INLINE_MARKUP.finditer(cleaned):
        if match.start() > position:
            runs.append(TextRun(text=cleaned[position : match.start()]))
        if match.group(1) is not None:
            runs.append(TextRun(text=match.group(1), bold=True))
        else:
            runs.append(TextRun(text=match.group(2), italic=True))
        position = match.end()
    if position < len(cleaned):
        runs.append(TextRun(text=cleaned[position:]))
    return runs


def plain_text(text: str) -> str:
    """The text without inline markup, for places that cannot hold runs."""
    return "".join(run.text for run in parse_inline_runs(text))


class OfficeFileSpec(BaseModel):
    """Arguments every Office tool takes."""

    file_name: str = Field(min_length=1, max_length=MAX_FILE_NAME_INPUT_LENGTH)


class OfficeFileResult(BaseModel):
    """The LLM-facing result of an Office tool call. It is also the saved
    tool-call response that the session replay reads back."""

    type: Literal["office_file"] = "office_file"
    file_name: str
    file_id: str
    file_link: str
    summary: str
