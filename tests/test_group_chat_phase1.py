import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, ChatMembership, User
from bot.database.repositories.chat import ChatRepository
from bot.database.repositories.chat_membership import ChatMembershipRepository
from bot.handlers.group.chat_leaderboard import msg_chat_leaderboard
from bot.handlers.group.membership import on_chat_member_update
from bot.handlers.group.onboarding import on_my_chat_member
from bot.handlers.group.owner_menu import cmd_evolis_open
from bot.middlewares.group_activity import GroupActivityMiddleware


def _chat_member_updated(chat_id: int, user_id: int, status: str, old_status: str = "left"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Test Chat"),
        new_chat_member=SimpleNamespace(status=status, user=SimpleNamespace(id=user_id)),
        old_chat_member=SimpleNamespace(status=old_status),
    )


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


def _state():
    return SimpleNamespace(storage=MemoryStorage())


class OnboardingTests(ChatModelsTestCase):
    async def test_my_chat_member_added_creates_pending_chat_under_threshold(self) -> None:
        event = _chat_member_updated(-100, 1, "member")
        bot = SimpleNamespace(
            id=1,
            get_chat_member_count=AsyncMock(return_value=10),
            get_chat_administrators=AsyncMock(
                return_value=[SimpleNamespace(status="creator", user=SimpleNamespace(id=42))]
            ),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        async with self.sessions() as session:
            chat = await session.get(Chat, -100)
        self.assertIsNotNone(chat)
        self.assertEqual(chat.status, "pending")
        self.assertEqual(chat.member_count, 10)
        self.assertEqual(chat.owner_user_id, 42)

    async def test_my_chat_member_added_marks_active_over_threshold(self) -> None:
        event = _chat_member_updated(-101, 1, "administrator")
        bot = SimpleNamespace(
            id=1,
            get_chat_member_count=AsyncMock(return_value=300),
            get_chat_administrators=AsyncMock(
                return_value=[SimpleNamespace(status="creator", user=SimpleNamespace(id=42))]
            ),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        async with self.sessions() as session:
            chat = await session.get(Chat, -101)
        self.assertEqual(chat.status, "active")

    async def test_my_chat_member_removed_marks_left(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-102, title="X", status="active", member_count=300))
            await session.commit()

        event = _chat_member_updated(-102, 1, "kicked", old_status="member")
        bot = SimpleNamespace(id=1, get_chat_member_count=AsyncMock(), get_chat_administrators=AsyncMock())
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        async with self.sessions() as session:
            chat = await session.get(Chat, -102)
        self.assertEqual(chat.status, "left")
        self.assertIsNotNone(chat.left_at)


class MembershipSyncTests(ChatModelsTestCase):
    async def test_chat_member_join_and_leave_tracked(self) -> None:
        joined = _chat_member_updated(-200, 555, "member")
        async with self.sessions() as session:
            await on_chat_member_update(joined, session)
        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-200, 555)
        self.assertIsNotNone(membership)
        self.assertIsNone(membership.left_at)

        left = _chat_member_updated(-200, 555, "left", old_status="member")
        async with self.sessions() as session:
            await on_chat_member_update(left, session)
        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-200, 555)
        self.assertIsNotNone(membership.left_at)


class GroupActivityMiddlewareTests(ChatModelsTestCase):
    async def test_group_message_increments_count_private_message_ignored(self) -> None:
        from aiogram.types import Chat as TgChat, Message, Update, User as TgUser
        from datetime import datetime

        async with self.sessions() as session:
            middleware = GroupActivityMiddleware()
            handler = AsyncMock(return_value="handled")

            group_update = Update(
                update_id=1,
                message=Message(
                    message_id=1,
                    date=datetime.utcnow(),
                    chat=TgChat(id=-300, type="supergroup"),
                    from_user=TgUser(id=777, is_bot=False, first_name="A"),
                    text="hi",
                ),
            )
            data = {
                "session": session,
                "event_chat": group_update.message.chat,
                "event_from_user": group_update.message.from_user,
            }
            result = await middleware(handler, group_update, data)
            self.assertEqual(result, "handled")

        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-300, 777)
        self.assertIsNotNone(membership)
        self.assertEqual(membership.message_count, 1)

        # A second message increments again.
        async with self.sessions() as session:
            middleware = GroupActivityMiddleware()
            handler = AsyncMock(return_value="handled")
            data = {
                "session": session,
                "event_chat": SimpleNamespace(id=-300, type="supergroup"),
                "event_from_user": SimpleNamespace(id=777),
            }
            group_update2 = Update(
                update_id=2,
                message=Message(
                    message_id=2,
                    date=datetime.utcnow(),
                    chat=TgChat(id=-300, type="supergroup"),
                    from_user=TgUser(id=777, is_bot=False, first_name="A"),
                    text="hi again",
                ),
            )
            await middleware(handler, group_update2, data)

        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-300, 777)
        self.assertEqual(membership.message_count, 2)


