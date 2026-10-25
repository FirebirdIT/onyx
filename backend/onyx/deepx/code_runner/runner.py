"""Job slots, batch and streamed jobs, and bash sessions."""

import asyncio
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from onyx.deepx.code_runner import config
from onyx.deepx.code_runner.models import (
    BashResponse,
    CreateSessionResponse,
    ExecuteRequest,
    JobResult,
    OutputEvent,
    StagedFile,
)
from onyx.deepx.code_runner.sandbox import (
    ProcessResult,
    build_matplotlib_cache,
    python_roots,
    run_in_sandbox,
)
from onyx.deepx.code_runner.workspace import (
    FileStore,
    JobDirs,
    Snapshot,
    collect_outputs,
    snapshot,
    stage_files,
)
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.utils.logger import setup_logger

logger = setup_logger()

_SESSION_SWEEP_SECONDS = 30


@dataclass
class PreparedJob:
    dirs: JobDirs
    before: Snapshot
    request: ExecuteRequest


@dataclass
class Session:
    dirs: JobDirs
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Runner:
    def __init__(self, data_dir: Path) -> None:
        self._jobs_dir = data_dir / "jobs"
        self._sessions_dir = data_dir / "sessions"
        for folder in (self._jobs_dir, self._sessions_dir):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
        self.store = FileStore(data_dir / "files")
        self._mpl_cache = data_dir / "matplotlib"
        self._slots = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self._sessions: dict[str, Session] = {}
        self._readable: list[str] = []
        self.problem = ""
        self._check_tools()

    def _check_tools(self) -> None:
        try:
            self._readable = [*python_roots(config.JOB_PYTHON), *config.EXTRA_READ]
            build_matplotlib_cache(config.JOB_PYTHON, self._mpl_cache)
            subprocess.run(
                [*config.SRT_COMMAND, "--version"],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as e:
            self.problem = "The sandbox or the job Python is not available"
            logger.error("%s: %s", self.problem, e)

    def _require_ready(self) -> None:
        if self.problem:
            raise OnyxError(OnyxErrorCode.SERVICE_UNAVAILABLE, self.problem)

    async def _take_slot(self) -> None:
        if self._slots.locked():
            raise OnyxError(
                OnyxErrorCode.RATE_LIMITED,
                "All job slots are busy",
                headers={"Retry-After": str(config.BUSY_RETRY_AFTER_SECONDS)},
            )
        await self._slots.acquire()

    def _new_dirs(self, parent: Path, files: list[StagedFile]) -> JobDirs:
        dirs = JobDirs.create(parent)
        try:
            if self._mpl_cache.is_dir():
                shutil.copytree(self._mpl_cache, dirs.home / ".matplotlib")
            stage_files(self.store, files, dirs.work)
        except Exception:
            dirs.remove()
            raise
        return dirs

    async def prepare(self, request: ExecuteRequest) -> PreparedJob:
        """Take a job slot and stage the files. Fails before any output, so
        the caller can still answer with an error status."""
        self._require_ready()
        await self._take_slot()
        try:
            dirs = self._new_dirs(self._jobs_dir, request.files)
            (dirs.root / "main.py").write_text(request.code)
            return PreparedJob(dirs=dirs, before=snapshot(dirs.work), request=request)
        except Exception:
            self._slots.release()
            raise

    async def stream(self, job: PreparedJob) -> AsyncIterator[OutputEvent | JobResult]:
        """Run a prepared job. Yields output, then one `JobResult`."""
        box: list[ProcessResult] = []
        try:
            async for event in run_in_sandbox(
                job.dirs,
                [config.JOB_PYTHON, str(job.dirs.root / "main.py")],
                self._readable,
                job.request.timeout_ms,
                job.request.stdin,
                box,
            ):
                yield event
            result = box[0]
            files = collect_outputs(
                self.store,
                job.dirs.work,
                job.before,
                config.MAX_OUTPUT_FILES,
                config.MAX_OUTPUT_BYTES,
            )
            yield JobResult(
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
                files=files,
            )
        finally:
            job.dirs.remove()
            self._slots.release()

    def create_session(
        self, ttl_seconds: int, files: list[StagedFile]
    ) -> CreateSessionResponse:
        self._require_ready()
        session_id = uuid.uuid4().hex
        ttl = min(ttl_seconds, config.MAX_SESSION_TTL_SECONDS)
        dirs = self._new_dirs(self._sessions_dir, files)
        session = Session(dirs=dirs, expires_at=time.time() + ttl)
        self._sessions[session_id] = session
        return CreateSessionResponse(
            session_id=session_id, expires_at=session.expires_at
        )

    def delete_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise OnyxError(OnyxErrorCode.SESSION_NOT_FOUND, "Session not found")
        session.dirs.remove()

    async def run_bash(
        self, session_id: str, cmd: str, timeout_ms: int
    ) -> BashResponse:
        session = self._sessions.get(session_id)
        if session is None or session.expires_at <= time.time():
            raise OnyxError(OnyxErrorCode.SESSION_NOT_FOUND, "Session not found")
        await self._take_slot()
        try:
            async with session.lock:
                box: list[ProcessResult] = []
                stdout: list[str] = []
                stderr: list[str] = []
                async for event in run_in_sandbox(
                    session.dirs, ["-c", cmd], self._readable, timeout_ms, None, box
                ):
                    (stdout if event.stream == "stdout" else stderr).append(event.data)
                result = box[0]
                return BashResponse(
                    stdout="".join(stdout),
                    stderr="".join(stderr),
                    exit_code=result.exit_code,
                    timed_out=result.timed_out,
                    duration_ms=result.duration_ms,
                )
        finally:
            self._slots.release()

    async def sweep_sessions_forever(self) -> None:
        while True:
            now = time.time()
            for session_id, session in list(self._sessions.items()):
                if session.expires_at <= now and not session.lock.locked():
                    self._sessions.pop(session_id, None)
                    session.dirs.remove()
            await asyncio.sleep(_SESSION_SWEEP_SECONDS)
