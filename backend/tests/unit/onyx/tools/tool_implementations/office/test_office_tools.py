import json
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from docx import Document

from onyx.configs.constants import FileOrigin
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.session_loading import create_office_file_packets
from onyx.server.query_and_chat.streaming_models import (
    CustomToolDelta,
    CustomToolStart,
    SectionEnd,
)
from onyx.tools.built_in_tools import BUILT_IN_TOOL_MAP, TOOL_NAME_TO_CLASS
from onyx.tools.models import PythonToolRichResponse, ToolCallException
from onyx.tools.tool_implementations.office.models import OfficeFileResult
from onyx.tools.tool_implementations.office.office_tools import (
    OFFICE_TOOL_CLASSES,
    CreateDocumentTool,
    CreatePresentationTool,
    CreateSpreadsheetTool,
)

TOOL_MODULE = "onyx.tools.tool_implementations.office.office_tools"
_PLACEMENT = Placement(turn_index=1, tab_index=0)
_FILE_ID = "saved-file-id"

_DOCUMENT_ARGS: dict[str, Any] = {
    "file_name": "../Plan.pdf",
    "title": "Plan",
    "blocks": [{"type": "paragraph", "text": "Hello"}],
}


def _emitted(emitter: MagicMock) -> list[Any]:
    return [call.args[0].obj for call in emitter.emit.call_args_list]


def test_tools_are_registered() -> None:
    for tool_cls in OFFICE_TOOL_CLASSES:
        assert BUILT_IN_TOOL_MAP[tool_cls.__name__] is tool_cls
        assert TOOL_NAME_TO_CLASS[tool_cls.NAME] is tool_cls


@pytest.mark.parametrize("tool_cls", OFFICE_TOOL_CLASSES)
def test_tool_definition(tool_cls: type[CreateDocumentTool]) -> None:
    tool = tool_cls(tool_id=7, emitter=MagicMock(), chat_session_id=uuid4())
    definition = tool.tool_definition()
    assert definition.name == tool_cls.NAME
    assert "file_name" in json.dumps(definition.parameters)
    assert tool.description


def test_run_saves_file_and_reports_it() -> None:
    emitter = MagicMock()
    session_id = uuid4()
    tool = CreateDocumentTool(tool_id=7, emitter=emitter, chat_session_id=session_id)
    file_store = MagicMock()
    file_store.save_file.return_value = _FILE_ID

    with patch(f"{TOOL_MODULE}.get_default_file_store", return_value=file_store):
        tool.emit_start(_PLACEMENT)
        response = tool.run(_PLACEMENT, None, **_DOCUMENT_ARGS)

    save_kwargs = file_store.save_file.call_args.kwargs
    assert save_kwargs["display_name"] == "Plan.docx"
    assert save_kwargs["file_origin"] == FileOrigin.CHAT_IMAGE_GEN
    assert save_kwargs["file_metadata"]["chat_session_id"] == str(session_id)
    saved = Document(BytesIO(save_kwargs["content"].getvalue()))
    assert any(p.text == "Hello" for p in saved.paragraphs)

    result = OfficeFileResult.model_validate_json(response.llm_facing_response)
    assert result.file_id == _FILE_ID
    assert result.file_name == "Plan.docx"
    assert result.file_link.endswith(f"/api/chat/file/{_FILE_ID}")

    assert isinstance(response.rich_response, PythonToolRichResponse)
    assert [f.filename for f in response.rich_response.generated_files] == ["Plan.docx"]

    start, delta = _emitted(emitter)
    assert isinstance(start, CustomToolStart)
    assert start.tool_name == CreateDocumentTool.DISPLAY_NAME
    assert isinstance(delta, CustomToolDelta)
    assert delta.file_ids == [_FILE_ID]
    assert delta.response_type == "file"


@pytest.mark.parametrize(
    ("tool_cls", "args"),
    [
        (CreateDocumentTool, {"file_name": "a", "title": "T", "blocks": []}),
        (CreateSpreadsheetTool, {"file_name": "a", "sheets": [{"name": "S"}]}),
        (CreatePresentationTool, {"file_name": "a", "title": "T"}),
    ],
)
def test_invalid_arguments_fail_without_saving(
    tool_cls: type[CreateDocumentTool], args: dict[str, Any]
) -> None:
    emitter = MagicMock()
    tool = tool_cls(tool_id=7, emitter=emitter, chat_session_id=uuid4())
    file_store = MagicMock()

    with (
        patch(f"{TOOL_MODULE}.get_default_file_store", return_value=file_store),
        pytest.raises(ToolCallException) as error,
    ):
        tool.run(_PLACEMENT, None, **args)

    file_store.save_file.assert_not_called()
    assert "Invalid arguments" in error.value.llm_facing_message
    (delta,) = _emitted(emitter)
    assert isinstance(delta, CustomToolDelta)
    assert delta.file_ids is None


def test_oversized_output_is_not_saved() -> None:
    tool = CreateDocumentTool(tool_id=7, emitter=MagicMock(), chat_session_id=uuid4())
    file_store = MagicMock()

    with (
        patch(f"{TOOL_MODULE}.get_default_file_store", return_value=file_store),
        patch(f"{TOOL_MODULE}.MAX_OUTPUT_BYTES", 10),
        pytest.raises(ToolCallException),
    ):
        tool.run(_PLACEMENT, None, **_DOCUMENT_ARGS)

    file_store.save_file.assert_not_called()


def test_replay_packets_from_saved_result() -> None:
    saved = OfficeFileResult(
        file_name="Plan.docx",
        file_id=_FILE_ID,
        file_link=f"http://x/api/chat/file/{_FILE_ID}",
        summary="Word document 'Plan'.",
    ).model_dump_json()

    packets = create_office_file_packets("Word Document", 7, saved, turn_index=2)

    objs = [packet.obj for packet in packets]
    assert isinstance(objs[0], CustomToolStart)
    delta = next(obj for obj in objs if isinstance(obj, CustomToolDelta))
    assert delta.file_ids == [_FILE_ID]
    assert delta.response_type == "file"
    assert isinstance(objs[-1], SectionEnd)


def test_replay_packets_from_saved_error() -> None:
    packets = create_office_file_packets("Word Document", 7, "Tool failed", 2)

    delta = next(p.obj for p in packets if isinstance(p.obj, CustomToolDelta))
    assert delta.file_ids is None
    assert delta.data == "Tool failed"