class EvolisOpenCommandTests(ChatModelsTestCase):
    def _message(self, chat_id: int, user_id: int, title: str = "Test Chat"):
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, title=title),
            from_user=SimpleNamespace(id=user_id),
            reply=AsyncMock(),
            answer=AsyncMock(),
        )

    async def test_non_creator_gets_no_response(self) -> None:
        message = self._message(-400, 999)
        bot = SimpleNamespace(
            get_chat_member_count=AsyncMock(return_value=300),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
        )
        async with self.sessions() as session:
            await cmd_evolis_open(message, bot, session)

        message.reply.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_under_threshold_shows_requirement_message(self) -> None:
        message = self._message(-401, 42)
        bot = SimpleNamespace(
            get_chat_member_count=AsyncMock(return_value=10),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="creator")),
        )
        async with self.sessions() as session:
            await cmd_evolis_open(message, bot, session)

        message.reply.assert_awaited_once()
        rendered = message.reply.await_args.args[0]
        self.assertIn("250", rendered)
        message.answer.assert_not_awaited()

    async def test_creator_over_threshold_sees_stats_menu(self) -> None:
        # Two chat members, one of whom is a registered bot user.
        async with self.sessions() as session:
            session.add(User(user_id=1001, first_name="A", stars_balance=Decimal("5.50")))
            session.add(ChatMembership(chat_id=-402, user_id=1001))
            session.add(ChatMembership(chat_id=-402, user_id=1002))  # not registered
            await session.commit()

        message = self._message(-402, 42)
        bot = SimpleNamespace(
            get_chat_member_count=AsyncMock(return_value=300),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="creator")),
        )
        async with self.sessions() as session:
            await cmd_evolis_open(message, bot, session)

        message.answer.assert_awaited_once()
        rendered = message.answer.await_args.args[0]
        self.assertIn("300", rendered)
        self.assertIn("<b>1</b>", rendered)  # 1 registered member
        self.assertIn("5.50", rendered)

        async with self.sessions() as session:
            chat = await ChatRepository(session).get(-402)
        self.assertEqual(chat.owner_user_id, 42)
        self.assertEqual(chat.status, "active")


class ChatLeaderboardTests(ChatModelsTestCase):
    async def test_top_10_ordered_by_member_count(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="Small", status="active", member_count=250))
            session.add(Chat(chat_id=-2, title="Big", status="active", member_count=9000))
            session.add(Chat(chat_id=-3, title="Left one", status="left", member_count=99999))
            await session.commit()

        message = SimpleNamespace(
            text="Топ Чатов",
            reply=AsyncMock(),
            answer=AsyncMock(),
        )
        async with self.sessions() as session:
            await msg_chat_leaderboard(message, session)

        message.answer.assert_awaited_once()
        rendered = message.answer.await_args.args[0]
        big_pos = rendered.index("Big")
        small_pos = rendered.index("Small")
        self.assertLess(big_pos, small_pos)
        self.assertNotIn("Left one", rendered)


class BalanceCommandTests(ChatModelsTestCase):
    async def _message(self, text: str, user_id: int):
        return SimpleNamespace(
            text=text,
            from_user=SimpleNamespace(id=user_id),
            reply=AsyncMock(),
        )

    async def test_each_alias_shows_own_balance(self) -> None:
        from bot.handlers.group.balance import msg_balance

        async with self.sessions() as session:
            session.add(User(user_id=555, first_name="A", stars_balance=Decimal("12.34")))
            await session.commit()

        for alias in ["б", "бал", "балик", "баланс", "Баланс", "БАЛ"]:
            message = await self._message(alias, 555)
            async with self.sessions() as session:
                await msg_balance(message, session)
            message.reply.assert_awaited_once()
            rendered = message.reply.await_args.args[0]
            self.assertIn("12.34", rendered)

    async def test_unregistered_user_gets_registration_prompt(self) -> None:
        from bot.handlers.group.balance import msg_balance

        message = await self._message("баланс", 556)
        async with self.sessions() as session:
            await msg_balance(message, session)
        args, kwargs = message.reply.await_args
        self.assertIn("пройдите регистрацию", args[0])
        markup = kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]
        self.assertIn("Пройти регистрацию", button.text)
        self.assertIn("?start=group", button.url)

    async def test_unrelated_text_does_not_match(self) -> None:
        from bot.handlers.group.balance import _matches_balance

        self.assertFalse(_matches_balance(SimpleNamespace(text="балансировка")))
        self.assertFalse(_matches_balance(SimpleNamespace(text="мой баланс")))
        self.assertTrue(_matches_balance(SimpleNamespace(text="  Баланс  ")))


if __name__ == "__main__":
    unittest.main()
