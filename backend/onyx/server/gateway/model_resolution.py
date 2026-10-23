"""Resolve a gateway model id to a provider the caller may use."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from onyx.db.llm import (
    fetch_accessible_llm_provider_by_id,
    fetch_all_accessible_llm_providers,
)
from onyx.db.models import User
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.server.gateway.model_catalog import build_gateway_model_catalog
from onyx.server.gateway.models import GatewayModelDescriptor
from onyx.server.manage.llm.models import LLMProviderView


@dataclass(frozen=True)
class GatewayModelId:
    """The wire id ``"<provider_id>/<model_name>"`` that the catalog emits."""

    provider_id: int
    model_name: str


def parse_gateway_model_id(model_id: str) -> GatewayModelId | None:
    """Split on the first ``/`` only: model names can contain slashes."""
    provider_id, separator, model_name = model_id.partition("/")
    if not separator or not model_name or not provider_id.isdigit():
        return None
    return GatewayModelId(provider_id=int(provider_id), model_name=model_name)


def _model_not_found(model_id: str) -> OnyxError:
    return OnyxError(
        OnyxErrorCode.NOT_FOUND,
        f"Model '{model_id}' does not exist or you do not have access to it.",
    )


def resolve_gateway_model(
    db_session: Session, user: User, model_id: str
) -> tuple[LLMProviderView, str]:
    """The provider (with its key) and model name for ``model_id``.

    Uses the same access rules as the model catalog. An unknown, hidden or
    inaccessible model is NOT_FOUND. There is no fallback to a default model:
    the caller selected this model explicitly.
    """
    parsed = parse_gateway_model_id(model_id)
    if parsed is None:
        raise _model_not_found(model_id)
    provider = fetch_accessible_llm_provider_by_id(db_session, user, parsed.provider_id)
    if provider is None:
        raise _model_not_found(model_id)
    servable = any(
        model.name == parsed.model_name and model.is_visible and model.supports_chat
        for model in provider.model_configurations
    )
    if not servable:
        raise _model_not_found(model_id)
    return provider, parsed.model_name


def list_gateway_models(
    db_session: Session, user: User
) -> list[GatewayModelDescriptor]:
    """Every model the caller can send to the gateway."""
    return build_gateway_model_catalog(
        fetch_all_accessible_llm_providers(db_session, user)
    )
