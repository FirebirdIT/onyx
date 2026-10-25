"""Unit tests for the read_skill chat tool."""

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from onyx.chat.emitter import Emitter
from onyx.db import skill as skill_db
from onyx.db.models import Skill, User
from onyx.llm.interfaces import LLM
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import CustomToolDelta
from onyx.skills import push
from onyx.tools import tool_constructor
from onyx.tools.built_in_tools import BUILT_IN_TOOL_MAP, TOOL_NAME_TO_CLASS
from onyx.tools.models import (
    PersonaToolConfiguration,
    ToolCallException,
    ToolConfiguration,
)
from onyx.tools.tool_implementations.search.search_tool import PersonaSearchInfo
from onyx.tools.tool_implementations.skills import read_skill_tool
from onyx.tools.tool_implementations.skills.read_skill_tool import (
    MAX_INDEX_DESCRIPTION_CHARS,
    MAX_INDEXED_SKILLS,
    MAX_SKILL_FILE_BYTES,
    ReadSkillTool,
    SkillIndexEntry,
    build_read_skill_description,
)

PLACEMENT = Placement(turn_index=0, tab_index=0)
SKILL_NAME = "pdf-forms"

SKILL_FILES: dict[str, bytes] = {
    "SKILL.md": (
        f"---\nname: {SKILL_NAME}\ndescription: Fill PDF forms.\n---\n\n"
        "# PDF forms\n\nRead reference/forms.md first."
    ).encode(),
    "reference/forms.md": b"Use field names exactly.",
    "scripts/fill.py": b"print('fill')",
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00binary",
    "big.txt": b"a" * (MAX_SKILL_FILE_BYTES + 1),
    "latin1.txt": "caf\xe9".encode("latin-1"),
}


def _tool(
    monkeypatch: pytest.MonkeyPatch,
    files: Mapping[str, bytes] | None = SKILL_FILES,
    skills: Sequence[SkillIndexEntry] | None = None,
) -> tuple[ReadSkillTool, MagicMock, MagicMock]:
    loader = MagicMock(return_value=files)
    monkeypatch.setattr(read_skill_tool, "load_effective_skill_files", loader)
    emitter = MagicMock(spec=Emitter)
    tool = ReadSkillTool(
        tool_id=11,
        emitter=emitter,
        user_id=uuid4(),
        skills=(
            skills
            if skills is not None
            else [SkillIndexEntry(name=SKILL_NAME, description="Fill PDF forms.")]
        ),
    )
    return tool, emitter, loader


def _emitted_texts(emitter: MagicMock) -> list[str]:
    texts: list[str] = []
    for call in emitter.emit.call_args_list:
        obj = call.args[0].obj
        if isinstance(obj, CustomToolDelta) and isinstance(obj.data, str):
            texts.append(obj.data)
    return texts


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


def test_tool_is_registered_as_built_in() -> None:
    assert BUILT_IN_TOOL_MAP[ReadSkillTool.__name__] is ReadSkillTool
    assert TOOL_NAME_TO_CLASS["read_skill"] is ReadSkillTool


# ------------------------------------------------------------------
# Description index
# ------------------------------------------------------------------


def test_description_lists_skills_and_sandbox_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _, _ = _tool(
        monkeypatch,
        skills=[
            SkillIndexEntry(name="pptx", description="Build slide decks."),
            SkillIndexEntry(
                name="brand-voice", description="Write in the  company\nvoice."
            ),
        ],
    )
    definition = tool.tool_definition()

    assert definition.name == "read_skill"
    assert "- pptx: Build slide decks." in definition.description
    assert "- brand-voice: Write in the company voice." in definition.description
    assert "call read_skill" in definition.description
    assert "does not run scripts" in definition.description
    assert "written for a coding sandbox" in definition.description


