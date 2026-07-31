"""drop phone verification and pinned country-notice columns

Revision ID: 0007
Revises: 5d332b616e7f
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "5d332b616e7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    for name in (
        "phone_number",
        "phone_country_code",
        "phone_verified",
        "phone_rejection_notified",
        "country_notice_message_id",
        "country_notice_pinned",
    ):
        if name in columns:
            op.drop_column("users", name)


def downgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("phone_country_code", sa.String(8), nullable=True))
    op.add_column(
        "users",
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column("phone_rejection_notified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("country_notice_message_id", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("country_notice_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
