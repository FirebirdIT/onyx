"""The local code runner against the backend's real CodeInterpreterClient.

Needs a job Python with pandas and matplotlib (DEEPX_TEST_JOB_PYTHON), and on
macOS and Linux sandbox-runtime (DEEPX_TEST_SRT_COMMAND, for example
"node /path/srt/dist/cli.js"). Windows uses its AppContainer sandbox. The
tests are skipped when the settings are not set.
"""

import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import requests
import uvicorn

from onyx.deepx.code_runner import config
from onyx.deepx.code_runner.app import create_app
from onyx.tools.tool_implementations.python import code_interpreter_client
from onyx.tools.tool_implementations.python.code_interpreter_client import (
    CodeInterpreterClient,
    FileInput,
    StreamOutputEvent,
    StreamResultEvent,
)

SRT_COMMAND = os.environ.get("DEEPX_TEST_SRT_COMMAND", "")
JOB_PYTHON = os.environ.get("DEEPX_TEST_JOB_PYTHON", "")

IS_WINDOWS = sys.platform == "win32"

pytestmark = pytest.mark.skipif(
    not JOB_PYTHON or not (SRT_COMMAND or IS_WINDOWS),
    reason="DEEPX_TEST_JOB_PYTHON (and DEEPX_TEST_SRT_COMMAND) are not set",
)
posix_only = pytest.mark.skipif(IS_WINDOWS, reason="macOS and Linux only")
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="Windows only")

TOKEN = "test-runner-token"
CSV = b"month,sales\nJan,10\nFeb,14\nMar,9\n"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def runner_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    patch = pytest.MonkeyPatch()
    if SRT_COMMAND:
        patch.setattr(config, "SRT_COMMAND", shlex.split(SRT_COMMAND))
    patch.setattr(config, "JOB_PYTHON", JOB_PYTHON)
    patch.setattr(config, "MAX_MEMORY_MB", 400)
    patch.setattr(config, "API_TOKEN", TOKEN)
    patch.setattr(code_interpreter_client, "CODE_INTERPRETER_API_TOKEN", TOKEN)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(tmp_path_factory.mktemp("code-runner")),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(600):
        try:
            requests.get(f"{url}/health", timeout=1)
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=10)
    patch.undo()


def _run(
    client: CodeInterpreterClient,
    code: str,
    timeout_ms: int = 30000,
    files: list[FileInput] | None = None,
) -> tuple[str, str, StreamResultEvent]:
    stdout: list[str] = []
    stderr: list[str] = []
    result: StreamResultEvent | None = None
    for event in client.execute_streaming(
        code=code, timeout_ms=timeout_ms, files=files
    ):
        if isinstance(event, StreamOutputEvent):
            (stdout if event.stream == "stdout" else stderr).append(event.data)
        elif isinstance(event, StreamResultEvent):
            result = event
    assert result is not None
    return "".join(stdout), "".join(stderr), result


def test_health_reports_the_session_api_level(runner_url: str) -> None:
    with CodeInterpreterClient(runner_url) as client:
        health = client.health()
        assert health.healthy, health.error
        # Windows has no bash, so no sessions, and the bash tool is not offered.
        assert client.supports(CodeInterpreterClient.create_session) != IS_WINDOWS


def test_staged_csv_makes_chart_and_excel(runner_url: str) -> None:
    code = (
        "import pandas as pd, matplotlib.pyplot as plt\n"
        "df = pd.read_csv('data/sales.csv')\n"
        "df.plot(x='month', y='sales', kind='bar'); plt.savefig('chart.png')\n"
        "import os; os.makedirs('out', exist_ok=True)\n"
        "df.to_excel('out/sales.xlsx', index=False)\n"
        "print('total', df.sales.sum())\n"
    )
    with CodeInterpreterClient(runner_url) as client:
        csv_id = client.upload_file(CSV, "sales.csv")
        stdout, stderr, result = _run(
            client, code, files=[{"path": "data/sales.csv", "file_id": csv_id}]
        )
        assert result.exit_code == 0, stderr
        assert "total 33" in stdout
        paths = {f.path: f.file_id for f in result.files}
        assert set(paths) == {"chart.png", "out/sales.xlsx"}
        png_id = paths["chart.png"]
        assert png_id is not None
        assert client.download_file(png_id).startswith(b"\x89PNG")
        client.delete_file(png_id)
        with pytest.raises(requests.HTTPError):
            client.download_file(png_id)


