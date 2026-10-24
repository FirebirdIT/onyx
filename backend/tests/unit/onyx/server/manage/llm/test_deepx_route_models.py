"""Tests for the Deep X Route model fetcher.

Verifies the mapping from the fixed /v1/models catalog to Onyx model configs,
and that the endpoint never sends the key anywhere but the fixed base.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from onyx.db.models import User
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.constants import LlmProviderNames
from onyx.llm.factory import get_llm
from onyx.llm.well_known_providers.constants import DEEPX_ROUTE_API_BASE
from onyx.server.manage.llm.api import get_deepx_route_available_models
from onyx.server.manage.llm.models import (
    DeepXRouteModelsRequest,
    LLMProviderUpsertRequest,
)
from onyx.server.manage.llm.models import (
    TestLLMRequest as LLMTestRequest,
)

_SAMPLE = {
    "object": "list",
    "data": [
        {
            "id": "deepx/chat-large",
            "name": "Chat Large",
            "context_length": 131072,
            "limits": {"max_output_tokens": 8192},
            "reasoning": {"mandatory": False, "supported_efforts": ["low", "high"]},
            "providers": [
                {
                    "enabled": True,
                    "endpoints": ["/v1/chat/completions"],
                    "streaming": True,
                    "max_output_tokens": 8192,
                }
            ],
        },
        {
            # null efforts = every level offered
            "id": "deepx/thinker",
            "reasoning": {"supported_efforts": None},
            "input_modalities": ["text", "image"],
        },
        {
            # empty reasoning block = no reasoning
            "id": "deepx/plain",
            "reasoning": {"supported_efforts": []},
            "input_modalities": ["text"],
        },
        {
            # no enabled chat route -> dropped
            "id": "deepx/disabled",
            "providers": [
                {"enabled": False, "endpoints": ["/v1/chat/completions"]},
                {"enabled": True, "endpoints": ["/v1/embeddings"]},
            ],
        },
        {
            # embedding model -> dropped by name
            "id": "text-embedding-3-small",
        },
        {"id": ""},
        "not-a-dict",
    ],
}


def _fetch(
    sample: dict | None = None, request: DeepXRouteModelsRequest | None = None
) -> dict:
    with (
        patch("onyx.server.manage.llm.api._resolve_api_key", return_value="k"),
        patch(
            "onyx.server.manage.llm.api._get_deepx_route_models_response",
            return_value=sample if sample is not None else _SAMPLE,
        ),
    ):
        results = get_deepx_route_available_models(
            request=request or DeepXRouteModelsRequest(api_key="k"),
            _=cast(User, None),
            db_session=cast(Session, None),
        )
    return {r.name: r for r in results}


def test_non_chat_entries_are_dropped() -> None:
    assert set(_fetch()) == {"deepx/chat-large", "deepx/thinker", "deepx/plain"}


def test_fields_mapped() -> None:
    by_name = _fetch()
    large = by_name["deepx/chat-large"]
    assert large.display_name == "Chat Large"
    assert large.max_input_tokens == 131072
    assert large.supports_image_input is False
    assert by_name["deepx/thinker"].display_name == "deepx/thinker"
    assert by_name["deepx/thinker"].max_input_tokens is None


def test_reasoning_from_catalog() -> None:
    by_name = _fetch()
    assert by_name["deepx/chat-large"].supports_reasoning is True
    assert by_name["deepx/thinker"].supports_reasoning is True
    assert by_name["deepx/plain"].supports_reasoning is False


def test_image_input_from_catalog_modalities() -> None:
    by_name = _fetch()
    assert by_name["deepx/thinker"].supports_image_input is True
    assert by_name["deepx/plain"].supports_image_input is False
    # No modalities listed means text only.
    assert by_name["deepx/chat-large"].supports_image_input is False


def test_malformed_modalities_mean_text_only() -> None:
    by_name = _fetch({"data": [{"id": "deepx/odd", "input_modalities": "image"}]})
    assert by_name["deepx/odd"].supports_image_input is False


def test_empty_catalog_raises() -> None:
    with pytest.raises(OnyxError):
        _fetch({"data": []})


def test_request_rejects_api_base() -> None:
    with pytest.raises(ValidationError):
        DeepXRouteModelsRequest.model_validate(
            {"api_key": "k", "api_base": "https://evil.example/v1"}
        )


def test_fetch_uses_fixed_url() -> None:
    response = MagicMock()
    response.json.return_value = _SAMPLE
    with patch(
        "onyx.server.manage.llm.api.httpx.get", return_value=response
    ) as mock_get:
        results = get_deepx_route_available_models(
            request=DeepXRouteModelsRequest(api_key="sk-deepx"),
            _=cast(User, None),
            db_session=cast(Session, None),
        )
    assert results
    assert mock_get.call_args.args[0] == f"{DEEPX_ROUTE_API_BASE}/models"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-deepx"


def test_missing_key_raises() -> None:
    with pytest.raises(OnyxError):
        get_deepx_route_available_models(
            request=DeepXRouteModelsRequest(),
            _=cast(User, None),
            db_session=cast(Session, None),
        )


def test_provider_id_of_another_provider_is_rejected() -> None:
    other = SimpleNamespace(provider=LlmProviderNames.OPENAI, api_key=None)
    with (
        patch(
            "onyx.server.manage.llm.api.fetch_existing_llm_provider_by_id",
            return_value=other,
        ),
        patch("onyx.server.manage.llm.api.httpx.get") as mock_get,
        pytest.raises(OnyxError),
    ):
        get_deepx_route_available_models(
            request=DeepXRouteModelsRequest(api_key="sk-a****b1c2", provider_id=3),
            _=cast(User, None),
            db_session=cast(Session, None),
        )
    mock_get.assert_not_called()


def test_upsert_pins_api_base() -> None:
    request = LLMProviderUpsertRequest(provider="deepx_route", api_key="k")
    assert request.api_base == DEEPX_ROUTE_API_BASE
    trailing = LLMProviderUpsertRequest(
        provider="deepx_route", api_key="k", api_base=f"{DEEPX_ROUTE_API_BASE}/"
    )
    assert trailing.api_base == DEEPX_ROUTE_API_BASE


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_base": "https://evil.example/v1"},
        {"custom_config": {"OPENAI_BASE_URL": "https://evil.example/v1"}},
    ],
)
def test_upsert_and_test_reject_redirects(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        LLMProviderUpsertRequest(provider="deepx_route", api_key="k", **overrides)
    with pytest.raises(ValidationError):
        LLMTestRequest(
            provider="deepx_route",
            model="deepx/chat-large",
            api_key="k",
            api_key_changed=True,
            custom_config_changed=False,
            **overrides,
        )


def test_get_llm_pins_api_base() -> None:
    llm = get_llm(
        provider=LlmProviderNames.DEEPX_ROUTE,
        model="deepx/chat-large",
        max_input_tokens=-1,
        deployment_name=None,
        api_key="k",
        api_base="https://evil.example/v1",
        custom_config={"OPENAI_BASE_URL": "https://evil.example/v1"},
    )
    assert llm._api_base == DEEPX_ROUTE_API_BASE
    assert llm._model_kwargs["api_base"] == DEEPX_ROUTE_API_BASE
    assert llm._custom_llm_provider == "openai"
    assert not llm._custom_config
