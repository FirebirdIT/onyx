"""Built-in tools that create Word, Excel, PowerPoint and PDF files.

The files are built in the API server process and saved to the file store as
chat-session-scoped generated files, the same way `run_python` saves its
output files. The UI shows them through the custom-tool packets."""

import abc
from collections.abc import Callable
from io import BytesIO
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import JsonValue, ValidationError
from typing_extensions import override

from onyx.chat.emitter import Emitter
from onyx.configs.constants import FileOrigin
from onyx.file_processing.file_types import (
    PDF_MIME_TYPE,
    PRESENTATION_MIME_TYPE,
    SPREADSHEET_MIME_TYPE,
    WORD_PROCESSING_MIME_TYPE,
)
from onyx.file_store.utils import (
    build_full_frontend_file_url,
    chat_image_gen_metadata,
    get_default_file_store,
)
from onyx.llm.models import ToolDefinition
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import (
    CustomToolDelta,
    CustomToolStart,
    Packet,
)
from onyx.tools.interface import Tool
from onyx.tools.models import (
    PythonExecutionFile,
    PythonToolRichResponse,
    ToolCallException,
    ToolResponse,
)
from onyx.tools.tool_implementations.office.docx_builder import (
    DOCUMENT_PARAMETERS,
    DOCX_EXTENSION,
    DocumentSpec,
    build_docx,
    document_parameters,
    summarize_docx,
)
from onyx.tools.tool_implementations.office.models import (
    MAX_OUTPUT_BYTES,
    OfficeFileResult,
    OfficeFileSpec,
    sanitize_file_name,
)
from onyx.tools.tool_implementations.office.pdf_builder import (
    PDF_EXTENSION,
    build_pdf,
    summarize_pdf,
)
from onyx.tools.tool_implementations.office.pptx_builder import (
    PPTX_EXTENSION,
    PRESENTATION_PARAMETERS,
    PresentationSpec,
    build_pptx,
    summarize_pptx,
)
from onyx.tools.tool_implementations.office.xlsx_builder import (
    SPREADSHEET_PARAMETERS,
    XLSX_EXTENSION,
    WorkbookSpec,
    build_xlsx,
    summarize_xlsx,
)
from onyx.utils.logger import setup_logger

logger = setup_logger()

# Response type of the custom-tool delta that carries the saved file.
OFFICE_FILE_RESPONSE_TYPE = "file"
_MAX_REPORTED_ERRORS = 10

TSpec = TypeVar("TSpec", bound=OfficeFileSpec)


def format_validation_error(error: ValidationError) -> str:
    """A short, model-readable list of what is wrong with the arguments."""
    lines: list[str] = []
    for detail in error.errors()[:_MAX_REPORTED_ERRORS]:
        location = ".".join(str(part) for part in detail["loc"]) or "arguments"
        lines.append(f"{location}: {detail['msg']}")
    remaining = error.error_count() - len(lines)
    if remaining > 0:
        lines.append(f"... and {remaining} more problems")
    return "; ".join(lines)


class OfficeFileTool(Tool[None], Generic[TSpec]):
    """Validate the arguments, build one file, save it, and report it."""

    NAME: str
    DISPLAY_NAME: str
    DESCRIPTION: str
    EXTENSION: str
    MIME_TYPE: str
    PARAMETERS: dict[str, JsonValue]
    SPEC_MODEL: type[TSpec]

    def __init__(self, tool_id: int, emitter: Emitter, chat_session_id: UUID) -> None:
        super().__init__(emitter=emitter)
        self._id = tool_id
        self._chat_session_id = chat_session_id

    @property
    def id(self) -> int:
        return self._id

    @property
    def description(self) -> str:
        return self.DESCRIPTION

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    @abc.abstractmethod
    def _builder(self) -> tuple[Callable[[TSpec], bytes], Callable[[TSpec], str]]:
        """The format's (build, summarize) functions."""
        raise NotImplementedError

    def tool_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name, description=self.description, parameters=self.PARAMETERS
        )

    def emit_start(self, placement: Placement) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolStart(tool_name=self.display_name, tool_id=self._id),
            )
        )

    def _emit_delta(
        self, placement: Placement, data: str, file_ids: list[str] | None
    ) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolDelta(
                    tool_name=self.display_name,
                    tool_id=self._id,
                    response_type=OFFICE_FILE_RESPONSE_TYPE if file_ids else "text",
                    data=data,
                    file_ids=file_ids,
                ),
            )
        )

    def _fail(self, placement: Placement, message: str) -> ToolCallException:
        self._emit_delta(placement, f"The file was not created. {message}", None)
        return ToolCallException(
            message=f"{self.name} failed: {message}",
            llm_facing_message=(
                f"The file was not created. {message} Fix the arguments and call "
                f"{self.name} again."
            ),
        )

    def _save(self, content: bytes, file_name: str) -> str:
        return get_default_file_store().save_file(
            content=BytesIO(content),
            display_name=file_name,
            file_origin=FileOrigin.CHAT_IMAGE_GEN,
            file_type=self.MIME_TYPE,
            file_metadata=chat_image_gen_metadata(self._chat_session_id),
        )

    def run(
        self,
        placement: Placement,
        override_kwargs: None,  # noqa: ARG002
        **llm_kwargs: Any,
    ) -> ToolResponse:
        try:
            spec = self.SPEC_MODEL.model_validate(llm_kwargs)
        except ValidationError as e:
            raise self._fail(
                placement, f"Invalid arguments: {format_validation_error(e)}."
            )

        build, summarize = self._builder()
        try:
            content = build(spec)
        except Exception as e:
            logger.exception("%s could not build the file", self.name)
            raise self._fail(placement, f"The builder reported an error: {e}.")
        if len(content) > MAX_OUTPUT_BYTES:
            raise self._fail(
                placement,
                f"The file is {len(content)} bytes; the limit is {MAX_OUTPUT_BYTES}. "
                "Reduce the content or split it into several files.",
            )

        file_name = sanitize_file_name(spec.file_name, self.EXTENSION)
        file_id = self._save(content, file_name)
        file_link = build_full_frontend_file_url(file_id)
        summary = summarize(spec)
        self._emit_delta(placement, f"{file_name}: {summary}", [file_id])

        result = OfficeFileResult(
            file_name=file_name,
            file_id=file_id,
            file_link=file_link,
            summary=summary,
        )
        return ToolResponse(
            rich_response=PythonToolRichResponse(
                generated_files=[
                    PythonExecutionFile(filename=file_name, file_link=file_link)
                ]
            ),
            llm_facing_response=result.model_dump_json(),
        )


