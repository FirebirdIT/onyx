from unittest.mock import MagicMock, patch

import pytest
from litellm.exceptions import BadRequestError

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.image_gen.exceptions import ImageProviderError
from onyx.llm.constants import LlmProviderNames
from onyx.server.manage.image_generation.api import (
    _resolve_source_llm_provider_api_key,
)
from onyx.server.manage.image_generation.api import (
    test_image_generation as run_test_image_generation,
)
from onyx.server.manage.image_generation.models import (
    TestImageGenerationRequest as ImageGenerationTestRequest,
)

_API_KEY = "sk-image-generation-secret"
_CUSTOM_SECRET = "custom-config-secret"


def test_image_generation_error_is_actionable_and_redacts_credentials() -> None:
    upstream_error = BadRequestError(
        message=(
            f"Unsupported image size. api_key={_API_KEY} custom_token={_CUSTOM_SECRET}"
        ),
        model="gpt-image-1",
        llm_provider="openai",
    )
    image_provider = MagicMock()
    image_provider.check_connection.side_effect = upstream_error
    request = ImageGenerationTestRequest(
        model_name="gpt-image-1",
        provider="openai",
        api_key=_API_KEY,
        custom_config={"custom_token": _CUSTOM_SECRET},
    )

    with (
        patch(
            "onyx.server.manage.image_generation.api.get_image_generation_provider",
            return_value=image_provider,
        ),
        pytest.raises(OnyxError) as exc_info,
    ):
        run_test_image_generation(request, MagicMock(), MagicMock())

    assert exc_info.value.error_code is OnyxErrorCode.VALIDATION_ERROR
    assert "Unsupported image size" in exc_info.value.detail
    assert exc_info.value.detail.count("[REDACTED]") == 2
    assert _API_KEY not in exc_info.value.detail
    assert _CUSTOM_SECRET not in exc_info.value.detail


_API = "onyx.server.manage.image_generation.api"
_DXR_KEY = "dxr-stored-secret"


def _stored_provider(provider: str, key: str | None) -> MagicMock:
    stored = MagicMock()
    stored.provider = provider
    stored.api_key = None
    if key is not None:
        stored.api_key = MagicMock()
        stored.api_key.get_value.return_value = key
    return stored


def test_provider_error_message_reaches_admin() -> None:
    image_provider = MagicMock()
    image_provider.check_connection.side_effect = ImageProviderError(
        f"Deep X Route returned HTTP 402: balance too low key={_DXR_KEY}"
    )
    request = ImageGenerationTestRequest(
        model_name="dxr-image", provider="deepx_route", api_key=_DXR_KEY
    )

    with (
        patch(f"{_API}.get_image_generation_provider", return_value=image_provider),
        pytest.raises(OnyxError) as exc_info,
    ):
        run_test_image_generation(request, MagicMock(), MagicMock())

    assert "balance too low" in exc_info.value.detail
    assert _DXR_KEY not in exc_info.value.detail


def test_reuse_returns_typed_key_without_source() -> None:
    assert (
        _resolve_source_llm_provider_api_key("deepx_route", "typed", None, MagicMock())
        == "typed"
    )


def test_reuse_copies_key_from_deepx_route_llm_provider() -> None:
    with patch(
        f"{_API}.fetch_existing_llm_provider_by_id",
        return_value=_stored_provider(LlmProviderNames.DEEPX_ROUTE, _DXR_KEY),
    ):
        key = _resolve_source_llm_provider_api_key("deepx_route", None, 7, MagicMock())
    assert key == _DXR_KEY


@pytest.mark.parametrize(
    ("image_provider", "stored"),
    [
        # Only providers with a pinned endpoint may reuse a stored key.
        ("openai", _stored_provider(LlmProviderNames.OPENAI, "sk")),
        # The source row must be the same vendor.
        ("deepx_route", _stored_provider(LlmProviderNames.OPENAI, "sk")),
        ("deepx_route", None),
        ("deepx_route", _stored_provider(LlmProviderNames.DEEPX_ROUTE, None)),
    ],
)
def test_reuse_rejects_invalid_source(
    image_provider: str, stored: MagicMock | None
) -> None:
    with (
        patch(f"{_API}.fetch_existing_llm_provider_by_id", return_value=stored),
        pytest.raises(OnyxError) as exc_info,
    ):
        _resolve_source_llm_provider_api_key(image_provider, None, 7, MagicMock())
    assert exc_info.value.error_code is OnyxErrorCode.VALIDATION_ERROR
