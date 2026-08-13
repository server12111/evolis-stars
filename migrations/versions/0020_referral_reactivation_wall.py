"""add pending_reactivation_referrer_id/since to User

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("pending_reactivation_referrer_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("pending_reactivation_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "pending_reactivation_since")
    op.drop_column("users", "pending_reactivation_referrer_id")
