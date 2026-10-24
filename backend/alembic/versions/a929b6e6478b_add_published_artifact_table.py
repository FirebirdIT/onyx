"""add published artifact table

Revision ID: a929b6e6478b
Revises: 924ba501b4d9
Create Date: 2026-10-24 22:47:31.006933

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a929b6e6478b"
down_revision = "924ba501b4d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "published_artifact",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("hub_artifact_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("delete_token", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hub_artifact_id"),
    )
    op.create_index(
        op.f("ix_published_artifact_user_id"),
        "published_artifact",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_published_artifact_user_id"), table_name="published_artifact"
    )
    op.drop_table("published_artifact")
