"""Built-in chat tool that reads the user's skills.

Chat has no Craft sandbox, so the model reads a skill's `SKILL.md`
instructions (and its text files) through this tool and follows them in the
conversation. It offers the same effective skills as a sandbox push
(`db/skill.py:list_runtime_skills_for_user`): built-in skills, enabled valid
custom skills, and skills of external apps that are ready for the user. Files
are rendered the same way as a push (`skills/push.py:build_skill_files_for_user`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from onyx.chat.emitter import Emitter
from onyx.db.engine.sql_engine import get_session_with_current_tenant
from onyx.db.models import Skill, User
from onyx.error_handling.exceptions import OnyxError
from onyx.llm.models import ToolDefinition
from onyx.server.features.build.configs import ENABLE_CRAFT
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import (
    CustomToolDelta,
    CustomToolStart,
    Packet,
)
from onyx.skills.bundle import SKILL_MD_NAME, strip_skill_md_frontmatter
from onyx.skills.content import normalize_skill_file_path, read_skill_text_file
from onyx.tools.interface import Tool
from onyx.tools.models import ToolCallException, ToolResponse
from onyx.utils.logger import setup_logger

logger = setup_logger()

NAME_FIELD = "name"
PATH_FIELD = "path"

# Built-in skills that only make sense inside Craft. Chat leaves them out
# while Craft is off.
CRAFT_ONLY_BUILT_IN_SKILL_IDS = frozenset({"craft-documentation"})

# The tool description lists at most this many skills.
MAX_INDEXED_SKILLS = 50
# Each listed description is cut to this many characters.
MAX_INDEX_DESCRIPTION_CHARS = 200
# A file larger than this is not returned.
MAX_SKILL_FILE_BYTES = 100 * 1024
# The SKILL.md result lists at most this many other files.
MAX_LISTED_FILES = 200

_SANDBOX_NOTE = (
    "Some skills were written for a coding sandbox. If the instructions name "
    "sandbox commands, scripts or paths, do the same work with the tools you "
    "have in this chat (for example Python, file creation or search). Do not "
    "claim to run a command or script that you cannot run."
)

_DESCRIPTION_INTRO = (
    "Read a skill. A skill is a set of instructions, sometimes with reference "
    "files, that tells you how to do one type of task. Before you start a task "
    "that matches a skill below, call read_skill with the skill name, then "
    "follow its instructions. Call read_skill again with `path` to read a file "
    "that the instructions or the file list name. This tool does not run "
    "scripts. " + _SANDBOX_NOTE
)


@dataclass(frozen=True)
class SkillIndexEntry:
    """The name and description of one skill in the tool description."""

    name: str
    description: str

    @classmethod
    def from_model(cls, skill: Skill) -> "SkillIndexEntry":
        return cls(name=skill.name, description=skill.description)


def _shorten(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def is_chat_skill(skill: Skill, *, craft_enabled: bool = ENABLE_CRAFT) -> bool:
    """True when chat offers ``skill``: everything except Craft-only built-ins
    while Craft is off."""
    return craft_enabled or skill.built_in_skill_id not in CRAFT_ONLY_BUILT_IN_SKILL_IDS


def build_read_skill_description(skills: Sequence[SkillIndexEntry]) -> str:
    """The tool description: usage guidance plus a compact skill index."""
    listed = skills[:MAX_INDEXED_SKILLS]
    lines = [_DESCRIPTION_INTRO, "", "Available skills:"]
    lines.extend(
        f"- {skill.name}: {_shorten(skill.description, MAX_INDEX_DESCRIPTION_CHARS)}"
        for skill in listed
    )
    hidden = len(skills) - len(listed)
    if hidden > 0:
        lines.append(
            f"({hidden} more skills are not listed. "
            "Call read_skill with an exact skill name to read one.)"
        )
    return "\n".join(lines)


def load_effective_skill_files(
    user_id: UUID, skill_name: str
) -> Mapping[str, bytes] | None:
    """Return the files of the user's effective skill named ``skill_name``.

    Returns ``None`` when the user no longer has that skill. Reads the skill
    state again, so a skill turned off during the chat is not served.
    """
    # Local imports: onyx.db.skill imports onyx.db.tools, which imports the
    # built-in tool map, which imports this module.
    from onyx.db.skill import list_runtime_skills_for_user
    from onyx.skills.push import build_skill_files_for_user

    with get_session_with_current_tenant() as db_session:
        user = db_session.get(User, user_id)
        if user is None:
            return None
        skill = next(
            (
                skill
                for skill in list_runtime_skills_for_user(
                    db_session=db_session, user=user
                )
                if skill.name == skill_name and is_chat_skill(skill)
            ),
            None,
        )
        if skill is None:
            return None
        return build_skill_files_for_user(skill, user, db_session)


def _format_file_list(files: Mapping[str, bytes]) -> str:
    entries = sorted(
        (path, len(content)) for path, content in files.items() if path != SKILL_MD_NAME
    )
    if not entries:
        return "This skill has no other files."
    listed = entries[:MAX_LISTED_FILES]
    lines = ["Other files in this skill (read one with read_skill and `path`):"]
    lines.extend(f"- {path} ({size} bytes)" for path, size in listed)
    hidden = len(entries) - len(listed)
    if hidden > 0:
        lines.append(f"({hidden} more files are not listed.)")
    lines.append(
        "These files are reference material. read_skill does not run scripts. "
        f"It returns UTF-8 text files up to {MAX_SKILL_FILE_BYTES} bytes."
    )
    return "\n".join(lines)


class ReadSkillTool(Tool[None]):
    NAME = "read_skill"
    DISPLAY_NAME = "Read Skill"

    def __init__(
        self,
        tool_id: int,
        emitter: Emitter,
        user_id: UUID,
        skills: Sequence[SkillIndexEntry],
    ) -> None:
        super().__init__(emitter=emitter)
        self._id = tool_id
        self._user_id = user_id
        self._skill_names = {skill.name for skill in skills}
        self._description = build_read_skill_description(skills)

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def description(self) -> str:
        return self._description

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    def tool_definition(self) -> ToolDefinition:
        parameters: dict[str, JsonValue] = {
            "type": "object",
            "properties": {
                NAME_FIELD: {
                    "type": "string",
                    "description": "The skill name from the list of available skills.",
                },
                PATH_FIELD: {
                    "type": "string",
                    "description": (
                        "Optional. A file path inside the skill, for example "
                        "`reference/forms.md`. Leave it out to read the skill "
                        "instructions (SKILL.md)."
                    ),
                },
            },
            "required": [NAME_FIELD],
        }
        return ToolDefinition(
            name=self.name, description=self.description, parameters=parameters
        )

    def emit_start(self, placement: Placement) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolStart(tool_name=self.display_name, tool_id=self._id),
            )
        )

    def _emit_text(self, placement: Placement, text: str) -> None:
        self.emitter.emit(
            Packet(
                placement=placement,
                obj=CustomToolDelta(
                    tool_name=self.display_name,
                    tool_id=self._id,
                    response_type="text",
                    data=text,
                ),
            )
        )

    def _fail(self, placement: Placement, message: str) -> ToolCallException:
        self._emit_text(placement, message)
        return ToolCallException(
            message=f"{self.name} failed: {message}",
            llm_facing_message=message,
        )

    def _resolve_name(self, placement: Placement, raw_name: Any) -> str:
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise self._fail(
                placement,
                f"read_skill needs a '{NAME_FIELD}' argument with a skill name.",
            )
        name = raw_name.strip()
        if name not in self._skill_names:
            available = ", ".join(sorted(self._skill_names)[:MAX_INDEXED_SKILLS])
            raise self._fail(
                placement,
                f"There is no skill named '{name}'. Available skills: {available}.",
            )
        return name

    def _load_files(self, placement: Placement, name: str) -> Mapping[str, bytes]:
        try:
            files = load_effective_skill_files(self._user_id, name)
        except Exception:
            logger.warning("read_skill could not load skill %s", name, exc_info=True)
            files = {}
        if files is None:
            raise self._fail(
                placement,
                f"The skill '{name}' is no longer available. Continue without it.",
            )
        if SKILL_MD_NAME not in files:
            raise self._fail(
                placement,
                f"The skill '{name}' could not be loaded. Continue without it.",
            )
        return files

    def run(
        self,
        placement: Placement,
        override_kwargs: None,  # noqa: ARG002
        **llm_kwargs: Any,
    ) -> ToolResponse:
        name = self._resolve_name(placement, llm_kwargs.get(NAME_FIELD))
        raw_path = llm_kwargs.get(PATH_FIELD)
        if raw_path is not None and not isinstance(raw_path, str):
            raise self._fail(placement, f"'{PATH_FIELD}' must be a string.")
        path = raw_path.strip() if raw_path else ""

        try:
            lookup_path = normalize_skill_file_path(path) if path else SKILL_MD_NAME
            files = self._load_files(placement, name)
            if lookup_path == SKILL_MD_NAME:
                return self._read_instructions(placement, name, files)
            return self._read_file(placement, name, files, lookup_path)
        except OnyxError as e:
            raise self._fail(placement, e.detail)

    def _read_instructions(
        self, placement: Placement, name: str, files: Mapping[str, bytes]
    ) -> ToolResponse:
        instructions = read_skill_text_file(
            files, SKILL_MD_NAME, max_bytes=MAX_SKILL_FILE_BYTES
        )
        llm_response = "\n\n".join(
            [
                f"# Skill: {name}",
                strip_skill_md_frontmatter(instructions),
                _format_file_list(files),
                _SANDBOX_NOTE,
            ]
        )
        summary = (
            f"Read the instructions of the '{name}' skill. "
            "Call read_skill again to see them."
        )
        self._emit_text(placement, f"Read the '{name}' skill.")
        return ToolResponse(rich_response=summary, llm_facing_response=llm_response)

    def _read_file(
        self,
        placement: Placement,
        name: str,
        files: Mapping[str, bytes],
        lookup_path: str,
    ) -> ToolResponse:
        text = read_skill_text_file(files, lookup_path, max_bytes=MAX_SKILL_FILE_BYTES)
        llm_response = f"# Skill: {name}, file: {lookup_path}\n\n{text}"
        summary = (
            f"Read '{lookup_path}' from the '{name}' skill. "
            "Call read_skill again to see it."
        )
        self._emit_text(placement, f"Read '{lookup_path}' from the '{name}' skill.")
        return ToolResponse(rich_response=summary, llm_facing_response=llm_response)
