"""Configure Deep X Route from DEEPX_ROUTE_API_KEY at api_server startup.

Each step runs only when its feature has no configuration yet, so a restart
never changes what an admin set up. Do not log the key.
"""

from sqlalchemy.orm import Session

from onyx.db.enums import LLMModelFlowType
from onyx.db.image_generation import get_all_image_generation_configs
from onyx.db.llm import (
    fetch_existing_llm_providers,
    update_default_model,
    upsert_llm_provider,
)
from onyx.error_handling.exceptions import OnyxError
from onyx.image_gen.factory import ImageGenerationProviderName
from onyx.image_gen.providers.deepx_route_img_gen import DXR_IMAGE_MODEL
from onyx.llm.constants import LlmProviderNames
from onyx.llm.well_known_providers.constants import (
    DEEPX_ROUTE_DEFAULT_CHAT_MODEL,
    DEEPX_ROUTE_FALLBACK_CHAT_MODELS,
)
from onyx.server.manage.image_generation.api import create_image_generation_config
from onyx.server.manage.image_generation.models import ImageGenerationConfigCreate
from onyx.server.manage.llm.api import fetch_deepx_route_chat_models
from onyx.server.manage.llm.models import (
    LLMProviderUpsertRequest,
    LLMProviderView,
    ModelConfigurationUpsertRequest,
)
from onyx.server.manage.llm.provider_cache import invalidate_provider_listing_cache
from onyx.utils.logger import setup_logger

logger = setup_logger()

DEEPX_ROUTE_BOOTSTRAP_PROVIDER_NAME = "Deep X Route"
# Same id as the "DXR Image" entry on the admin Image Generation page.
DEEPX_ROUTE_BOOTSTRAP_IMAGE_PROVIDER_ID = "deepx_route_dxr_image"


def _deepx_route_model_configurations(
    api_key: str,
) -> list[ModelConfigurationUpsertRequest]:
    """Models from the live catalog, else the fallback list."""
    try:
        catalog = fetch_deepx_route_chat_models(api_key)
    except OnyxError as e:
        logger.warning(
            "Could not fetch the Deep X Route model catalog (%s). "
            "Using the fallback model list.",
            e.detail,
        )
        return [
            ModelConfigurationUpsertRequest(name=name, is_visible=True)
            for name in DEEPX_ROUTE_FALLBACK_CHAT_MODELS
        ]
    return [
        ModelConfigurationUpsertRequest(
            name=model.name,
            display_name=model.display_name,
            max_input_tokens=model.max_input_tokens,
            supports_image_input=model.supports_image_input,
            supports_reasoning=model.supports_reasoning,
            is_visible=True,
        )
        for model in catalog
    ]


def _pick_default_model(models: list[ModelConfigurationUpsertRequest]) -> str:
    names = [model.name for model in models]
    if DEEPX_ROUTE_DEFAULT_CHAT_MODEL in names:
        return DEEPX_ROUTE_DEFAULT_CHAT_MODEL
    return names[0]


def ensure_deepx_route_llm_provider(
    db_session: Session, api_key: str
) -> LLMProviderView | None:
    """Create the Deep X Route provider when no Deep X Route provider exists.

    It becomes the default only when no other provider exists. Returns the new
    provider, or None when a Deep X Route provider already exists.
    """
    existing = fetch_existing_llm_providers(
        db_session=db_session,
        flow_type_filter=[],
        include_model_configurations=False,
    )
    if any(p.provider == LlmProviderNames.DEEPX_ROUTE for p in existing):
        logger.info("A Deep X Route provider exists. Deep X Route bootstrap skipped.")
        return None
    # Another provider (for example one set up in onboarding) keeps the default.
    make_default = not existing

    model_configurations = _deepx_route_model_configurations(api_key)
    request = LLMProviderUpsertRequest(
        name=DEEPX_ROUTE_BOOTSTRAP_PROVIDER_NAME,
        provider=LlmProviderNames.DEEPX_ROUTE,
        api_key=api_key,
        api_key_changed=True,
        is_public=True,
        groups=[],
        model_configurations=model_configurations,
    )
    try:
        provider = upsert_llm_provider(
            llm_provider_upsert_request=request, db_session=db_session
        )
        if make_default:
            update_default_model(
                db_session=db_session,
                provider_id=provider.id,
                model=_pick_default_model(model_configurations),
                flow_type=LLMModelFlowType.CHAT,
            )
    finally:
        invalidate_provider_listing_cache()
    logger.notice(
        "Created the Deep X Route LLM provider (id=%s, default=%s).",
        provider.id,
        make_default,
    )
    return provider


def ensure_deepx_route_image_generation(db_session: Session, api_key: str) -> bool:
    """Create the default Deep X Route image generation config when none exists.

    Returns True when this call created the config.
    """
    if get_all_image_generation_configs(db_session):
        logger.info(
            "An image generation config exists. "
            "Deep X Route image generation bootstrap skipped."
        )
        return False

    create_image_generation_config(
        ImageGenerationConfigCreate(
            image_provider_id=DEEPX_ROUTE_BOOTSTRAP_IMAGE_PROVIDER_ID,
            model_name=DXR_IMAGE_MODEL,
            provider=ImageGenerationProviderName.DEEPX_ROUTE.value,
            api_key=api_key,
            is_default=True,
        ),
        db_session,
    )
    logger.notice("Created the default Deep X Route image generation config.")
    return True
