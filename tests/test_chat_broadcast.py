import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, User
from bot.database.repositories.chat_broadcast import ChatBroadcastRepository
from bot.database.repositories.settings import SettingsRepository
from bot.handlers.mychats import (
    cb_custom_broadcast_add_start,
    cb_custom_broadcast_delete,
    cb_custom_broadcast_interval_start,
    cb_custom_broadcast_panel,
    cb_custom_broadcast_toggle,
    msg_custom_broadcast_interval,
    msg_custom_broadcast_text,
)
from bot.services.chat_broadcast_scheduler import _run_pass, _send_one
from bot.states.group import ChatOwnerBroadcastStates


def _state():
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _callback(user_id: int, data: str):
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=user_id), data=data, answer=AsyncMock())


def _text_message(user_id: int, text: str):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=user_id), answer=AsyncMock())


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class RepositoryTests(ChatModelsTestCase):
    async def test_add_list_delete_message(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        async with self.sessions() as session:
            repo = ChatBroadcastRepository(session)
            m1 = await repo.add_message(-1, "hello")
            await repo.add_message(-1, "world")
            self.assertEqual(await repo.count(-1), 2)
            messages = await repo.list_messages(-1)
            self.assertEqual([m.text for m in messages], ["hello", "world"])

            self.assertTrue(await repo.delete_message(-1, m1.id))
            self.assertEqual(await repo.count(-1), 1)
            # Wrong chat_id must not delete another chat's message.
            self.assertFalse(await repo.delete_message(-2, messages[1].id))

    async def test_due_chats_respects_interval_and_last_sent(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="Never sent", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            session.add(Chat(
                chat_id=-2, title="Due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
                custom_broadcast_last_sent_at=now - timedelta(seconds=120),
            ))
            session.add(Chat(
                chat_id=-3, title="Not due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=600,
                custom_broadcast_last_sent_at=now - timedelta(seconds=30),
            ))
            session.add(Chat(
                chat_id=-4, title="Disabled", status="active", owner_user_id=1,
                custom_broadcast_enabled=False, custom_broadcast_interval_seconds=1,
            ))
            session.add(Chat(
                chat_id=-5, title="No interval yet", status="active", owner_user_id=1,
                custom_broadcast_enabled=True,
            ))
            await session.commit()

        async with self.sessions() as session:
            due = await ChatBroadcastRepository(session).due_chats(now)
        self.assertEqual({c.chat_id for c in due}, {-1, -2})


class HandlerAccessControlTests(ChatModelsTestCase):
    async def test_add_start_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(999, "mychats:custombc:add:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_add_start(cb, session, state)

        self.assertIsNone(await state.get_state())
        cb.message.edit_text.assert_not_awaited()

    async def test_add_text_flow_creates_message(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(1, "mychats:custombc:add:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_add_start(cb, session, state)
        self.assertEqual(await state.get_state(), ChatOwnerBroadcastStates.enter_text)

        msg = _text_message(1, "Join our giveaway!")
        async with self.sessions() as session:
            await msg_custom_broadcast_text(msg, session, state)
        self.assertIsNone(await state.get_state())

        async with self.sessions() as session:
            messages = await ChatBroadcastRepository(session).list_messages(-1)
        self.assertEqual([m.text for m in messages], ["Join our giveaway!"])

    async def test_delete_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            msg_row = await ChatBroadcastRepository(session).add_message(-1, "hi")

        cb = _callback(999, f"mychats:custombc:del:-1:{msg_row.id}")
        async with self.sessions() as session:
            await cb_custom_broadcast_delete(cb, session)

        async with self.sessions() as session:
            self.assertEqual(await ChatBroadcastRepository(session).count(-1), 1)

    async def test_interval_start_denied_for_non_owner(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        cb = _callback(999, "mychats:custombc:interval:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_interval_start(cb, session, state)

        self.assertIsNone(await state.get_state())
        cb.message.edit_text.assert_not_awaited()

    async def test_interval_below_minimum_rejected(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.update_data(chat_id=-1)
        await state.set_state(ChatOwnerBroadcastStates.enter_interval)
        msg = _text_message(1, "5")
        async with self.sessions() as session:
            await msg_custom_broadcast_interval(msg, session, state)

        msg.answer.assert_awaited_once()
        self.assertIn("не может быть меньше", msg.answer.await_args.args[0])
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertIsNone(chat.custom_broadcast_interval_seconds)

    async def test_interval_valid_is_saved(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        state = _state()
        await state.update_data(chat_id=-1)
        await state.set_state(ChatOwnerBroadcastStates.enter_interval)
        msg = _text_message(1, "600")
        async with self.sessions() as session:
            await msg_custom_broadcast_interval(msg, session, state)

        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertEqual(chat.custom_broadcast_interval_seconds, 600)
        self.assertIsNone(await state.get_state())

    async def test_toggle_blocked_without_texts_or_interval(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertFalse(chat.custom_broadcast_enabled)

    async def test_toggle_succeeds_once_text_and_interval_present(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_interval_seconds=300,
            ))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "hi")

        cb = _callback(1, "mychats:custombc:toggle:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_toggle(cb, session)

        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertTrue(chat.custom_broadcast_enabled)

    async def test_panel_shows_reward_and_texts(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="T", status="active", owner_user_id=1))
            await session.commit()
            await ChatBroadcastRepository(session).add_message(-1, "Come play!")

        cb = _callback(1, "mychats:custombc:-1")
        async with self.sessions() as session:
            await cb_custom_broadcast_panel(cb, session, _state())

        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Come play!", rendered)
        self.assertIn("RP⭐️", rendered)


class SchedulerTests(ChatModelsTestCase):
    async def test_send_one_rotates_credits_owner_and_advances_index(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner", stars_balance=Decimal("0")))
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()
            repo = ChatBroadcastRepository(session)
            await repo.add_message(-1, "first")
            await repo.add_message(-1, "second")
            await SettingsRepository(session).set("broadcast_reward_per_send", "0.5")

        bot = SimpleNamespace(send_message=AsyncMock())
        now = datetime.utcnow()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, now)

        bot.send_message.assert_awaited_once_with(-1, "first")
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            owner = await session.get(User, 1)
        self.assertEqual(chat.custom_broadcast_next_index, 1)
        self.assertEqual(chat.custom_broadcast_last_sent_at, now)
        self.assertEqual(owner.stars_balance, Decimal("0.5"))

        # Second call rotates to the other text.
        bot2 = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot2, session, chat, now)
        bot2.send_message.assert_awaited_once_with(-1, "second")

    async def test_send_one_disables_broadcast_when_no_texts_left(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner", stars_balance=Decimal("0")))
            session.add(Chat(
                chat_id=-1, title="T", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
            ))
            await session.commit()

        bot = SimpleNamespace(send_message=AsyncMock())
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
            await _send_one(bot, session, chat, datetime.utcnow())

        bot.send_message.assert_not_awaited()
        async with self.sessions() as session:
            chat = await session.get(Chat, -1)
        self.assertFalse(chat.custom_broadcast_enabled)

    async def test_run_pass_only_sends_to_due_chats(self) -> None:
        now = datetime.utcnow()
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="Owner", stars_balance=Decimal("0")))
            session.add(Chat(
                chat_id=-1, title="Due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=60,
                custom_broadcast_last_sent_at=now - timedelta(seconds=120),
            ))
            session.add(Chat(
                chat_id=-2, title="Not due", status="active", owner_user_id=1,
                custom_broadcast_enabled=True, custom_broadcast_interval_seconds=600,
                custom_broadcast_last_sent_at=now,
            ))
            await session.commit()
            repo = ChatBroadcastRepository(session)
            await repo.add_message(-1, "hi")
            await repo.add_message(-2, "hi")

        bot = SimpleNamespace(send_message=AsyncMock())
        with patch("bot.services.chat_broadcast_scheduler.SessionFactory", self.sessions):
            await _run_pass(bot)

        bot.send_message.assert_awaited_once_with(-1, "hi")


if __name__ == "__main__":
    unittest.main()
