"""add per-chat games toggle and sponsor wall (max-per-chat + completions)

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chats", sa.Column("games_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "chats", sa.Column("sponsor_wall_max_sponsors", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_table(
        "chat_sponsor_wall_sponsors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("sponsor_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("sponsor_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("added_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "sponsor_chat_id", name="uq_wall_sponsor_chat"),
    )
    op.create_table(
        "chat_sponsor_wall_completions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "sponsor_id",
            sa.Integer(),
            sa.ForeignKey("chat_sponsor_wall_sponsors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("rewarded_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("sponsor_id", "user_id", name="uq_wall_completion_sponsor_user"),
    )


def downgrade() -> None:
    op.drop_table("chat_sponsor_wall_completions")
    op.drop_table("chat_sponsor_wall_sponsors")
    op.drop_column("chats", "sponsor_wall_max_sponsors")
    op.drop_column("chats", "games_enabled")
