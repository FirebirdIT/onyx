"""The LLM gateway's OpenAI Chat Completions contract, against the mock LLM.

Covers the MIT gateway a Deep X build serves (and any gateway at the same
path). Skips when the deployment does not serve the gateway.
"""

import json
from typing import Any

import pytest

from onyx.db.enums import Permission
from tests.integration.common_utils.constants import API_SERVER_URL
from tests.integration.common_utils.http_client import client
from tests.integration.common_utils.managers.llm_provider import LLMProviderManager
from tests.integration.common_utils.managers.mock_llm import (
    MOCK_LLM_MODEL_NAME,
    MockLLMScript,
)
from tests.integration.common_utils.managers.pat import PATManager
from tests.integration.common_utils.test_models import DATestUser
from tests.integration.mock_services.mock_llm_server.models import (
    Reply,
    RequestConditions,
    ToolCall,
)

_GATEWAY_URL = f"{API_SERVER_URL}/gateway/v1"
_TOOL_FREE = RequestConditions(has_tools=False)
_BASH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command.",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    },
}


def _gateway_token(user: DATestUser) -> str:
    pat = PATManager.create(
        name="chat-completions-gateway",
        expiration_days=None,
        user_performing_action=user,
        scopes=[Permission.USE_LLM_GATEWAY],
    )
    assert pat.token
    return pat.token


@pytest.fixture
def gateway(
    mock_llm: MockLLMScript,  # noqa: ARG001 - makes the mock model the default
    admin_user: DATestUser,
) -> tuple[str, str]:
    """A gateway PAT and the wire id of the mock model."""
    probe = client.get(
        f"{_GATEWAY_URL}/models",
        headers=admin_user.headers,
        cookies=admin_user.cookies,
    )
    if probe.status_code in (402, 404):
        pytest.skip("This deployment does not serve the LLM gateway.")
    default = LLMProviderManager.get_default_model(admin_user)
    assert default is not None and default.model_name == MOCK_LLM_MODEL_NAME
    return _gateway_token(admin_user), f"{default.provider_id}/{MOCK_LLM_MODEL_NAME}"


def _post(token: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"{_GATEWAY_URL}/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=60,
    )


def _sse_payloads(response: Any) -> list[str]:
    return [
        line.removeprefix("data: ")
        for line in response.iter_lines()
        if line and line.startswith("data: ")
    ]


def test_models_lists_the_mock_model(gateway: tuple[str, str]) -> None:
    token, model_id = gateway
    response = client.get(
        f"{_GATEWAY_URL}/models", headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    body = response.json()
    assert body["object"] == "list"
    assert model_id in [entry["id"] for entry in body["data"]]


def test_non_streaming_completion(
    gateway: tuple[str, str], mock_llm: MockLLMScript
) -> None:
    token, model_id = gateway
    mock_llm.conversation("main", Reply(text="Hello there."), conditions=_TOOL_FREE)

    response = _post(
        token,
        {"model": model_id, "messages": [{"role": "user", "content": "Hi"}]},
    )

    response.raise_for_status()
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == model_id
    assert body["choices"][0]["message"]["content"] == "Hello there."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0


def test_streaming_completion(
    gateway: tuple[str, str], mock_llm: MockLLMScript
) -> None:
    token, model_id = gateway
    mock_llm.conversation("main", Reply(text="Streamed reply."), conditions=_TOOL_FREE)

    response = _post(
        token,
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )

    response.raise_for_status()
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(response)
    assert payloads[-1] == "[DONE]"
    chunks = [json.loads(payload) for payload in payloads[:-1]]
    assert not any("error" in chunk for chunk in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Streamed reply."
    assert any(c.get("usage") for c in chunks)


def test_streaming_tool_call_round_trip(
    gateway: tuple[str, str], mock_llm: MockLLMScript
) -> None:
    token, model_id = gateway
    mock_llm.conversation(
        "main",
        Reply(tool_calls=[ToolCall(id="call_1", name="bash", arguments={"cmd": "ls"})]),
        Reply(text="Done."),
        conditions=RequestConditions(offers=["bash"]),
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "List files"}]

    first = _post(
        token,
        {
            "model": model_id,
            "messages": messages,
            "tools": [_BASH_TOOL],
            "stream": True,
        },
    )
    first.raise_for_status()
    chunks = [json.loads(p) for p in _sse_payloads(first)[:-1]]
    deltas = [
        call for c in chunks for call in c["choices"][0]["delta"].get("tool_calls", [])
    ]
    assert deltas[0]["id"] == "call_1"
    assert deltas[0]["function"]["name"] == "bash"
    arguments = "".join(d["function"].get("arguments", "") for d in deltas)
    assert json.loads(arguments) == {"cmd": "ls"}
    assert "tool_calls" in [c["choices"][0]["finish_reason"] for c in chunks]

    second = _post(
        token,
        {
            "model": model_id,
            "tools": [_BASH_TOOL],
            "messages": [
                *messages,
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": arguments},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"},
            ],
        },
    )
    second.raise_for_status()
    assert second.json()["choices"][0]["message"]["content"] == "Done."
    assert mock_llm.requests[1].tool_result_ids() == ["call_1"]


def test_unknown_model_is_not_found(gateway: tuple[str, str]) -> None:
    token, _ = gateway
    response = _post(
        token,
        {"model": "999999/none", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


def test_session_auth_is_rejected(
    gateway: tuple[str, str], admin_user: DATestUser
) -> None:
    _, model_id = gateway
    response = client.post(
        f"{_GATEWAY_URL}/chat/completions",
        headers=admin_user.headers,
        cookies=admin_user.cookies,
        json={"model": model_id, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 403
