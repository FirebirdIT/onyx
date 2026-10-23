"""MIT LLM gateway: an OpenAI-compatible API in front of the configured providers.

Mounted only when the Enterprise Edition code is not loaded and the gateway is
available (a Deep X build), see ``onyx.main``. Callers authenticate with a PAT
whose scopes grant ``use:llm_gateway``; a Craft sandbox PAT also requires Craft
to be enabled for its owner. Session and API-key auth never qualify.
"""

import contextvars
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from onyx.auth.permissions import require_permission
from onyx.db.engine.sql_engine import get_session
from onyx.db.enums import Permission
from onyx.db.models import User
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.factory import llm_from_provider
from onyx.llm.interfaces import LLMUserIdentity
from onyx.server.features.build.craft_gateway import gateway_request_flow
from onyx.server.gateway.completions import run_completion, stream_completion
from onyx.server.gateway.configs import GATEWAY_PATH_PREFIX
from onyx.server.gateway.craft_session import craft_session_binder
from onyx.server.gateway.model_resolution import (
    list_gateway_models,
    resolve_gateway_model,
)
from onyx.server.gateway.models import ChatCompletionRequest, ModelListResponse
from onyx.server.gateway.request_translation import translate_request
from onyx.server.settings.store import load_settings
from onyx.tracing.flows import LLMFlow

# One instance: FastAPI then resolves the user once for the binder and the route.
_gateway_user = require_permission(Permission.USE_LLM_GATEWAY)

router = APIRouter(
    prefix=GATEWAY_PATH_PREFIX,
    dependencies=[Depends(craft_session_binder(_gateway_user))],
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def authorize_gateway_request(request: Request, user: User) -> LLMFlow:
    """The flow to tag this call with, or an error.

    The workspace switch (``llm_gateway_enabled``) applies to direct gateway
    callers only. Craft sandboxes follow the Craft settings instead.
    """
    flow = gateway_request_flow(request, user)
    if flow is None:
        raise OnyxError(
            OnyxErrorCode.INSUFFICIENT_PERMISSIONS,
            "The LLM gateway requires a personal access token with the "
            "use:llm_gateway scope.",
        )
    if (
        flow == LLMFlow.LLM_GATEWAY
        and not load_settings(raise_on_error=True).llm_gateway_enabled
    ):
        raise OnyxError(
            OnyxErrorCode.FEATURE_DISABLED,
            "The LLM gateway is disabled for this workspace.",
        )
    return flow


@router.get("/v1/models")
def gateway_list_models(
    request: Request,
    user: User = Depends(_gateway_user),
    db_session: Session = Depends(get_session),
) -> dict[str, Any]:
    authorize_gateway_request(request, user)
    catalog = list_gateway_models(db_session, user)
    return ModelListResponse.from_catalog(catalog).to_wire()


@router.post("/v1/chat/completions")
def gateway_chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    user: User = Depends(_gateway_user),
    db_session: Session = Depends(get_session),
) -> Response:
    flow = authorize_gateway_request(request, user)
    provider, model_name = resolve_gateway_model(db_session, user, body.model)
    call = translate_request(body)
    try:
        llm = llm_from_provider(
            model_name=model_name,
            llm_provider=provider,
            temperature=body.temperature,
        )
    except ValueError as e:
        raise OnyxError(OnyxErrorCode.INVALID_INPUT, str(e))
    user_identity = LLMUserIdentity(user_id=str(user.id))

    if not body.stream:
        return JSONResponse(
            content=run_completion(llm, call, flow, body.model, user_identity)
        )

    # This copy carries the tenant and the bound Craft session into the worker.
    context = contextvars.copy_context()
    return StreamingResponse(
        stream_completion(llm, call, flow, body.model, user_identity, context),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
