"""flip wall_integration_enabled to opt-in (default false)

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table (SQLite recreates the table under the hood -- see
    # 0012's own comment; op.alter_column's server_default change isn't
    # valid SQLite DDL on its own). bot/database/engine.py's
    # _add_missing_user_columns is the actual migration path this project
    # runs at startup (Alembic itself isn't invoked in production) -- kept
    # in sync here for parity.
    with op.batch_alter_table("chats", schema=None) as batch_op:
        batch_op.alter_column("wall_integration_enabled", server_default=sa.false())
    # One-time reset: this toggle briefly shipped defaulting to True. The
    # paid-sponsors GATE itself was genuinely live that whole time for any
    # chat with >=1 owner sponsor (ChatSponsorWallMiddleware and the
    # mychats.py toggle button were both already wired) -- only the
    # "Проверить" confirmation callback was unreachable (a separate router-
    # wiring fix shipped alongside this one). Resetting every chat to the
    # new opt-in default is a deliberate product decision (confirmed with
    # the user) made within the same short window the toggle shipped in,
    # not a claim that nothing was ever enforced.
    op.execute("UPDATE chats SET wall_integration_enabled = false WHERE wall_integration_enabled != false")


def downgrade() -> None:
    with op.batch_alter_table("chats", schema=None) as batch_op:
        batch_op.alter_column("wall_integration_enabled", server_default=sa.true())
    op.execute("UPDATE chats SET wall_integration_enabled = true WHERE wall_integration_enabled != true")
