"""add wall_integration_wave* to User and chat_wall_integration_completions

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("wall_integration_wave", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("wall_integration_wave_one", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("wall_integration_wave_two", sa.Text(), nullable=True))
    op.create_table(
        "chat_wall_integration_completions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("item_key", sa.String(length=512), nullable=False),
        sa.Column("rewarded_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", "item_key", name="uq_wall_integration_completion"),
    )


def downgrade() -> None:
    op.drop_table("chat_wall_integration_completions")
    op.drop_column("users", "wall_integration_wave_two")
    op.drop_column("users", "wall_integration_wave_one")
    op.drop_column("users", "wall_integration_wave")
