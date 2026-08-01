"""track rewarded sponsor urls for recurring referral payouts

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "rewarded_sponsor_urls" not in columns:
        op.add_column("users", sa.Column("rewarded_sponsor_urls", sa.Text(), nullable=True))
    if "referral_insufficient_notified" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "referral_insufficient_notified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "referral_insufficient_notified")
    op.drop_column("users", "rewarded_sponsor_urls")
