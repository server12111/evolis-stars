"""add shared withdrawal display numbering (stars + vc)

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "withdrawal_counters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("withdrawals", sa.Column("display_number", sa.Integer(), nullable=True))
    op.add_column("vc_withdrawals", sa.Column("display_number", sa.Integer(), nullable=True))
    op.add_column("vc_withdrawals", sa.Column("channel_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vc_withdrawals", "channel_message_id")
    op.drop_column("vc_withdrawals", "display_number")
    op.drop_column("withdrawals", "display_number")
    op.drop_table("withdrawal_counters")
