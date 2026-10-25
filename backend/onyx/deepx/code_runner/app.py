"""HTTP API of the local code runner."""

import asyncio
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from onyx.deepx.code_runner import config
from onyx.deepx.code_runner.models import (
    BashRequest,
    BashResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    ErrorEvent,
    ExecuteRequest,
    ExecuteResponse,
    JobResult,
    OutputEvent,
    UploadResponse,
)
from onyx.deepx.code_runner.runner import Runner
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import (
    OnyxError,
    onyx_error_to_json_response,
    register_onyx_exception_handlers,
)
from onyx.utils.logger import setup_logger

logger = setup_logger()


def _sse(event: str, body: str) -> str:
    return f"event: {event}\ndata: {body}\n\n"


async def _only_the_engine(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # A browser sends Origin on every cross-site request. The backend never
    # does, so this stops web pages that post to 127.0.0.1.
    if "origin" in request.headers:
        return onyx_error_to_json_response(
            OnyxError(OnyxErrorCode.UNAUTHORIZED, "Browser requests are not allowed")
        )
    if config.API_TOKEN and not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {config.API_TOKEN}"
    ):
        return onyx_error_to_json_response(
            OnyxError(OnyxErrorCode.UNAUTHENTICATED, "A valid token is required")
        )
    return await call_next(request)


def create_app(data_dir: Path = config.DATA_DIR) -> FastAPI:
    runner_holder: list[Runner] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runner_holder.append(Runner(data_dir))
        sweeper = asyncio.create_task(runner_holder[0].sweep_sessions_forever())
        yield
        sweeper.cancel()

    app = FastAPI(title="Deep X code runner", lifespan=lifespan)
    register_onyx_exception_handlers(app)

    app.middleware("http")(_only_the_engine)

    def runner() -> Runner:
        return runner_holder[0]

    @app.get("/health")
    def health() -> dict[str, str]:
        problem = runner().problem
        if problem:
            return {
                "status": "error",
                "message": problem,
                "version": config.API_VERSION,
            }
        return {"status": "ok", "version": config.API_VERSION}

    @app.post("/v1/execute")
    async def execute(request: ExecuteRequest) -> ExecuteResponse:
        job = await runner().prepare(request)
        stdout: list[str] = []
        stderr: list[str] = []
        async for event in runner().stream(job):
            if isinstance(event, OutputEvent):
                (stdout if event.stream == "stdout" else stderr).append(event.data)
            else:
                return ExecuteResponse(
                    stdout="".join(stdout), stderr="".join(stderr), **event.model_dump()
                )
        raise OnyxError(OnyxErrorCode.INTERNAL_ERROR, "The job ended with no result")

    @app.post("/v1/execute/stream")
    async def execute_stream(request: ExecuteRequest) -> StreamingResponse:
        job = await runner().prepare(request)

        async def events() -> AsyncIterator[str]:
            try:
                async for event in runner().stream(job):
                    if isinstance(event, JobResult):
                        yield _sse("result", event.model_dump_json())
                    else:
                        yield _sse("output", event.model_dump_json())
            except Exception:
                logger.exception("Code runner job failed")
                yield _sse(
                    "error", ErrorEvent(message="The job failed").model_dump_json()
                )

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/v1/sessions")
    def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
        return runner().create_session(request.ttl_seconds, request.files)

    @app.delete("/v1/sessions/{session_id}")
    def delete_session(session_id: str) -> None:
        runner().delete_session(session_id)

    @app.post("/v1/sessions/{session_id}/bash")
    async def bash(session_id: str, request: BashRequest) -> BashResponse:
        return await runner().run_bash(session_id, request.cmd, request.timeout_ms)

    @app.post("/v1/files")
    async def upload_file(file: UploadFile) -> UploadResponse:
        content = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(content) > config.MAX_UPLOAD_BYTES:
            raise OnyxError(OnyxErrorCode.PAYLOAD_TOO_LARGE, "The file is too large")
        file_id = runner().store.save(content, file.filename or "file")
        return UploadResponse(file_id=file_id)

    @app.get("/v1/files/{file_id}")
    def download_file(file_id: str) -> FileResponse:
        return FileResponse(runner().store.content_path(file_id))

    @app.delete("/v1/files/{file_id}")
    def delete_file(file_id: str) -> None:
        runner().store.delete(file_id)

    return app
