"""File rules of the local code runner: staged paths and returned files."""

import os
import sys
from pathlib import Path

import pytest

from onyx.deepx.code_runner.models import StagedFile
from onyx.deepx.code_runner.workspace import (
    FileStore,
    collect_outputs,
    safe_relative_path,
    snapshot,
    stage_files,
)
from onyx.error_handling.exceptions import OnyxError


@pytest.mark.parametrize(
    "raw", ["", ".", "/etc/passwd", "../up.txt", "a/../../up.txt", "..\\up.txt"]
)
def test_unsafe_staged_paths_are_rejected(raw: str) -> None:
    with pytest.raises(OnyxError):
        safe_relative_path(raw)


def test_nested_staged_path_is_accepted() -> None:
    assert safe_relative_path("data/sales.csv").parts == ("data", "sales.csv")


def test_only_new_or_changed_regular_files_are_returned(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "files")
    work = tmp_path / "work"
    work.mkdir()
    input_id = store.save(b"a,b\n", "in.csv")
    stage_files(store, [StagedFile(path="in.csv", file_id=input_id)], work)
    before = snapshot(work)

    (work / "chart.png").write_bytes(b"png\r\n\x1a")
    (work / "out").mkdir()
    (work / "out" / "report.xlsx").write_bytes(b"xlsx")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "secret.txt").write_text("private")
    if sys.platform == "win32":
        # A symlink needs a privilege on Windows, but anyone can make a junction.
        import _winapi

        _winapi.CreateJunction(str(secret), str(work / "leak"))
    else:
        os.symlink(secret / "secret.txt", work / "leak.txt")
        os.symlink(secret, work / "leak")

    outputs = collect_outputs(store, work, before, max_files=10, max_bytes=1024)

    assert [f.path for f in outputs] == ["chart.png", "out/report.xlsx"]
    first_id = outputs[0].file_id
    assert first_id is not None
    # Copied as bytes, also on Windows.
    assert store.content_path(first_id).read_bytes() == b"png\r\n\x1a"


def test_output_limits_stop_collection(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "files")
    work = tmp_path / "work"
    work.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (work / name).write_bytes(b"12345")

    by_count = collect_outputs(store, work, {}, max_files=2, max_bytes=1024)
    by_size = collect_outputs(store, work, {}, max_files=10, max_bytes=12)

    assert [f.path for f in by_count] == ["a.txt", "b.txt"]
    assert [f.path for f in by_size] == ["a.txt", "b.txt"]


def test_unknown_file_ids_are_not_found(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "files")
    for file_id in ("missing", "../files", "0" * 32):
        with pytest.raises(OnyxError):
            store.content_path(file_id)
