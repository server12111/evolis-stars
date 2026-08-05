import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, User
from bot.handlers.admin.chats import cb_admin_chatslist, cb_admin_chatslist_page


def _admin() -> SimpleNamespace:
    return SimpleNamespace(is_admin=True, user_id=1)


def _callback(data: str) -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, data=data, answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class AdminChatsListTests(ChatModelsTestCase):
    async def test_non_admin_is_ignored(self) -> None:
        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, SimpleNamespace(is_admin=False, user_id=999), session)
        cb.message.edit_text.assert_not_awaited()

    async def test_public_chat_links_via_username(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="Public Chat", username="publicchat",
                status="active", member_count=50, owner_user_id=1,
            ))
            session.add(User(user_id=1, username="owner1", first_name="Owner"))
            await session.commit()

        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, _admin(), session)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn('href="https://t.me/publicchat"', rendered)
        self.assertIn("@owner1", rendered)

    async def test_private_chat_links_via_saved_invite_link_only(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-2, title="Private Chat", username=None,
                invite_link="https://t.me/+abc123", status="active", member_count=10,
            ))
            await session.commit()

        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, _admin(), session)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn('href="https://t.me/+abc123"', rendered)

    async def test_chat_with_no_link_shows_plain_title(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-3, title="No Link Chat", status="active", member_count=5))
            await session.commit()

        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, _admin(), session)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("No Link Chat", rendered)
        self.assertNotIn("<a href", rendered)

    async def test_pagination_shows_next_button_and_second_page_works(self) -> None:
        async with self.sessions() as session:
            for i in range(15):
                session.add(Chat(chat_id=-100 - i, title=f"Chat {i}", status="active", member_count=i))
            await session.commit()

        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, _admin(), session)
        kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
        callback_datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("admin:chatslist:1", callback_datas)

        cb2 = _callback("admin:chatslist:1")
        async with self.sessions() as session:
            await cb_admin_chatslist_page(cb2, _admin(), session)
        rendered2 = cb2.message.edit_text.await_args.args[0]
        self.assertIn("Страница 2/2", rendered2)

    async def test_html_in_title_is_escaped(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-4, title="<script>Evil</script>", status="active", member_count=1))
            await session.commit()

        cb = _callback("admin:chatslist")
        async with self.sessions() as session:
            await cb_admin_chatslist(cb, _admin(), session)

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)


class ChatUpsertUsernameTests(ChatModelsTestCase):
    async def test_upsert_without_username_arg_preserves_previously_saved_one(self) -> None:
        """upsert() is also called from mychats.py's refresh action without
        a username argument — it must not wipe out a username saved
        earlier (e.g. by onboarding.py, which does pass one)."""
        from bot.database.repositories.chat import ChatRepository

        async with self.sessions() as session:
            session.add(Chat(chat_id=-5, title="T", username="keepme", status="active", member_count=10))
            await session.commit()

        async with self.sessions() as session:
            await ChatRepository(session).upsert(
                chat_id=-5, title="T", member_count=20, owner_user_id=None, min_members=250,
            )

        async with self.sessions() as session:
            saved = await session.get(Chat, -5)
        self.assertEqual(saved.username, "keepme")


if __name__ == "__main__":
    unittest.main()
