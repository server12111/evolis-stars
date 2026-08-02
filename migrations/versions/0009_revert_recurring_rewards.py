"""drop rewarded_sponsor_urls — referral reward reverted to one-time payment

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "rewarded_sponsor_urls" in columns:
        op.drop_column("users", "rewarded_sponsor_urls")


def downgrade() -> None:
    op.add_column("users", sa.Column("rewarded_sponsor_urls", sa.Text(), nullable=True))