def test_job_cannot_read_home_write_outside_or_use_network(runner_url: str) -> None:
    home = str(Path.home())
    code = f"""
import os, socket, urllib.request
def attempt(name, action):
    try:
        action(); print(name, 'ALLOWED')
    except Exception:
        print(name, 'blocked')
attempt('read', lambda: os.listdir({home!r}))
attempt('write', lambda: open({home!r} + '/deepx-runner-escape.txt', 'w').write('x'))
attempt('http', lambda: urllib.request.urlopen('https://example.com', timeout=5))
attempt('tcp', lambda: socket.create_connection(('1.1.1.1', 443), timeout=5))
"""
    with CodeInterpreterClient(runner_url) as client:
        stdout, _, result = _run(client, code)
    assert result.exit_code == 0
    assert "ALLOWED" not in stdout
    assert stdout.count("blocked") == 4
    assert not (Path.home() / "deepx-runner-escape.txt").exists()


@windows_only
def test_job_cannot_reach_local_ports(runner_url: str) -> None:
    port = int(runner_url.rsplit(":", 1)[1])
    code = f"""
import socket
try:
    socket.create_connection(('127.0.0.1', {port}), timeout=5).close()
    print('ALLOWED')
except OSError as e:
    print('blocked', e)
"""
    with CodeInterpreterClient(runner_url) as client:
        stdout, _, result = _run(client, code)
    assert result.exit_code == 0
    assert "blocked" in stdout, stdout


@windows_only
def test_junction_to_a_user_folder_is_not_followed(
    runner_url: str, tmp_path: Path
) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "secret.txt").write_text("user data")
    code = (
        "import subprocess\n"
        f"subprocess.run(['cmd', '/c', 'mklink', '/J', 'leak', {str(secret)!r}])\n"
    )
    with CodeInterpreterClient(runner_url) as client:
        _, _, result = _run(client, code)
    assert result.files == []


@windows_only
def test_child_processes_end_with_the_job(runner_url: str) -> None:
    code = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        "print(child.pid)\n"
    )
    with CodeInterpreterClient(runner_url) as client:
        stdout, stderr, result = _run(client, code)
    assert result.exit_code == 0, stderr
    pid = stdout.strip()
    time.sleep(1)
    tasks = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert pid not in tasks


@posix_only
def test_symlink_to_a_user_file_is_not_returned(runner_url: str) -> None:
    code = f"import os; os.symlink({str(Path.home() / '.zshrc')!r}, 'leak.txt')\n"
    with CodeInterpreterClient(runner_url) as client:
        _, _, result = _run(client, code)
    assert result.files == []


def test_endless_loop_times_out(runner_url: str) -> None:
    with CodeInterpreterClient(runner_url) as client:
        _, _, result = _run(client, "while True: pass\n", timeout_ms=2000)
    assert result.timed_out
    assert result.exit_code is None


def test_memory_hog_is_stopped(runner_url: str) -> None:
    code = "x = []\nwhile True: x.append(bytearray(20_000_000))\n"
    with CodeInterpreterClient(runner_url) as client:
        _, stderr, result = _run(client, code, timeout_ms=30000)
    assert not result.timed_out
    assert result.exit_code != 0
    assert "more than 400 MB" in stderr


def test_bad_staged_path_is_rejected(runner_url: str) -> None:
    with CodeInterpreterClient(runner_url) as client:
        csv_id = client.upload_file(CSV, "sales.csv")
        response = client.session.post(
            f"{runner_url}/v1/execute",
            json={
                "code": "print(1)",
                "files": [{"path": "../x.csv", "file_id": csv_id}],
            },
            timeout=30,
        )
    assert response.status_code == 400


@posix_only
def test_bash_session_keeps_files_and_has_no_network(runner_url: str) -> None:
    with CodeInterpreterClient(runner_url) as client:
        csv_id = client.upload_file(CSV, "sales.csv")
        session = client.create_session(
            ttl_seconds=60, files=[{"path": "sales.csv", "file_id": csv_id}]
        )
        first = client.execute_bash_in_session(
            session.session_id, "wc -l < sales.csv && echo kept > note.txt"
        )
        assert first.exit_code == 0, first.stderr
        assert first.stdout.strip() == "4"
        second = client.execute_bash_in_session(session.session_id, "cat note.txt")
        assert second.stdout.strip() == "kept"
        offline = client.execute_bash_in_session(
            session.session_id, "curl -sS -m 5 https://example.com"
        )
        assert offline.exit_code != 0
        client.delete_session(session.session_id)
        with pytest.raises(requests.HTTPError):
            client.execute_bash_in_session(session.session_id, "true")


def test_requests_without_the_token_are_refused(runner_url: str) -> None:
    response = requests.get(f"{runner_url}/health", timeout=10)
    assert response.status_code == 401
    wrong = requests.get(
        f"{runner_url}/health",
        headers={"Authorization": "Bearer wrong"},
        timeout=10,
    )
    assert wrong.status_code == 401


def test_browser_requests_are_refused_even_with_the_token(runner_url: str) -> None:
    response = requests.post(
        f"{runner_url}/v1/execute",
        json={"code": "print(1)"},
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "https://evil.example",
        },
        timeout=10,
    )
    assert response.status_code == 403
