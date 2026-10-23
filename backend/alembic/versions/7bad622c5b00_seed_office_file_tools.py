"""seed office file tools

Revision ID: 7bad622c5b00
Revises: c5178de73522
Create Date: 2026-10-23 20:08:23.673121

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7bad622c5b00"
down_revision = "c5178de73522"
branch_labels = None
depends_on = None

DEFAULT_PERSONA_ID = 0

OFFICE_TOOLS = [
    {
        "name": "create_document",
        "display_name": "Word Document",
        "description": "Create an editable Word (.docx) document to download.",
        "in_code_tool_id": "CreateDocumentTool",
        "enabled": True,
    },
    {
        "name": "create_spreadsheet",
        "display_name": "Excel Spreadsheet",
        "description": (
            "Create an editable Excel (.xlsx) workbook with formulas and charts "
            "to download."
        ),
        "in_code_tool_id": "CreateSpreadsheetTool",
        "enabled": True,
    },
    {
        "name": "create_presentation",
        "display_name": "PowerPoint Presentation",
        "description": "Create an editable PowerPoint (.pptx) deck to download.",
        "in_code_tool_id": "CreatePresentationTool",
        "enabled": True,
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for tool in OFFICE_TOOLS:
        existing = conn.execute(
            sa.text("SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id"),
            {"in_code_tool_id": tool["in_code_tool_id"]},
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
                tool,
            )
            tool_id = existing[0]
        else:
            tool_id = conn.execute(
                sa.text("""
                    INSERT INTO tool (name, display_name, description, in_code_tool_id, enabled)
                    VALUES (:name, :display_name, :description, :in_code_tool_id, :enabled)
                    RETURNING id
                    """),
                tool,
            ).scalar_one()

        # Make the tools available in default Chat, like the file reader.
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
    in_code_tool_ids = [tool["in_code_tool_id"] for tool in OFFICE_TOOLS]
    conn.execute(
        sa.text("""
            DELETE FROM persona__tool
            WHERE tool_id IN (
                SELECT id FROM tool WHERE in_code_tool_id = ANY(:in_code_tool_ids)
            )
            """),
        {"in_code_tool_ids": in_code_tool_ids},
    )
    conn.execute(
        sa.text("DELETE FROM tool WHERE in_code_tool_id = ANY(:in_code_tool_ids)"),
        {"in_code_tool_ids": in_code_tool_ids},
    )
