import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


def _chat_member_updated(
    chat_id: int, user_id: int, status: str, old_status: str = "left", is_bot: bool = False,
    chat_type: str = "supergroup",
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Test Chat", username=None, type=chat_type),
        new_chat_member=SimpleNamespace(status=status, user=SimpleNamespace(id=user_id, is_bot=is_bot)),
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

    async def test_channel_never_becomes_a_manageable_chat(self) -> None:
        """Channels have no member interaction, so none of the "chat
        owner" features (promo/bonus/broadcast/games/top) make sense for
        them — adding the bot as admin to a channel must never create a
        Панель чатов entry, unlike a group/supergroup."""
        event = _chat_member_updated(-300, 1, "administrator", chat_type="channel")
        bot = SimpleNamespace(
            id=1,
            get_chat_member_count=AsyncMock(return_value=5000),
            get_chat_administrators=AsyncMock(
                return_value=[SimpleNamespace(status="creator", user=SimpleNamespace(id=42))]
            ),
            send_message=AsyncMock(),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        async with self.sessions() as session:
            chat = await session.get(Chat, -300)
        self.assertIsNone(chat)
        bot.send_message.assert_not_awaited()  # no "successfully connected" DM either

    async def test_channel_incorrectly_registered_before_the_fix_gets_self_healed(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-301, title="Old Channel", status="active", owner_user_id=42, member_count=5000))
            await session.commit()

        event = _chat_member_updated(-301, 1, "administrator", old_status="administrator", chat_type="channel")
        bot = SimpleNamespace(
            id=1, get_chat_member_count=AsyncMock(), get_chat_administrators=AsyncMock(), send_message=AsyncMock(),
        )
        async with self.sessions() as session:
            await on_my_chat_member(event, bot, session, _state())

        async with self.sessions() as session:
            chat = await session.get(Chat, -301)
            owned = await ChatRepository(session).list_owned_by(42)
        self.assertEqual(chat.status, "left")
        self.assertNotIn(-301, {c.chat_id for c in owned})


class ChatUpsertConcurrencyTests(ChatModelsTestCase):
    async def test_insert_conflict_falls_back_to_updating_the_real_row(self) -> None:
        """Telegram can fire more than one my_chat_member event for the
        same brand-new chat in quick succession (e.g. added then
        immediately promoted to admin) — this event type has no lock
        protecting it, so two upsert() calls can both see no existing Chat
        row and both try to insert. Must not raise an uncaught
        IntegrityError; must fall back to updating the row the other call
        already created."""
        async with self.sessions() as session:
            session.add(Chat(chat_id=-200, title="Old", status="pending", member_count=5))
            await session.commit()

        async with self.sessions() as session:
            real_get = session.get
            call_count = {"n": 0}

            async def fake_get(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return None
                return await real_get(*args, **kwargs)

            with patch.object(session, "get", side_effect=fake_get):
                chat = await ChatRepository(session).upsert(
                    -200, "New Title", 300, owner_user_id=99, min_members=250,
                )

        self.assertEqual(chat.title, "New Title")
        self.assertEqual(chat.status, "active")
        async with self.sessions() as session:
            saved = await session.get(Chat, -200)
        self.assertEqual(saved.title, "New Title")
        self.assertEqual(saved.owner_user_id, 99)


class MembershipSyncTests(ChatModelsTestCase):
    async def test_chat_member_join_and_leave_tracked(self) -> None:
        bot = AsyncMock()
        joined = _chat_member_updated(-200, 555, "member")
        async with self.sessions() as session:
            await on_chat_member_update(joined, session, bot)
        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-200, 555)
        self.assertIsNotNone(membership)
        self.assertIsNone(membership.left_at)

        left = _chat_member_updated(-200, 555, "left", old_status="member")
        async with self.sessions() as session:
            await on_chat_member_update(left, session, bot)
        async with self.sessions() as session:
            repo = ChatMembershipRepository(session)
            membership = await repo.get(-200, 555)
        self.assertIsNotNone(membership.left_at)

    async def test_genuine_join_sends_ephemeral_welcome(self) -> None:
        bot = AsyncMock()
        joined = _chat_member_updated(-201, 556, "member", old_status="left")
        async with self.sessions() as session:
            await on_chat_member_update(joined, session, bot)

        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(args[0], -201)
        self.assertIn("Добро пожаловать", args[1])
        self.assertEqual(kwargs["receiver_user_id"], 556)

    async def test_promotion_of_existing_member_does_not_send_welcome(self) -> None:
        bot = AsyncMock()
        promoted = _chat_member_updated(-202, 557, "administrator", old_status="member")
        async with self.sessions() as session:
            await on_chat_member_update(promoted, session, bot)
        bot.send_message.assert_not_awaited()

    async def test_bot_being_added_does_not_get_a_welcome_message(self) -> None:
        bot = AsyncMock()
        joined = _chat_member_updated(-203, 558, "member", old_status="left", is_bot=True)
        async with self.sessions() as session:
            await on_chat_member_update(joined, session, bot)
        bot.send_message.assert_not_awaited()

    async def test_welcome_send_failure_does_not_crash_membership_tracking(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("boom")
        joined = _chat_member_updated(-204, 559, "member", old_status="left")
        async with self.sessions() as session:
            await on_chat_member_update(joined, session, bot)
        async with self.sessions() as session:
            membership = await ChatMembershipRepository(session).get(-204, 559)
        self.assertIsNotNone(membership)


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

    async def test_non_owner_gets_no_response(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-400, title="T", status="active", owner_user_id=42))
            await session.commit()
        message = self._message(-400, 999)
        async with self.sessions() as session:
            await cmd_evolis_open(message, session)

        message.reply.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_unknown_chat_gets_no_response(self) -> None:
        message = self._message(-401, 42)
        async with self.sessions() as session:
            await cmd_evolis_open(message, session)
        message.reply.assert_not_awaited()

    async def test_owner_gets_short_redirect_to_private_panel(self) -> None:
        # Management (settings/promo/bonus/stats/...) has moved entirely to
        # the private chat panel — /EvolisOpen in a group is now just a
        # pointer there, not a functional panel itself.
        async with self.sessions() as session:
            session.add(Chat(chat_id=-402, title="T", status="active", owner_user_id=42, member_count=300))
            await session.commit()

        message = self._message(-402, 42)
        async with self.sessions() as session:
            await cmd_evolis_open(message, session)

        message.reply.assert_awaited_once()
        args, kwargs = message.reply.await_args
        self.assertIn("личном чате", args[0])
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertTrue(button.url.startswith("https://t.me/"))

        # Existing chat data/settings are untouched by this command now.
        async with self.sessions() as session:
            chat = await ChatRepository(session).get(-402)
        self.assertEqual(chat.owner_user_id, 42)
        self.assertEqual(chat.status, "active")
        self.assertEqual(chat.member_count, 300)


class ChatLeaderboardTests(ChatModelsTestCase):
    async def test_top_10_ordered_by_member_count(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="Small", status="active", member_count=250))
            session.add(
                Chat(chat_id=-2, title="Big", status="active", member_count=9000, username="bigchat"),
            )
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
        # Chats with a saved username must render as a clickable t.me link.
        self.assertIn('<a href="https://t.me/bigchat">Big</a>', rendered)


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
