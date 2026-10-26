"""Start the local code runner: `python -m onyx.deepx.code_runner`."""

import os

import uvicorn

from onyx.deepx.code_runner.app import create_app


def main() -> None:
    host = os.environ.get("DEEPX_CODE_RUNNER_HOST", "127.0.0.1")
    port = int(os.environ.get("DEEPX_CODE_RUNNER_PORT", "8790"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
