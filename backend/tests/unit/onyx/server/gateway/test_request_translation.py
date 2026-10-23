"""OpenAI Chat Completions requests translate into typed provider messages."""

from typing import Any

import pytest

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.model_request import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from onyx.llm.models import (
    ImageContentPart,
    NamedToolChoice,
    ReasoningEffort,
    TextContentPart,
    ToolChoiceOptions,
)
from onyx.server.gateway.models import ChatCompletionRequest
from onyx.server.gateway.request_translation import (
    translate_messages,
    translate_reasoning_effort,
    translate_request,
    translate_tool_choice,
)

_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {"name": "bash", "parameters": {"type": "object"}},
}


def test_translates_a_tool_loop_conversation() -> None:
    messages = translate_messages(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "developer", "content": [{"type": "text", "text": "Rules."}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;x"}},
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"},
        ]
    )

    system, developer, user, assistant, tool = messages
    assert system == SystemMessage(content="Be brief.")
    assert developer == SystemMessage(content="Rules.")
    assert isinstance(user, UserMessage) and isinstance(user.content, list)
    assert isinstance(user.content[0], TextContentPart)
    assert isinstance(user.content[1], ImageContentPart)
    assert isinstance(assistant, AssistantMessage)
    assert assistant.content is None
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].function.name == "bash"
    assert assistant.tool_calls[0].function.arguments == '{"cmd":"ls"}'
    assert tool == ToolMessage(content="a.txt", tool_call_id="call_1")


def test_object_tool_arguments_are_serialized() -> None:
    (assistant,) = translate_messages(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "c", "function": {"name": "f", "arguments": {"a": 1}}}
                ],
            }
        ]
    )
    assert isinstance(assistant, AssistantMessage)
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].function.arguments == '{"a": 1}'


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "function", "content": "x"}],
        [{"role": "tool", "content": "x"}],
        [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}],
        [{"role": "system", "content": [{"type": "image_url", "image_url": {}}]}],
        [{"content": "no role"}],
    ],
)
def test_unsupported_messages_are_rejected(messages: list[dict[str, Any]]) -> None:
    with pytest.raises(OnyxError) as exc_info:
        translate_messages(messages)
    assert exc_info.value.error_code == OnyxErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("auto", ToolChoiceOptions.AUTO),
        ("required", ToolChoiceOptions.REQUIRED),
        (
            {"type": "function", "function": {"name": "bash"}},
            NamedToolChoice(name="bash"),
        ),
    ],
)
def test_tool_choice(raw: Any, expected: Any) -> None:
    assert translate_tool_choice(raw) == expected


def test_bad_tool_choice_is_rejected() -> None:
    with pytest.raises(OnyxError):
        translate_tool_choice("sometimes")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ReasoningEffort.AUTO),
        ("none", ReasoningEffort.OFF),
        ("minimal", ReasoningEffort.LOW),
        ("HIGH", ReasoningEffort.HIGH),
        ("xhigh", ReasoningEffort.XHIGH),
        ("unknown", ReasoningEffort.AUTO),
    ],
)
def test_reasoning_effort(raw: str | None, expected: ReasoningEffort) -> None:
    assert translate_reasoning_effort(raw) is expected


def test_request_ignores_unknown_fields_and_drops_tool_choice_without_tools() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "1/m",
            "messages": [{"role": "user", "content": "hi"}],
            "tool_choice": "required",
            "max_tokens": 10,
            "max_completion_tokens": 20,
            "reasoningSummary": "auto",
        }
    )
    call = translate_request(request)
    assert call.tools is None
    assert call.tool_choice is None
    assert call.max_tokens == 20


def test_request_keeps_tools_and_choice() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "1/m",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_TOOL],
            "tool_choice": "auto",
        }
    )
    call = translate_request(request)
    assert call.tools == [_TOOL]
    assert call.tool_choice is ToolChoiceOptions.AUTO
