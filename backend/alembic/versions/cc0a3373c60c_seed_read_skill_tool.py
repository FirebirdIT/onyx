"""seed read skill tool

Revision ID: cc0a3373c60c
Revises: 13b01ea7efcb
Create Date: 2026-10-25 23:02:17.754131

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "cc0a3373c60c"
down_revision = "13b01ea7efcb"
branch_labels = None
depends_on = None

DEFAULT_PERSONA_ID = 0

READ_SKILL_TOOL = {
    "name": "read_skill",
    "display_name": "Read Skill",
    "description": "Read the instructions of the skills the user added.",
    "in_code_tool_id": "ReadSkillTool",
    "enabled": True,
}


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id"),
        {"in_code_tool_id": READ_SKILL_TOOL["in_code_tool_id"]},
    ).fetchone()
    if existing:
        conn.execute(
            sa.text("""
                UPDATE tool
                SET name = :name,
                    display_name = :display_name,
                    description = :description
                WHERE in_code_tool_id = :in_code_tool_id
                """),
            READ_SKILL_TOOL,
        )
        tool_id = existing[0]
    else:
        tool_id = conn.execute(
            sa.text("""
                INSERT INTO tool (name, display_name, description, in_code_tool_id, enabled)
                VALUES (:name, :display_name, :description, :in_code_tool_id, :enabled)
                RETURNING id
                """),
            READ_SKILL_TOOL,
        ).scalar_one()

    # Make the tool available in default Chat. It only reaches the model when
    # the user has at least one enabled custom skill.
    conn.execute(
        sa.text("""
            INSERT INTO persona__tool (persona_id, tool_id)
            SELECT :persona_id, :tool_id
            WHERE EXISTS (SELECT 1 FROM persona WHERE id = :persona_id)
            ON CONFLICT DO NOTHING
            """),
        {"persona_id": DEFAULT_PERSONA_ID, "tool_id": tool_id},
    )


def downgrade() -> None:
    conn = op.get_bind()
    params = {"in_code_tool_id": READ_SKILL_TOOL["in_code_tool_id"]}
    conn.execute(
        sa.text("""
            DELETE FROM persona__tool
            WHERE tool_id IN (
                SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id
            )
            """),
        params,
    )
    conn.execute(
        sa.text("DELETE FROM tool WHERE in_code_tool_id = :in_code_tool_id"),
        params,
    )
