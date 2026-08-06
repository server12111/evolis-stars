"""add vc_withdrawals table (second withdrawal currency)

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vc_withdrawals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vc_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("rate", sa.Numeric(10, 2), nullable=False),
        sa.Column("rp_debited", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("admin_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("vc_withdrawals")
