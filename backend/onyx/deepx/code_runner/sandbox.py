"""Run one job inside sandbox-runtime, with time and memory limits."""

import asyncio
import codecs
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

from onyx.deepx.code_runner import config
from onyx.deepx.code_runner.models import OutputEvent
from onyx.deepx.code_runner.workspace import JobDirs
from onyx.utils.logger import setup_logger

logger = setup_logger()

_READ_CHUNK_BYTES = 4096


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    duration_ms: int


# Prints the Python folders, and the install folder of each step of the
# executable's symlink chain. The sandbox checks every step of a chain.
_ROOTS_SCRIPT = """
import os, sys
roots = {sys.prefix, sys.base_prefix}
path = sys.executable
for _ in range(20):
    roots.add(os.path.dirname(os.path.dirname(path)))
    if not os.path.islink(path):
        break
    path = os.path.join(os.path.dirname(path), os.readlink(path))
roots.add(os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))))
print("\\n".join(sorted(roots)))
"""


def python_roots(job_python: str) -> list[str]:
    """The folders of the job Python, which jobs must be able to read."""
    output = subprocess.run(
        [job_python, "-c", _ROOTS_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return [line for line in output.splitlines() if line]


def build_matplotlib_cache(job_python: str, cache_dir: Path) -> None:
    """Build the matplotlib font cache once. Each job gets a copy, so a job
    does not spend about 10 s to build it again."""
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True)
    result = subprocess.run(
        [job_python, "-c", "import matplotlib.pyplot"],
        capture_output=True,
        env={**os.environ, "MPLCONFIGDIR": str(cache_dir), "MPLBACKEND": "Agg"},
        timeout=300,
    )
    if result.returncode != 0:
        logger.warning("The job Python has no matplotlib. Jobs build no font cache.")
        shutil.rmtree(cache_dir, ignore_errors=True)


def sandbox_settings(dirs: JobDirs, readable: list[str]) -> dict:
    """srt rules: read nothing in the denied folders except the job and the
    runtimes, write only the job's own folders, and no network."""
    return {
        "filesystem": {
            "denyRead": list(config.DENY_READ),
            "allowRead": [str(dirs.root), str(dirs.tmp), *readable],
            "allowWrite": dirs.writable(),
            "denyWrite": [],
        },
        "network": {"allowedDomains": [], "deniedDomains": []},
    }


def _job_env(dirs: JobDirs, job_python: str) -> dict[str, str]:
    python_bin = str(Path(job_python).parent) if os.sep in job_python else ""
    path = os.pathsep.join(p for p in [python_bin, os.environ.get("PATH", "")] if p)
    return {
        "PATH": path,
        "HOME": str(dirs.home),
        "TMPDIR": str(dirs.tmp),
        "MPLCONFIGDIR": str(dirs.home / ".matplotlib"),
        "MPLBACKEND": "Agg",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8",
    }


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """Kill the job's process group, and every descendant that left it."""
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for child in descendants:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue


def _group_rss_bytes(pid: int) -> int:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for process in processes:
        try:
            total += process.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total


_Queue = asyncio.Queue[OutputEvent | None]


async def _pump(
    stream: asyncio.StreamReader | None,
    name: Literal["stdout", "stderr"],
    queue: _Queue,
) -> None:
    if stream is None:
        return
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while chunk := await stream.read(_READ_CHUNK_BYTES):
        text = decoder.decode(chunk)
        if text:
            await queue.put(OutputEvent(stream=name, data=text))
    tail = decoder.decode(b"", final=True)
    if tail:
        await queue.put(OutputEvent(stream=name, data=tail))


async def _pump_all(process: asyncio.subprocess.Process, queue: _Queue) -> None:
    await asyncio.gather(
        _pump(process.stdout, "stdout", queue),
        _pump(process.stderr, "stderr", queue),
    )
    await queue.put(None)


async def _watch_memory(
    process: asyncio.subprocess.Process, limit_bytes: int, stopped: list[bool]
) -> None:
    while process.returncode is None:
        if _group_rss_bytes(process.pid) > limit_bytes:
            stopped.append(True)
            _kill_group(process)
            return
        await asyncio.sleep(config.WATCHDOG_INTERVAL_SECONDS)


async def _feed_stdin(process: asyncio.subprocess.Process, stdin: str | None) -> None:
    if process.stdin is None:
        return
    try:
        if stdin:
            process.stdin.write(stdin.encode())
            await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()


async def _next_event(queue: _Queue, deadline: float) -> OutputEvent | None | bool:
    """The next output event, None at the end of output, or False on timeout."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    try:
        return await asyncio.wait_for(queue.get(), timeout=remaining)
    except asyncio.TimeoutError:
        return False


async def run_in_sandbox(
    dirs: JobDirs,
    command: list[str],
    readable: list[str],
    timeout_ms: int,
    stdin: str | None,
    result_box: list[ProcessResult],
) -> AsyncIterator[OutputEvent]:
    """Run `command` in the sandbox. Yields output as it comes, then puts the
    result into `result_box`. Closing the iterator stops the job."""
    settings_path = dirs.root / "srt-settings.json"
    settings_path.write_text(json.dumps(sandbox_settings(dirs, readable)))
    started = time.monotonic()
    deadline = started + min(timeout_ms, config.MAX_TIMEOUT_MS) / 1000

    process = await asyncio.create_subprocess_exec(
        *config.SRT_COMMAND,
        "-s",
        str(settings_path),
        *command,
        cwd=dirs.work,
        env=_job_env(dirs, config.JOB_PYTHON),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    queue: _Queue = asyncio.Queue()
    stopped_for_memory: list[bool] = []
    tasks = [
        asyncio.create_task(_pump_all(process, queue)),
        asyncio.create_task(
            _watch_memory(
                process, config.MAX_MEMORY_MB * 1024 * 1024, stopped_for_memory
            )
        ),
        asyncio.create_task(_feed_stdin(process, stdin)),
    ]
    timed_out = False
    try:
        while (event := await _next_event(queue, deadline)) is not None:
            if event is False:
                timed_out = True
                _kill_group(process)
                break
            if isinstance(event, OutputEvent):
                yield event
        await process.wait()
        # A child process can keep running after the main process ends.
        _kill_group(process)
        if stopped_for_memory:
            yield OutputEvent(
                stream="stderr",
                data=f"\nThe job was stopped because it used more than "
                f"{config.MAX_MEMORY_MB} MB of memory.\n",
            )
        result_box.append(
            ProcessResult(
                exit_code=None if timed_out else process.returncode,
                timed_out=timed_out,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
    finally:
        _kill_group(process)
        for task in tasks:
            task.cancel()
