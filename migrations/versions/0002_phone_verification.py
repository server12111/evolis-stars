"""add phone allowlist and referral acceptance state"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_counted", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("phone_number", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("phone_country_code", sa.String(8), nullable=True))
    op.add_column("users", sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("phone_rejection_notified", sa.Boolean(), nullable=False, server_default="false"))
    op.execute("UPDATE users SET referral_counted = 1 WHERE referrer_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("users", "phone_rejection_notified")
    op.drop_column("users", "phone_verified")
    op.drop_column("users", "phone_country_code")
    op.drop_column("users", "phone_number")
    op.drop_column("users", "referral_counted")
