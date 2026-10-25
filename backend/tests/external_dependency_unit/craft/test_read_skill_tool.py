"""The read_skill chat tool against real skill rows and the file store."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from onyx.chat.emitter import Emitter
from onyx.db.models import Skill, User
from onyx.db.skill import list_runtime_skills_for_user, set_skill_enabled_for_user
from onyx.server.query_and_chat.placement import Placement
from onyx.tools.models import ToolCallException
from onyx.tools.tool_implementations.skills import read_skill_tool
from onyx.tools.tool_implementations.skills.read_skill_tool import (
    ReadSkillTool,
    SkillIndexEntry,
)
from tests.external_dependency_unit.craft.db_helpers import (
    make_external_app,
    make_user,
)

PLACEMENT = Placement(turn_index=0, tab_index=0)


@pytest.fixture
def tool_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Let the tool read through the test session, which sees uncommitted rows."""

    @contextmanager
    def _session() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(read_skill_tool, "get_session_with_current_tenant", _session)
    return db_session


def _tool(user: User, db_session: Session) -> ReadSkillTool:
    skills = list_runtime_skills_for_user(db_session=db_session, user=user)
    return ReadSkillTool(
        tool_id=1,
        emitter=MagicMock(spec=Emitter),
        user_id=user.id,
        skills=[SkillIndexEntry.from_model(skill) for skill in skills],
    )


def test_enabled_custom_skill_is_indexed_and_readable(
    tool_session: Session,
    seeded_skill: Callable[..., Skill],
) -> None:
    user = make_user(tool_session, standard_account=True)
    skill = seeded_skill(
        "chat-brand-voice",
        public=True,
        bundle_files={
            "SKILL.md": (
                "---\nname: chat-brand-voice\ndescription: Write in our voice.\n"
                "---\n\n# Voice\n\nRead reference/tone.md."
            ),
            "reference/tone.md": "Short sentences.",
        },
    )
    set_skill_enabled_for_user(
        skill_id=skill.id, enabled=True, user=user, db_session=tool_session
    )

    tool = _tool(user, tool_session)

    assert "- chat-brand-voice: Seeded skill chat-brand-voice" in tool.description
    instructions = tool.run(PLACEMENT, None, name="chat-brand-voice")
    assert "# Voice" in instructions.llm_facing_response
    assert "- reference/tone.md (16 bytes)" in instructions.llm_facing_response
    tone = tool.run(PLACEMENT, None, name="chat-brand-voice", path="reference/tone.md")
    assert tone.llm_facing_response.endswith("Short sentences.")


def test_built_in_skills_are_indexed_and_read_from_disk(
    tool_session: Session,
) -> None:
    user = make_user(tool_session, standard_account=True)
    built_ins = [
        skill
        for skill in list_runtime_skills_for_user(db_session=tool_session, user=user)
        if skill.built_in_skill_id is not None
    ]
    if not built_ins:
        pytest.skip("No built-in skill rows are seeded in this database.")

    tool = _tool(user, tool_session)
    first = built_ins[0]

    assert f"- {first.name}:" in tool.description
    response = tool.run(PLACEMENT, None, name=first.name)
    assert response.llm_facing_response.startswith(f"# Skill: {first.name}")


def test_skill_turned_off_after_indexing_is_refused(
    tool_session: Session,
    seeded_skill: Callable[..., Skill],
) -> None:
    user = make_user(tool_session, standard_account=True)
    skill = seeded_skill("chat-temporary", public=True)
    set_skill_enabled_for_user(
        skill_id=skill.id, enabled=True, user=user, db_session=tool_session
    )
    tool = _tool(user, tool_session)
    set_skill_enabled_for_user(
        skill_id=skill.id, enabled=False, user=user, db_session=tool_session
    )

    with pytest.raises(ToolCallException) as exc_info:
        tool.run(PLACEMENT, None, name="chat-temporary")

    assert "no longer available" in exc_info.value.llm_facing_message


def test_skill_of_unready_external_app_is_not_indexed(
    tool_session: Session,
    seeded_skill: Callable[..., Skill],
) -> None:
    user = make_user(tool_session, standard_account=True)
    skill = seeded_skill("chat-app-skill", public=True)
    make_external_app(tool_session, skill=skill, auth_template={"token": "{token}"})

    tool = _tool(user, tool_session)

    assert "chat-app-skill" not in tool.description
