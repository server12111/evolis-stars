"""persist two sponsor waves

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "sponsor_wave" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "sponsor_wave",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "sponsor_wave_one" not in columns:
        op.add_column(
            "users",
            sa.Column("sponsor_wave_one", sa.Text(), nullable=True),
        )
    if "sponsor_wave_two" not in columns:
        op.add_column(
            "users",
            sa.Column("sponsor_wave_two", sa.Text(), nullable=True),
        )

    op.execute(
        "UPDATE bot_settings SET value = '6' "
        "WHERE key = 'sponsor_max_channels' "
        "AND CAST(value AS INTEGER) > 6"
    )


def downgrade() -> None:
    op.drop_column("users", "sponsor_wave_two")
    op.drop_column("users", "sponsor_wave_one")
    op.drop_column("users", "sponsor_wave")
