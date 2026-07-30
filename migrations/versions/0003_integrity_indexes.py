"""prevent duplicate task completions and promo activations"""

from collections.abc import Sequence

from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_task_completion_task_user",
        "task_completions",
        ["task_id", "user_id"],
        unique=True,
    )
    op.create_index(
        "uq_promo_use_code_user",
        "promo_uses",
        ["code_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_promo_use_code_user", table_name="promo_uses")
    op.drop_index("uq_task_completion_task_user", table_name="task_completions")