class CreateDocumentTool(OfficeFileTool[DocumentSpec]):
    NAME = "create_document"
    DISPLAY_NAME = "Word Document"
    DESCRIPTION = (
        "Create an editable Word (.docx) file that the user can download. Use it "
        "when the user asks for a Word document or a document file such as a "
        "report, letter, memo, proposal or summary to keep. Give a title and the "
        "body as ordered blocks: headings, paragraphs, bullet and numbered "
        "lists, tables and page breaks. Do not use it for answers that fit in "
        "the chat."
    )
    EXTENSION = DOCX_EXTENSION
    MIME_TYPE = WORD_PROCESSING_MIME_TYPE
    PARAMETERS = DOCUMENT_PARAMETERS
    SPEC_MODEL = DocumentSpec

    @property
    def name(self) -> str:
        return self.NAME

    @override
    def _builder(
        self,
    ) -> tuple[Callable[[DocumentSpec], bytes], Callable[[DocumentSpec], str]]:
        return build_docx, summarize_docx


class CreateSpreadsheetTool(OfficeFileTool[WorkbookSpec]):
    NAME = "create_spreadsheet"
    DISPLAY_NAME = "Excel Spreadsheet"
    DESCRIPTION = (
        "Create an editable Excel (.xlsx) workbook that the user can download. "
        "Use it when the user asks for a spreadsheet, an Excel file, or tabular "
        "data to work with in Excel. Each sheet has a header row, data rows "
        "(text, numbers, booleans or formulas that start with '='), optional "
        "column widths, a frozen header and optional native bar, line or pie "
        "charts over a cell range."
    )
    EXTENSION = XLSX_EXTENSION
    MIME_TYPE = SPREADSHEET_MIME_TYPE
    PARAMETERS = SPREADSHEET_PARAMETERS
    SPEC_MODEL = WorkbookSpec

    @property
    def name(self) -> str:
        return self.NAME

    @override
    def _builder(
        self,
    ) -> tuple[Callable[[WorkbookSpec], bytes], Callable[[WorkbookSpec], str]]:
        return build_xlsx, summarize_xlsx


class CreatePresentationTool(OfficeFileTool[PresentationSpec]):
    NAME = "create_presentation"
    DISPLAY_NAME = "PowerPoint Presentation"
    DESCRIPTION = (
        "Create an editable widescreen PowerPoint (.pptx) deck that the user can "
        "download. Use it when the user asks for slides, a deck or a "
        "presentation. Each slide uses one layout (title, section, bullets, "
        "two_column, table or chart) and can carry speaker notes. Keep slide "
        "text short and vary the layouts to fit the content."
    )
    EXTENSION = PPTX_EXTENSION
    MIME_TYPE = PRESENTATION_MIME_TYPE
    PARAMETERS = PRESENTATION_PARAMETERS
    SPEC_MODEL = PresentationSpec

    @property
    def name(self) -> str:
        return self.NAME

    @override
    def _builder(
        self,
    ) -> tuple[Callable[[PresentationSpec], bytes], Callable[[PresentationSpec], str]]:
        return build_pptx, summarize_pptx


class CreatePdfTool(OfficeFileTool[DocumentSpec]):
    NAME = "create_pdf"
    DISPLAY_NAME = "PDF Document"
    DESCRIPTION = (
        "Create a PDF (.pdf) file that the user can download. Use it when the "
        "user asks for a PDF, or for a document to print, sign or send that must "
        "not change. Give a title and the body as ordered blocks: headings, "
        "paragraphs, bullet and numbered lists, tables and page breaks. Do not "
        "use it for answers that fit in the chat."
    )
    EXTENSION = PDF_EXTENSION
    MIME_TYPE = PDF_MIME_TYPE
    PARAMETERS = document_parameters(PDF_EXTENSION)
    SPEC_MODEL = DocumentSpec

    @property
    def name(self) -> str:
        return self.NAME

    @override
    def _builder(
        self,
    ) -> tuple[Callable[[DocumentSpec], bytes], Callable[[DocumentSpec], str]]:
        return build_pdf, summarize_pdf


OFFICE_TOOL_CLASSES: tuple[type[OfficeFileTool[Any]], ...] = (
    CreateDocumentTool,
    CreateSpreadsheetTool,
    CreatePresentationTool,
    CreatePdfTool,
)
