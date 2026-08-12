"""add referral_tier (Premium/Sigma/Good cosmetic badges) to User

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_tier", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "referral_tier")