def test_description_caps_skill_count_and_description_length() -> None:
    long_description = "word " * 200
    skills = [
        SkillIndexEntry(name=f"skill-{index:03d}", description=long_description)
        for index in range(MAX_INDEXED_SKILLS + 5)
    ]

    description = build_read_skill_description(skills)

    listed = [line for line in description.splitlines() if line.startswith("- ")]
    assert len(listed) == MAX_INDEXED_SKILLS
    assert "skill-049" in description
    assert "skill-050" not in description
    assert "5 more skills are not listed" in description
    for line in listed:
        _, entry_description = line.split(": ", 1)
        assert len(entry_description) <= MAX_INDEX_DESCRIPTION_CHARS
        assert entry_description.endswith("…")


# ------------------------------------------------------------------
# Reading SKILL.md
# ------------------------------------------------------------------


def test_reading_skill_returns_instructions_and_file_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, emitter, loader = _tool(monkeypatch)

    response = tool.run(PLACEMENT, None, name=SKILL_NAME)

    text = response.llm_facing_response
    assert "# PDF forms" in text
    assert "Read reference/forms.md first." in text
    assert "name: pdf-forms" not in text, "frontmatter must be stripped"
    assert "- reference/forms.md (24 bytes)" in text
    assert "- scripts/fill.py (13 bytes)" in text
    assert "SKILL.md (" not in text
    assert "does not run scripts" in text
    assert "written for a coding sandbox" in text
    assert isinstance(response.rich_response, str)
    assert SKILL_NAME in response.rich_response
    assert _emitted_texts(emitter) == [f"Read the '{SKILL_NAME}' skill."]
    assert loader.call_args.args[1] == SKILL_NAME


def test_explicit_skill_md_path_returns_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _, _ = _tool(monkeypatch)

    response = tool.run(PLACEMENT, None, name=SKILL_NAME, path="./SKILL.md")

    assert "# PDF forms" in response.llm_facing_response


# ------------------------------------------------------------------
# Reading other files
# ------------------------------------------------------------------


def test_reading_text_file_returns_its_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _, _ = _tool(monkeypatch)

    response = tool.run(PLACEMENT, None, name=SKILL_NAME, path="reference/forms.md")

    assert response.llm_facing_response.endswith("Use field names exactly.")
    assert "reference/forms.md" in response.llm_facing_response


@pytest.mark.parametrize(
    "path",
    ["../secrets.txt", "/etc/passwd", "reference/../../x", "reference\\forms.md"],
)
def test_unsafe_path_is_refused(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    tool, _, loader = _tool(monkeypatch)

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name=SKILL_NAME, path=path)

    assert "not a safe path" in exc_info.value.llm_facing_message
    loader.assert_not_called()


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("reference/missing.md", "has no file 'reference/missing.md'"),
        ("assets/logo.png", "binary"),
        ("latin1.txt", "not UTF-8"),
        ("big.txt", f"The limit is {MAX_SKILL_FILE_BYTES} bytes"),
    ],
)
def test_unreadable_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, path: str, message: str
) -> None:
    tool, emitter, _ = _tool(monkeypatch)

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name=SKILL_NAME, path=path)

    assert message in exc_info.value.llm_facing_message
    assert _emitted_texts(emitter) == [exc_info.value.llm_facing_message]


# ------------------------------------------------------------------
# Skill name and loading
# ------------------------------------------------------------------


def test_unknown_skill_name_lists_available_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _, loader = _tool(monkeypatch)

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name="does-not-exist")

    message = exc_info.value.llm_facing_message
    assert "no skill named 'does-not-exist'" in message
    assert SKILL_NAME in message
    loader.assert_not_called()


def test_missing_skill_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    tool, _, _ = _tool(monkeypatch)

    with pytest.raises(ToolCallException):
        tool.run(PLACEMENT, None)


def test_skill_turned_off_during_chat_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, _, _ = _tool(monkeypatch, files=None)

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name=SKILL_NAME)

    assert "no longer available" in exc_info.value.llm_facing_message


def test_skill_without_files_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    tool, _, _ = _tool(monkeypatch, files={})

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name=SKILL_NAME)

    assert "could not be loaded" in exc_info.value.llm_facing_message


def test_loader_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    tool, _, loader = _tool(monkeypatch)
    loader.side_effect = RuntimeError("store down")

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name=SKILL_NAME)

    assert "could not be loaded" in exc_info.value.llm_facing_message


