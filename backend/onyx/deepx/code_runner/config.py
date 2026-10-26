"""Settings of the local code runner, read once from the environment."""

import os
import shlex
import sys
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


# Windows has no sandbox-runtime: jobs run in an AppContainer
# (windows_sandbox.py), and there is no bash.
IS_WINDOWS = sys.platform == "win32"

# The API level of the code-interpreter container that this service matches.
# Sessions and bash need 0.4.0. Windows has no bash, so it reports the level
# before them, and the backend does not offer the bash tool.
API_VERSION = "0.3.0" if IS_WINDOWS else "0.4.0"

# Jobs, sessions and stored files live here. Everything under it is temporary.
DATA_DIR = Path(
    os.environ.get("DEEPX_CODE_RUNNER_DATA_DIR")
    or Path.home() / ".deepx" / "code-runner"
)

# Requests must send `Authorization: Bearer <token>`. The engine makes a random
# token at each start and gives it to the backend as CODE_INTERPRETER_API_TOKEN.
# Empty accepts requests with no token (tests only).
API_TOKEN = os.environ.get("DEEPX_CODE_RUNNER_TOKEN", "")

# The Python that runs the jobs. It has the data and office libraries.
JOB_PYTHON = os.environ.get("DEEPX_CODE_RUNNER_PYTHON", "python3")

# The sandbox-runtime command, for example "node /path/to/srt/dist/cli.js".
SRT_COMMAND: list[str] = shlex.split(
    os.environ.get("DEEPX_CODE_RUNNER_SRT_COMMAND", "srt")
)

# Folders that jobs may not read. Defaults to the home folder of the user.
DENY_READ: list[str] = [
    path
    for path in os.environ.get("DEEPX_CODE_RUNNER_DENY_READ", str(Path.home())).split(
        os.pathsep
    )
    if path
]

# More folders that jobs may read inside DENY_READ, for example the bundle.
EXTRA_READ: list[str] = [
    path
    for path in os.environ.get("DEEPX_CODE_RUNNER_EXTRA_READ", "").split(os.pathsep)
    if path
]

MAX_CONCURRENT_JOBS = _int_env("DEEPX_CODE_RUNNER_MAX_JOBS", 2)
MAX_MEMORY_MB = _int_env("DEEPX_CODE_RUNNER_MAX_MEMORY_MB", 2048)
# Windows only: the most processes that one job can run at a time.
MAX_JOB_PROCESSES = _int_env("DEEPX_CODE_RUNNER_MAX_JOB_PROCESSES", 64)
MAX_TIMEOUT_MS = _int_env("DEEPX_CODE_RUNNER_MAX_TIMEOUT_MS", 10 * 60 * 1000)
MAX_SESSION_TTL_SECONDS = _int_env("DEEPX_CODE_RUNNER_MAX_SESSION_TTL", 2 * 60 * 60)
MAX_UPLOAD_BYTES = _int_env("DEEPX_CODE_RUNNER_MAX_UPLOAD_MB", 200) * 1024 * 1024
MAX_OUTPUT_FILES = _int_env("DEEPX_CODE_RUNNER_MAX_OUTPUT_FILES", 200)
MAX_OUTPUT_BYTES = _int_env("DEEPX_CODE_RUNNER_MAX_OUTPUT_MB", 500) * 1024 * 1024

# How often the memory watchdog checks a running job.
WATCHDOG_INTERVAL_SECONDS = 0.05

# Seconds a client waits before it retries when all job slots are busy.
BUSY_RETRY_AFTER_SECONDS = 1
