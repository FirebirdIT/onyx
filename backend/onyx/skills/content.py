"""Helpers for reading skill source content."""

from collections.abc import Iterator, Mapping
from pathlib import Path

from onyx.db.models import Skill
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError
from onyx.file_store.file_store import FileStore, get_default_file_store
from onyx.skills.built_in import BuiltInSkillDefinition
from onyx.skills.bundle import (
    SKILL_MD_NAME,
    TEMPLATE_SUFFIX,
    read_custom_bundle_instructions,
    strip_skill_md_frontmatter,
)


def iter_builtin_skill_files(source_dir: Path) -> Iterator[Path]:
    """Yield source files, excluding hidden paths and Python caches."""
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(
            part == "__pycache__" or part.startswith(".")
            for part in path.relative_to(source_dir).parts
        ):
            continue
        yield path


def read_builtin_skill_instructions(definition: BuiltInSkillDefinition) -> str:
    source_path = definition.source_dir / SKILL_MD_NAME
    if not source_path.is_file():
        source_path = definition.source_dir / f"{SKILL_MD_NAME}{TEMPLATE_SUFFIX}"
    if not source_path.is_file():
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            f"Built-in skill '{definition.built_in_skill_id}' has no SKILL.md source.",
        )
    try:
        return strip_skill_md_frontmatter(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            f"Failed to read built-in skill '{definition.built_in_skill_id}'.",
        ) from exc


def read_custom_skill_bundle_instructions(
    skill: Skill,
    file_store: FileStore | None = None,
) -> str:
    bundle_bytes = read_custom_skill_bundle_bytes(skill, file_store)
    return read_custom_bundle_instructions(bundle_bytes)


def read_custom_skill_bundle_bytes(
    skill: Skill,
    file_store: FileStore | None = None,
) -> bytes:
    if skill.bundle_file_id is None:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            f"Custom skill '{skill.name}' has no bundle.",
        )
    store = file_store or get_default_file_store()
    try:
        bundle_bytes = store.read_file(skill.bundle_file_id).read()
    except Exception as exc:
        raise OnyxError(
            OnyxErrorCode.INTERNAL_ERROR,
            f"Failed to read bundle for skill '{skill.name}'.",
        ) from exc
    return bundle_bytes


def normalize_skill_file_path(path: str) -> str:
    """Normalize a caller-supplied path inside a skill, rejecting unsafe forms."""
    trimmed = path.strip()
    while trimmed.startswith("./"):
        trimmed = trimmed[2:]
    parts = trimmed.split("/")
    if (
        not trimmed
        or trimmed.startswith("/")
        or "\\" in trimmed
        or "\x00" in trimmed
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise OnyxError(
            OnyxErrorCode.INVALID_INPUT,
            f"'{path}' is not a safe path inside the skill.",
        )
    return trimmed


def read_skill_text_file(
    files: Mapping[str, bytes], path: str, *, max_bytes: int
) -> str:
    """Return one UTF-8 text file from a skill's ``{path: bytes}`` map.

    Rejects unsafe paths, unknown files, files over ``max_bytes`` and files
    that are not UTF-8 text.
    """
    lookup_path = normalize_skill_file_path(path)
    raw = files.get(lookup_path)
    if raw is None:
        raise OnyxError(
            OnyxErrorCode.NOT_FOUND,
            f"The skill has no file '{lookup_path}'.",
        )
    if len(raw) > max_bytes:
        raise OnyxError(
            OnyxErrorCode.PAYLOAD_TOO_LARGE,
            f"'{lookup_path}' is {len(raw)} bytes. The limit is {max_bytes} bytes.",
        )
    if b"\x00" in raw:
        raise OnyxError(
            OnyxErrorCode.INVALID_INPUT,
            f"'{lookup_path}' is a binary file and cannot be read as text.",
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OnyxError(
            OnyxErrorCode.INVALID_INPUT,
            f"'{lookup_path}' is not UTF-8 text and cannot be read.",
        ) from exc
