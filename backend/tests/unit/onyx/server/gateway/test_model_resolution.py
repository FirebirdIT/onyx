"""Gateway model ids resolve only to visible chat models the caller can use."""

from unittest.mock import MagicMock, patch

import pytest

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.server.gateway import model_resolution
from onyx.server.gateway.model_resolution import (
    GatewayModelId,
    parse_gateway_model_id,
    resolve_gateway_model,
)
from onyx.server.manage.llm.models import LLMProviderView, ModelConfigurationView


def _provider() -> LLMProviderView:
    return LLMProviderView(
        id=7,
        name="Main",
        provider="openai",
        api_key="sk-test",
        model_configurations=[
            _model("gpt-5-mini"),
            _model("org/model"),
            _model("hidden", visible=False),
            _model("decider", supports_chat=False),
        ],
    )


def _model(
    name: str, *, visible: bool = True, supports_chat: bool = True
) -> ModelConfigurationView:
    return ModelConfigurationView(
        name=name,
        is_visible=visible,
        supports_image_input=False,
        supports_chat=supports_chat,
    )


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("7/gpt-5-mini", GatewayModelId(7, "gpt-5-mini")),
        # Model names keep their own slashes.
        ("7/org/model", GatewayModelId(7, "org/model")),
        ("gpt-5-mini", None),
        ("7/", None),
        ("abc/gpt-5-mini", None),
        ("/gpt-5-mini", None),
    ],
)
def test_parse_gateway_model_id(model_id: str, expected: GatewayModelId | None) -> None:
    assert parse_gateway_model_id(model_id) == expected


@pytest.mark.parametrize("model_name", ["gpt-5-mini", "org/model"])
def test_resolves_visible_chat_model(model_name: str) -> None:
    with patch.object(
        model_resolution,
        "fetch_accessible_llm_provider_by_id",
        return_value=_provider(),
    ) as fetch:
        provider, resolved = resolve_gateway_model(
            MagicMock(), MagicMock(), f"7/{model_name}"
        )
    assert provider.id == 7
    assert resolved == model_name
    assert fetch.call_args.args[2] == 7


@pytest.mark.parametrize(
    ("model_id", "provider"),
    [
        ("malformed", _provider()),
        ("7/hidden", _provider()),
        ("7/decider", _provider()),
        ("7/missing", _provider()),
        # Not accessible to the caller.
        ("7/gpt-5-mini", None),
    ],
)
def test_unservable_model_is_not_found(
    model_id: str, provider: LLMProviderView | None
) -> None:
    with patch.object(
        model_resolution,
        "fetch_accessible_llm_provider_by_id",
        return_value=provider,
    ):
        with pytest.raises(OnyxError) as exc_info:
            resolve_gateway_model(MagicMock(), MagicMock(), model_id)
    assert exc_info.value.error_code == OnyxErrorCode.NOT_FOUND
