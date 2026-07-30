import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Task, User
from bot.handlers.admin.tasks import cb_task_view as admin_task_view
from bot.handlers.tasks import _show_next_task, cb_tasks_menu, settings


class TaskPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _create_task(self, *, photo_file_id: str = "photo-file-id") -> User:
        async with self.sessions() as session:
            user = User(user_id=800, first_name="User")
            session.add_all(
                (
                    user,
                    Task(
                        title="Photo task",
                        description="Description",
                        url="https://t.me/example",
                        task_type="external_url",
                        reward=Decimal("0.30"),
                        photo_file_id=photo_file_id,
                    ),
                )
            )
            await session.commit()
            return user

    async def test_tasks_menu_opens_custom_task_with_photo(self) -> None:
        user = await self._create_task()
        callback = SimpleNamespace(message=AsyncMock(), answer=AsyncMock())

        async with self.sessions() as session:
            saved_user = await session.get(User, user.user_id)
            await cb_tasks_menu(callback, saved_user, session)

        callback.message.answer_photo.assert_awaited_once()
        self.assertEqual(
            callback.message.answer_photo.await_args.args[0],
            "photo-file-id",
        )
        callback.message.delete.assert_awaited_once()

    async def test_invalid_photo_falls_back_to_text_without_deleting_menu(self) -> None:
        user = await self._create_task(photo_file_id="expired-photo")
        message = AsyncMock()
        message.answer_photo.side_effect = RuntimeError("invalid file id")
        callback = SimpleNamespace(message=message)

        async with self.sessions() as session:
            saved_user = await session.get(User, user.user_id)
            await _show_next_task(callback, saved_user, session)

        message.answer.assert_awaited_once()
        message.delete.assert_not_awaited()

    async def test_empty_custom_tasks_continue_to_piarflow(self) -> None:
        async with self.sessions() as session:
            user = User(user_id=801, first_name="User")
            session.add(user)
            await session.commit()
            callback = SimpleNamespace(message=AsyncMock())
            old_key = settings.piarflow_key
            settings.piarflow_key = "test-key"
            try:
                with patch(
                    "bot.handlers.tasks._show_pf_task",
                    new=AsyncMock(),
                ) as show_piarflow:
                    await _show_next_task(callback, user, session)
                    show_piarflow.assert_awaited_once_with(
                        callback,
                        user,
                        session,
                    )
            finally:
                settings.piarflow_key = old_key

    async def test_admin_task_view_shows_saved_photo(self) -> None:
        user = await self._create_task()
        user.is_admin = True
        callback = SimpleNamespace(
            data="admin:task_view:1",
            message=AsyncMock(),
            answer=AsyncMock(),
        )

        async with self.sessions() as session:
            saved_user = await session.get(User, user.user_id)
            saved_user.is_admin = True
            await admin_task_view(callback, saved_user, session)

        callback.message.answer_photo.assert_awaited_once()
        callback.message.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
