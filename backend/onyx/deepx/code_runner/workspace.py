"""Stored files, job folders, and the files that a job makes."""

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from onyx.deepx.code_runner.models import StagedFile, WorkspaceFile
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError

_FILE_ID = re.compile(r"^[0-9a-f]{32}$")

# sandbox-runtime makes its proxy socket in TMPDIR. A macOS socket path can
# have only about 104 characters, so each job's temp folder is short.
_SHORT_TMP_ROOT = "/tmp"  # noqa: S108  mkdtemp makes a private 0700 folder
_NAME_FILE = "name.json"
_CONTENT_FILE = "content"

# File state before a job: size and modification time for each relative path.
Snapshot = dict[str, tuple[int, int]]


@dataclass(frozen=True)
class JobDirs:
    """One job. Only `work`, `home` and `tmp` are writable inside the sandbox."""

    root: Path
    work: Path
    home: Path
    tmp: Path

    @staticmethod
    def create(parent: Path) -> "JobDirs":
        root = parent / uuid.uuid4().hex
        if sys.platform == "win32":
            # The job's AppContainer can use only the folders under `root`.
            tmp = root / "tmp"
        else:
            tmp = Path(
                tempfile.mkdtemp(prefix="deepx-job-", dir=_SHORT_TMP_ROOT)
            ).resolve()
        dirs = JobDirs(root=root, work=root / "work", home=root / "home", tmp=tmp)
        dirs.work.mkdir(parents=True)
        dirs.home.mkdir()
        tmp.mkdir(exist_ok=True)
        return dirs

    def writable(self) -> list[str]:
        return [str(self.work), str(self.home), str(self.tmp)]

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


class FileStore:
    """Files that the backend uploads, and files that jobs make."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes, filename: str) -> str:
        file_id = uuid.uuid4().hex
        folder = self._root / file_id
        folder.mkdir()
        (folder / _CONTENT_FILE).write_bytes(content)
        (folder / _NAME_FILE).write_text(json.dumps({"filename": filename}))
        return file_id

    def save_copy(self, source: Path, filename: str) -> str | None:
        """Copy a job file into the store. Returns None when `source` is not a
        regular file when it is opened, so a link swapped in late is not read.
        On Windows the job's processes have all ended by then."""
        if sys.platform == "win32":
            flags = os.O_RDONLY | os.O_BINARY
        else:
            flags = os.O_RDONLY | os.O_NOFOLLOW
        try:
            fd = os.open(source, flags)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            file_id = uuid.uuid4().hex
            folder = self._root / file_id
            folder.mkdir()
            with open(folder / _CONTENT_FILE, "wb") as target:
                shutil.copyfileobj(handle, target)
        (folder / _NAME_FILE).write_text(json.dumps({"filename": filename}))
        return file_id

    def content_path(self, file_id: str) -> Path:
        if not _FILE_ID.match(file_id):
            raise OnyxError(OnyxErrorCode.NOT_FOUND, "File not found")
        path = self._root / file_id / _CONTENT_FILE
        if not path.is_file():
            raise OnyxError(OnyxErrorCode.NOT_FOUND, "File not found")
        return path

    def delete(self, file_id: str) -> None:
        self.content_path(file_id)
        shutil.rmtree(self._root / file_id, ignore_errors=True)


def safe_relative_path(raw: str) -> PurePosixPath:
    """A staged path, checked so that it stays inside the work folder."""
    path = PurePosixPath(raw.replace("\\", "/"))
    if (
        not raw
        or path.is_absolute()
        or ".." in path.parts
        or path == PurePosixPath(".")
    ):
        raise OnyxError(OnyxErrorCode.INVALID_INPUT, f"Invalid file path: {raw}")
    return path


def stage_files(store: FileStore, files: list[StagedFile], work: Path) -> None:
    for staged in files:
        target = work.joinpath(*safe_relative_path(staged.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(store.content_path(staged.file_id), target)


def _is_link(info: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point, such as a junction."""
    if stat.S_ISLNK(info.st_mode):
        return True
    if sys.platform == "win32":
        return bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def _job_files(folder: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """The regular files under `folder`, in name order. Links and junctions
    are not followed: the runner reads files outside the sandbox, so a link
    could otherwise copy a file of the user out of a job."""
    for path in sorted(folder.iterdir()):
        info = path.lstat()
        if _is_link(info):
            continue
        if stat.S_ISDIR(info.st_mode):
            yield from _job_files(path)
        elif stat.S_ISREG(info.st_mode):
            yield path, info


def snapshot(work: Path) -> Snapshot:
    return {
        path.relative_to(work).as_posix(): (info.st_size, info.st_mtime_ns)
        for path, info in _job_files(work)
    }


def collect_outputs(
    store: FileStore,
    work: Path,
    before: Snapshot,
    max_files: int,
    max_bytes: int,
) -> list[WorkspaceFile]:
    """Store the files that the job made or changed, and list them."""
    outputs: list[WorkspaceFile] = []
    total_bytes = 0
    for path, info in _job_files(work):
        relative = path.relative_to(work).as_posix()
        if before.get(relative) == (info.st_size, info.st_mtime_ns):
            continue
        if len(outputs) >= max_files or total_bytes + info.st_size > max_bytes:
            break
        file_id = store.save_copy(path, path.name)
        if file_id is None:
            continue
        total_bytes += info.st_size
        outputs.append(WorkspaceFile(path=relative, kind="file", file_id=file_id))
    return outputs
