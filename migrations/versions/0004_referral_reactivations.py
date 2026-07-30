"""add rewarded referral return ledger

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "tasks" in existing_tables:
        task_columns = {
            column["name"] for column in inspector.get_columns("tasks")
        }
        if "photo_file_id" not in task_columns:
            op.add_column(
                "tasks",
                sa.Column("photo_file_id", sa.String(256), nullable=True),
            )

    if "auction_rounds" not in existing_tables:
        op.create_table(
            "auction_rounds",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "current_bid",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
            sa.Column("current_bidder_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "prize_pool",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
            sa.Column("start_at", sa.DateTime(), nullable=False),
            sa.Column("end_at", sa.DateTime(), nullable=False),
            sa.Column("winner_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["current_bidder_id"],
                ["users.user_id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if "auction_bids" not in existing_tables:
        op.create_table(
            "auction_bids",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("round_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column(
                "bid_at",
                sa.DateTime(),
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["round_id"],
                ["auction_rounds.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.user_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if "referral_reactivations" not in existing_tables:
        op.create_table(
            "referral_reactivations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("referred_user_id", sa.BigInteger(), nullable=False),
            sa.Column("referrer_id", sa.BigInteger(), nullable=False),
            sa.Column("inactive_since", sa.DateTime(), nullable=False),
            sa.Column(
                "returned_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("reward_amount", sa.Numeric(14, 2), nullable=False),
            sa.ForeignKeyConstraint(
                ["referred_user_id"],
                ["users.user_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["referrer_id"],
                ["users.user_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "referred_user_id",
                "inactive_since",
                name="uq_referral_reactivation_cycle",
            ),
        )
        op.create_index(
            "ix_referral_reactivations_referrer",
            "referral_reactivations",
            ["referrer_id", "returned_at"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_referral_reactivations_referrer",
        table_name="referral_reactivations",
    )
    op.drop_table("referral_reactivations")
    op.drop_table("auction_bids")
    op.drop_table("auction_rounds")
    op.drop_column("tasks", "photo_file_id")
