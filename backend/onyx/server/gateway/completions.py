"""Run one Chat Completions call through the shared LiteLLM client.

Every call opens an ``llm_gateway_trace`` and an ``llm_generation_span`` tagged
with the resolved ``LLMFlow``, so usage is recorded like any other LLM call.
"""

import asyncio
import contextvars
import json
import threading
from collections.abc import AsyncIterator, Callable, Generator
from typing import Any

from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.exceptions import (
    LLMContextLimitError,
    LLMRateLimitError,
    LLMTimeoutError,
    litellm_exception_to_error_msg,
)
from onyx.llm.interfaces import LLMUserIdentity
from onyx.llm.multi_llm import LitellmLLM
from onyx.server.gateway.configs import GATEWAY_LLM_TOTAL_TIMEOUT_SECONDS
from onyx.server.gateway.models import ChatCompletionChunk, ChatCompletionResponse
from onyx.server.gateway.request_translation import GatewayCall
from onyx.server.gateway.tracing import llm_gateway_trace
from onyx.tracing.flows import LLMFlow
from onyx.tracing.llm_utils import (
    llm_generation_span,
    record_llm_response,
    record_llm_span_output,
)
from onyx.utils.logger import setup_logger
from onyx.utils.threadpool_concurrency import start_thread_with_context

logger = setup_logger()

SSE_DONE = "data: [DONE]\n\n"


def _error_code_for(error: Exception) -> OnyxErrorCode:
    from litellm.exceptions import BadRequestError, RateLimitError, Timeout

    cause = error.__cause__ if isinstance(error.__cause__, Exception) else error
    if isinstance(error, (LLMTimeoutError, Timeout)) or isinstance(cause, Timeout):
        return OnyxErrorCode.GATEWAY_TIMEOUT
    if isinstance(error, (LLMRateLimitError, RateLimitError)) or isinstance(
        cause, RateLimitError
    ):
        return OnyxErrorCode.RATE_LIMITED
    if isinstance(error, (LLMContextLimitError, BadRequestError)):
        return OnyxErrorCode.INVALID_INPUT
    return OnyxErrorCode.LLM_PROVIDER_ERROR


def gateway_error(error: Exception, llm: LitellmLLM) -> OnyxError:
    """A client-safe error: the message is classified and redacted."""
    message, _, _ = litellm_exception_to_error_msg(
        error, llm, fallback_to_error_msg=True
    )
    return OnyxError(_error_code_for(error), llm.redact_error(message))


def run_completion(
    llm: LitellmLLM,
    call: GatewayCall,
    flow: LLMFlow,
    model_id: str,
    user_identity: LLMUserIdentity,
) -> dict[str, Any]:
    """One non-streaming completion as a Chat Completions response body."""
    with llm_gateway_trace(flow, model_id):
        with llm_generation_span(
            llm=llm, flow=flow, input_messages=call.prompt, tools=call.tools
        ) as span:
            try:
                response = llm.invoke_raw(
                    prompt=call.prompt,
                    tools=call.tools,
                    tool_choice=call.tool_choice,
                    structured_response_format=call.structured_response_format,
                    max_tokens=call.max_tokens,
                    reasoning_effort=call.reasoning_effort,
                    user_identity=user_identity,
                    total_timeout_s=GATEWAY_LLM_TOTAL_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning("Gateway completion failed: %s", type(e).__name__)
                raise gateway_error(e, llm)
            record_llm_response(span, response)
    return ChatCompletionResponse.from_model_response(response, model_id).to_wire()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _error_frame(error: OnyxError) -> str:
    """In-band error: the HTTP status is already 200 once a stream starts."""
    return _sse(
        {
            "error": {
                "message": error.detail,
                "type": error.error_code.code.lower(),
                "code": error.error_code.code,
            }
        }
    )


def _stream_frames(
    llm: LitellmLLM,
    call: GatewayCall,
    flow: LLMFlow,
    model_id: str,
    user_identity: LLMUserIdentity,
    emit: Callable[[str], None],
    stopped: threading.Event,
) -> None:
    """Runs on its own thread, so the trace and span open here, not in the
    endpoint: a generator that Starlette resumes on other threads loses them."""
    with llm_gateway_trace(flow, model_id):
        with llm_generation_span(
            llm=llm, flow=flow, input_messages=call.prompt, tools=call.tools
        ) as span:
            content: list[str] = []
            reasoning: list[str] = []
            usage = None
            stream = llm.stream_raw(
                prompt=call.prompt,
                tools=call.tools,
                tool_choice=call.tool_choice,
                structured_response_format=call.structured_response_format,
                max_tokens=call.max_tokens,
                reasoning_effort=call.reasoning_effort,
                user_identity=user_identity,
            )
            try:
                for index, chunk in enumerate(stream):
                    if stopped.is_set():
                        break
                    delta = chunk.choice.delta
                    content.append(delta.content or "")
                    reasoning.append(delta.reasoning_content or "")
                    usage = chunk.usage or usage
                    frame = ChatCompletionChunk.from_stream_chunk(
                        chunk, model_id, include_role=index == 0
                    )
                    emit(_sse(frame.to_wire()))
            finally:
                if isinstance(stream, Generator):
                    stream.close()
                record_llm_span_output(
                    span,
                    "".join(content),
                    usage=usage,
                    reasoning="".join(reasoning) or None,
                )
    emit(SSE_DONE)


async def stream_completion(
    llm: LitellmLLM,
    call: GatewayCall,
    flow: LLMFlow,
    model_id: str,
    user_identity: LLMUserIdentity,
    context: contextvars.Context,
) -> AsyncIterator[str]:
    """SSE frames for one streaming completion.

    A worker thread runs the provider stream in ``context`` (the request
    context, with the tenant and the bound Craft session) and hands frames back
    through a queue. A client that disconnects stops the worker.
    """
    loop = asyncio.get_running_loop()
    frames: asyncio.Queue[str | None] = asyncio.Queue()
    stopped = threading.Event()

    def emit(frame: str | None) -> None:
        if not loop.is_closed():
            loop.call_soon_threadsafe(frames.put_nowait, frame)

    def worker() -> None:
        try:
            _stream_frames(llm, call, flow, model_id, user_identity, emit, stopped)
        except Exception as e:
            logger.warning("Gateway stream failed: %s", type(e).__name__)
            emit(_error_frame(gateway_error(e, llm)))
        finally:
            # None marks the end of the stream.
            emit(None)

    start_thread_with_context(
        worker, name="llm-gateway-stream", daemon=True, context=context
    )
    try:
        while (frame := await frames.get()) is not None:
            yield frame
    finally:
        stopped.set()
