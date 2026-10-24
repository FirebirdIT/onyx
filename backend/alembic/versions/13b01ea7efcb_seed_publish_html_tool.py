"""seed publish html tool

Revision ID: 13b01ea7efcb
Revises: a929b6e6478b
Create Date: 2026-10-24 23:43:19.323671

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "13b01ea7efcb"
down_revision = "a929b6e6478b"
branch_labels = None
depends_on = None

DEFAULT_PERSONA_ID = 0

PUBLISH_HTML_TOOL = {
    "name": "publish_html",
    "display_name": "Publish Web Page",
    "description": "Publish an HTML file from the chat as a public link.",
    "in_code_tool_id": "PublishHtmlTool",
    "enabled": True,
}


def upgrade() -> None:
    conn = op.get_bind()
    existing = conn.execute(
        sa.text("SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id"),
        {"in_code_tool_id": PUBLISH_HTML_TOOL["in_code_tool_id"]},
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
            PUBLISH_HTML_TOOL,
        )
        tool_id = existing[0]
    else:
        tool_id = conn.execute(
            sa.text("""
                INSERT INTO tool (name, display_name, description, in_code_tool_id, enabled)
                VALUES (:name, :display_name, :description, :in_code_tool_id, :enabled)
                RETURNING id
                """),
            PUBLISH_HTML_TOOL,
        ).scalar_one()

    # Make the tool available in default Chat, like the office file tools.
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
    params = {"in_code_tool_id": PUBLISH_HTML_TOOL["in_code_tool_id"]}
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
