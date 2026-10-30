"""referral reminder marker, referral fraud flag, perf indexes; drop virus game

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_referral_reminder_sent_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column("referral_fraud_flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing referrers are already "reminded" for their current streak
    # (mirrors bot/database/engine.py) so deploy doesn't mass-notify them.
    op.execute(
        "UPDATE users SET last_referral_reminder_sent_at = ("
        "SELECT MAX(r.created_at) FROM users r WHERE r.referrer_id = users.user_id) "
        "WHERE EXISTS (SELECT 1 FROM users r WHERE r.referrer_id = users.user_id)"
    )
    op.create_index("idx_users_referrer_created", "users", ["referrer_id", "created_at"])
    op.create_index("idx_game_sessions_user_type_played", "game_sessions", ["user_id", "game_type", "played_at"])
    op.create_index("idx_game_sessions_user_chat", "game_sessions", ["user_id", "chat_id"])
    op.create_index("idx_game_sessions_chat", "game_sessions", ["chat_id"])
    op.create_index("idx_game_sessions_played_at", "game_sessions", ["played_at"])
    op.drop_table("virus_infections")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("virus_bonus_attempt")
        batch_op.drop_column("virus_last_used_at")


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("virus_last_used_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("virus_bonus_attempt", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.drop_index("idx_game_sessions_played_at", "game_sessions")
    op.drop_index("idx_game_sessions_chat", "game_sessions")
    op.drop_index("idx_game_sessions_user_chat", "game_sessions")
    op.drop_index("idx_game_sessions_user_type_played", "game_sessions")
    op.drop_index("idx_users_referrer_created", "users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("referral_fraud_flagged")
        batch_op.drop_column("last_referral_reminder_sent_at")
