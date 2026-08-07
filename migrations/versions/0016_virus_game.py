"""add "Вирус" chat game (infections + user cooldown/bonus columns)

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("virus_last_used_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column("virus_bonus_attempt", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "virus_infections",
        sa.Column("infected_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("infector_user_id", sa.BigInteger(), nullable=False),
        sa.Column("virus_type", sa.String(16), nullable=False),
        sa.Column("infected_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_payout_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["infected_user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["infector_user_id"], ["users.user_id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("virus_infections")
    op.drop_column("users", "virus_bonus_attempt")
    op.drop_column("users", "virus_last_used_at")
