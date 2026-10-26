"""Local code interpreter for the Deep X desktop engine.

It serves the HTTP API of the code-interpreter container that the backend calls
(`onyx/tools/tool_implementations/python/code_interpreter_client.py`), but it
runs each job as a normal process inside Anthropic's sandbox-runtime (`srt`)
on macOS and Linux, and inside an AppContainer on Windows (`windows_sandbox.py`),
so the engine does not need Docker.
"""