def test_loader_uses_effective_skills_and_push_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=uuid4())
    db_session = MagicMock(spec=Session)
    db_session.get.return_value = user
    session_cm = MagicMock()
    session_cm.__enter__.return_value = db_session
    monkeypatch.setattr(
        read_skill_tool, "get_session_with_current_tenant", lambda: session_cm
    )
    built_in = cast(Skill, SimpleNamespace(name="pptx", built_in_skill_id="pptx"))
    custom = cast(Skill, SimpleNamespace(name=SKILL_NAME, built_in_skill_id=None))
    monkeypatch.setattr(
        skill_db, "list_runtime_skills_for_user", lambda **_: [built_in, custom]
    )
    build = MagicMock(return_value={"SKILL.md": b"x"})
    monkeypatch.setattr(push, "build_skill_files_for_user", build)

    assert read_skill_tool.load_effective_skill_files(user.id, "pptx") == {
        "SKILL.md": b"x"
    }
    build.assert_called_once_with(built_in, user, db_session)
    assert read_skill_tool.load_effective_skill_files(user.id, "gone") is None

    db_session.get.return_value = None
    assert read_skill_tool.load_effective_skill_files(UUID(int=0), "pptx") is None


# ------------------------------------------------------------------
# Construction in tool_constructor
# ------------------------------------------------------------------


def _configuration() -> PersonaToolConfiguration:
    return PersonaToolConfiguration(
        persona_id=7,
        persona_name="Test",
        tools=[
            ToolConfiguration(
                id=5,
                name="read_skill",
                description=None,
                display_name="Read Skill",
                in_code_tool_id=ReadSkillTool.__name__,
                enabled=True,
                openapi_schema=None,
                mcp_input_schema=None,
                custom_headers=None,
                passthrough_auth=False,
                mcp_server_id=None,
                oauth_config_id=None,
            )
        ],
        search=PersonaSearchInfo(
            document_set_names=[],
            search_start_date=None,
            attached_document_ids=[],
            hierarchy_node_ids=[],
        ),
    )


def _construct(
    monkeypatch: pytest.MonkeyPatch, skills: list[Skill], user: User | None = None
) -> dict:
    monkeypatch.setattr(tool_constructor, "get_current_search_settings", MagicMock())
    monkeypatch.setattr(tool_constructor, "get_default_document_index", MagicMock())
    monkeypatch.setattr(
        tool_constructor, "list_runtime_skills_for_user", lambda **_: skills
    )
    return tool_constructor._construct_tools_impl(
        configuration=_configuration(),
        db_session=MagicMock(spec=Session),
        emitter=MagicMock(spec=Emitter),
        user=user or User(id=uuid4(), enable_memory_tool=False),
        llm=MagicMock(spec=LLM),
    )


def test_constructor_skips_tool_without_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _construct(monkeypatch, []) == {}


def test_constructor_indexes_built_in_and_custom_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = [
        Skill(name="pptx", description="Build slide decks.", built_in_skill_id="pptx"),
        Skill(name=SKILL_NAME, description="Fill PDF forms.", bundle_file_id="b"),
    ]

    result = _construct(monkeypatch, skills)

    tools = result[5]
    assert len(tools) == 1
    assert isinstance(tools[0], ReadSkillTool)
    assert "- pptx: Build slide decks." in tools[0].description
    assert f"- {SKILL_NAME}: Fill PDF forms." in tools[0].description


@pytest.mark.parametrize(
    ("built_in_skill_id", "craft_enabled", "offered"),
    [
        ("craft-documentation", False, False),
        ("craft-documentation", True, True),
        ("pptx", False, True),
        (None, False, True),
    ],
)
def test_chat_leaves_out_craft_only_skills_while_craft_is_off(
    built_in_skill_id: str | None, craft_enabled: bool, offered: bool
) -> None:
    skill = cast(Skill, SimpleNamespace(built_in_skill_id=built_in_skill_id))
    assert read_skill_tool.is_chat_skill(skill, craft_enabled=craft_enabled) is offered
