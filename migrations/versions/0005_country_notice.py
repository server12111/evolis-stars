"""track the one-time pinned country notice

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"] for column in inspector.get_columns("users")
    }
    if "country_notice_message_id" not in columns:
        op.add_column(
            "users",
            sa.Column("country_notice_message_id", sa.Integer(), nullable=True),
        )
    if "country_notice_pinned" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "country_notice_pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "country_notice_pinned")
    op.drop_column("users", "country_notice_message_id")
