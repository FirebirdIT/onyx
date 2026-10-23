"""Translate an OpenAI Chat Completions request into provider call arguments.

The gateway accepts the public Chat Completions wire format and hands the
messages to ``LitellmLLM.invoke_raw`` / ``stream_raw`` as typed provider
messages. Unknown request fields are ignored; unsupported content is refused.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.model_request import (
    AssistantMessage,
    ChatCompletionMessage,
    RequestFunctionCall,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from onyx.llm.models import (
    ContentPart,
    NamedToolChoice,
    ReasoningEffort,
    TextContentPart,
    ToolChoice,
    ToolChoiceOptions,
)
from onyx.server.gateway.models import ChatCompletionRequest

_CONTENT_PARTS = TypeAdapter(list[ContentPart])

# OpenAI effort names that have no ReasoningEffort member of the same name.
_REASONING_EFFORT_ALIASES: dict[str, ReasoningEffort] = {
    "none": ReasoningEffort.OFF,
    "minimal": ReasoningEffort.LOW,
}


class _WireFunction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: str | dict[str, Any] = ""


class _WireToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["function"] = "function"
    function: _WireFunction


class _WireMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[_WireToolCall] | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class GatewayCall:
    """Arguments for one provider call, shared by invoke and stream."""

    prompt: list[ChatCompletionMessage]
    tools: list[dict[str, Any]] | None
    tool_choice: ToolChoice | None
    structured_response_format: dict[str, Any] | None
    max_tokens: int | None
    reasoning_effort: ReasoningEffort


def _invalid(detail: str) -> OnyxError:
    return OnyxError(OnyxErrorCode.INVALID_INPUT, detail)


def _content_parts(raw: list[dict[str, Any]], role: str) -> list[ContentPart]:
    try:
        return _CONTENT_PARTS.validate_python(raw)
    except ValidationError:
        raise _invalid(
            f"Unsupported content in a '{role}' message: only text and "
            "image_url parts are accepted."
        )


def _text_content(content: str | list[dict[str, Any]] | None, role: str) -> str:
    if content is None or isinstance(content, str):
        return content or ""
    parts = _content_parts(content, role)
    if any(not isinstance(part, TextContentPart) for part in parts):
        raise _invalid(f"A '{role}' message can contain text parts only.")
    return "".join(part.text for part in parts if isinstance(part, TextContentPart))


def _tool_call(call: _WireToolCall) -> ToolCall:
    arguments = call.function.arguments
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    return ToolCall(
        id=call.id,
        function=RequestFunctionCall(name=call.function.name, arguments=arguments),
    )


def _translate_message(message: _WireMessage) -> ChatCompletionMessage:
    role = message.role
    if role in ("system", "developer"):
        return SystemMessage(content=_text_content(message.content, role))
    if role == "user":
        if isinstance(message.content, list):
            return UserMessage(content=_content_parts(message.content, role))
        return UserMessage(content=message.content or "")
    if role == "assistant":
        text = _text_content(message.content, role) or None
        tool_calls = [_tool_call(call) for call in message.tool_calls or []]
        return AssistantMessage(content=text, tool_calls=tool_calls or None)
    if role == "tool":
        if not message.tool_call_id:
            raise _invalid("A 'tool' message requires tool_call_id.")
        return ToolMessage(
            content=_text_content(message.content, role),
            tool_call_id=message.tool_call_id,
        )
    raise _invalid(f"Unsupported message role '{role}'.")


def translate_messages(
    raw_messages: list[dict[str, Any]],
) -> list[ChatCompletionMessage]:
    if not raw_messages:
        raise _invalid("messages must contain at least one message.")
    try:
        wire_messages = [_WireMessage.model_validate(raw) for raw in raw_messages]
    except ValidationError as e:
        raise _invalid(f"Malformed message: {e.errors()[0]['msg']}")
    return [_translate_message(message) for message in wire_messages]


def translate_tool_choice(raw: Any) -> ToolChoice | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return ToolChoiceOptions(raw)
        except ValueError:
            raise _invalid(f"Unsupported tool_choice '{raw}'.")
    if isinstance(raw, dict):
        function = raw.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return NamedToolChoice(name=function["name"])
    raise _invalid("tool_choice must be a string or a named function.")


def translate_reasoning_effort(raw: str | None) -> ReasoningEffort:
    """Unknown values fall back to AUTO, like other unknown request fields."""
    if raw is None:
        return ReasoningEffort.AUTO
    normalized = raw.lower()
    if normalized in _REASONING_EFFORT_ALIASES:
        return _REASONING_EFFORT_ALIASES[normalized]
    try:
        return ReasoningEffort(normalized)
    except ValueError:
        return ReasoningEffort.AUTO


def translate_request(request: ChatCompletionRequest) -> GatewayCall:
    tools = request.tools or None
    return GatewayCall(
        prompt=translate_messages(request.messages),
        tools=tools,
        tool_choice=translate_tool_choice(request.tool_choice) if tools else None,
        structured_response_format=request.response_format,
        max_tokens=request.max_completion_tokens or request.max_tokens,
        reasoning_effort=translate_reasoning_effort(request.reasoning_effort),
    )
