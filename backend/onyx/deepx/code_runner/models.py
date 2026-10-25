"""Request and response bodies. They match `code_interpreter_client.py`."""

from typing import Literal

from pydantic import BaseModel, Field


class StagedFile(BaseModel):
    path: str
    file_id: str


class ExecuteRequest(BaseModel):
    code: str
    timeout_ms: int = Field(default=30000, gt=0)
    stdin: str | None = None
    files: list[StagedFile] = Field(default_factory=list)


class WorkspaceFile(BaseModel):
    path: str
    kind: Literal["file", "directory"]
    file_id: str | None = None


class JobResult(BaseModel):
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    files: list[WorkspaceFile]


class ExecuteResponse(JobResult):
    stdout: str
    stderr: str


class OutputEvent(BaseModel):
    stream: Literal["stdout", "stderr"]
    data: str


class ErrorEvent(BaseModel):
    message: str


class CreateSessionRequest(BaseModel):
    ttl_seconds: int = Field(default=15 * 60, gt=0)
    files: list[StagedFile] = Field(default_factory=list)


class CreateSessionResponse(BaseModel):
    session_id: str
    expires_at: float


class BashRequest(BaseModel):
    cmd: str
    timeout_ms: int = Field(default=30000, gt=0)


class BashResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int


class UploadResponse(BaseModel):
    file_id: str
