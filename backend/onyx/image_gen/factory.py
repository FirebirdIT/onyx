from enum import Enum

from onyx.image_gen.interfaces import (
    ImageGenerationProvider,
    ImageGenerationProviderCredentials,
)
from onyx.image_gen.providers.azure_img_gen import AzureImageGenerationProvider
from onyx.image_gen.providers.deepx_route_img_gen import (
    DeepXRouteImageGenerationProvider,
)
from onyx.image_gen.providers.openai_img_gen import OpenAIImageGenerationProvider
from onyx.image_gen.providers.vertex_img_gen import VertexImageGenerationProvider


class ImageGenerationProviderName(str, Enum):
    AZURE = "azure"
    # Same value as LlmProviderNames.DEEPX_ROUTE: the stored LLM provider row
    # names the image provider.
    DEEPX_ROUTE = "deepx_route"
    OPENAI = "openai"
    VERTEX_AI = "vertex_ai"


PROVIDERS: dict[ImageGenerationProviderName, type[ImageGenerationProvider]] = {
    ImageGenerationProviderName.AZURE: AzureImageGenerationProvider,
    ImageGenerationProviderName.DEEPX_ROUTE: DeepXRouteImageGenerationProvider,
    ImageGenerationProviderName.OPENAI: OpenAIImageGenerationProvider,
    ImageGenerationProviderName.VERTEX_AI: VertexImageGenerationProvider,
}


def get_image_generation_provider(
    provider: str,
    credentials: ImageGenerationProviderCredentials,
) -> ImageGenerationProvider:
    provider_cls = _get_provider_cls(provider)
    return provider_cls.build_from_credentials(credentials)


def validate_credentials(
    provider: str,
    credentials: ImageGenerationProviderCredentials,
) -> bool:
    provider_cls = _get_provider_cls(provider)
    return provider_cls.validate_credentials(credentials)


def _get_provider_cls(provider: str) -> type[ImageGenerationProvider]:
    try:
        provider_enum = ImageGenerationProviderName(provider)
    except ValueError:
        raise ValueError(f"Invalid image generation provider: {provider}")
    return PROVIDERS[provider_enum]
