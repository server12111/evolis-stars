import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Task, User
from bot.handlers.tasks import cb_task_skip, settings


def _callback() -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock()),
        answer=AsyncMock(),
    )


class TaskSkipDoesNotLoopTests(unittest.IsolatedAsyncioTestCase):
    """Regression: with exactly 2 active admin tasks, skipping twice used
    to cycle A -> B -> A -> B forever -- _show_next_task only ever
    excluded the ONE task just skipped (via the transient current_task_id
    parameter) for that single render, with nothing persisted across
    calls. Skipping must remember what was skipped (same 15-min pattern
    already used for PiarFlow/FlyerHub skip buttons) so a second skip
    reaches "nothing left" instead of looping back to the first task."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_skipping_both_of_two_tasks_does_not_show_the_first_again(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=900, first_name="User")
            task_a = Task(
                title="Task A", description="d", url="https://t.me/a",
                task_type="external_url", reward=Decimal("0.30"),
            )
            task_b = Task(
                title="Task B", description="d", url="https://t.me/b",
                task_type="external_url", reward=Decimal("0.30"),
            )
            session.add_all((user, task_a, task_b))
            await session.commit()
            task_a_id, task_b_id = task_a.id, task_b.id

        old_pf, old_fh, old_linkni = settings.piarflow_key, settings.flyerhub_key, settings.linkni_code
        settings.piarflow_key = ""
        settings.flyerhub_key = ""
        settings.linkni_code = ""
        try:
            async with self.sessions() as session:
                saved_user = await session.get(User, user.user_id)
                cb1 = _callback()
                cb1.data = f"task:skip:{task_a_id}"
                await cb_task_skip(cb1, saved_user, session)
                shown_after_first_skip = cb1.message.edit_text.await_args.args[0]
                self.assertIn("Task B", shown_after_first_skip)

                cb2 = _callback()
                cb2.data = f"task:skip:{task_b_id}"
                await cb_task_skip(cb2, saved_user, session)
                shown_after_second_skip = cb2.message.edit_text.await_args.args[0]

            self.assertNotIn("Task A", shown_after_second_skip)
            self.assertNotIn("Task B", shown_after_second_skip)
            self.assertIn("выполнены", shown_after_second_skip)
        finally:
            settings.piarflow_key = old_pf
            settings.flyerhub_key = old_fh
            settings.linkni_code = old_linkni


if __name__ == "__main__":
    unittest.main()
