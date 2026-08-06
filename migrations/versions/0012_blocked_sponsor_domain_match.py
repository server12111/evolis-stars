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
    # batch_alter_table (SQLite recreates the table under the hood) --
    # 0011's original single-column UNIQUE(url_key) has to go, or a
    # domain block ever colliding with an existing exact-url block's
    # url_key (e.g. "example.com" blocked as a link, then blocked again
    # as a domain) hits the old constraint on INSERT. See
    # bot/database/engine.py's _add_missing_user_columns for the actual
    # migration path this project runs at startup (Alembic itself isn't
    # invoked in production) -- kept in sync here for parity.
    with op.batch_alter_table("blocked_sponsor_urls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("match_type", sa.String(length=16), nullable=False, server_default="url"))
        batch_op.drop_constraint("uq_blocked_sponsor_urls_url_key", type_="unique")
        batch_op.create_unique_constraint("uq_blocked_sponsor_urls_type_key", ["match_type", "url_key"])


def downgrade() -> None:
    with op.batch_alter_table("blocked_sponsor_urls", schema=None) as batch_op:
        batch_op.drop_constraint("uq_blocked_sponsor_urls_type_key", type_="unique")
        batch_op.create_unique_constraint("uq_blocked_sponsor_urls_url_key", ["url_key"])
        batch_op.drop_column("match_type")
