"""add match_type (url/domain) to blocked_sponsor_urls

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blocked_sponsor_urls",
        sa.Column("match_type", sa.String(length=16), nullable=False, server_default="url"),
    )


def downgrade() -> None:
    op.drop_column("blocked_sponsor_urls", "match_type")
