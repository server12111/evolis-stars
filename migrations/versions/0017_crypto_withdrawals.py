"""add crypto_withdrawals table (third withdrawal currency)

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crypto_withdrawals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("rp_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("payout_amount", sa.Numeric(18, 8), nullable=False),
        sa.Column("ton_rate_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("recipient", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("display_number", sa.Integer(), nullable=True),
        sa.Column("channel_message_id", sa.Integer(), nullable=True),
        sa.Column("admin_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("crypto_withdrawals")
