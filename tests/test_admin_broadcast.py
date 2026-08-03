import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.engine import Base
from bot.database.models import Chat, User
from bot.handlers.admin.broadcast import (
    cb_broadcast,
    cb_broadcast_audience,
    cb_broadcast_button_attach,
    cb_broadcast_button_new,
    cb_broadcast_button_skip,
    cb_broadcast_confirm,
    cb_broadcast_scope_bot,
    cb_broadcast_scope_chats,
    msg_broadcast_button_label,
    msg_broadcast_button_url,
    msg_broadcast_content,
)
from bot.states.admin import AdminBroadcastStates


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=1, user_id=1))


def _admin() -> User:
    return User(user_id=999, first_name="Admin", is_admin=True, stars_balance=Decimal("0"))


def _message(text: str = "hi"):
    return SimpleNamespace(
        message_id=1,
        chat=SimpleNamespace(id=1),
        text=text,
        answer=AsyncMock(),
    )


def _callback():
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        edit_text=AsyncMock(),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(forward_message=AsyncMock(return_value=SimpleNamespace(message_id=2)))
    return SimpleNamespace(message=message, answer=AsyncMock(), bot=bot, data=None)


class ChatModelsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class BroadcastScopeTests(ChatModelsTestCase):
    async def test_scope_selector_shown_on_entry(self) -> None:
        cb = _callback()
        state = _state()
        await cb_broadcast(cb, _admin(), state)
        cb.message.edit_text.assert_awaited_once()
        rendered = cb.message.edit_text.await_args.args[0]
        self.assertIn("Рассылка", rendered)

    async def test_bot_scope_stores_scope_and_prompts_for_message(self) -> None:
        cb = _callback()
        state = _state()
        await cb_broadcast_scope_bot(cb, _admin(), state)
        data = await state.get_data()
        self.assertEqual(data["scope"], "bot")
        self.assertEqual(await state.get_state(), AdminBroadcastStates.waiting_message)

    async def test_chats_scope_then_opted_in_audience(self) -> None:
        cb = _callback()
        state = _state()
        await cb_broadcast_scope_chats(cb, _admin())
        cb2 = _callback()
        cb2.data = "admin:broadcast_audience:opted_in"
        await cb_broadcast_audience(cb2, _admin(), state)
        data = await state.get_data()
        self.assertEqual(data["scope"], "chats")
        self.assertEqual(data["chat_audience"], "opted_in")
        self.assertEqual(await state.get_state(), AdminBroadcastStates.waiting_message)


class BroadcastButtonFlowTests(ChatModelsTestCase):
    async def test_skip_button_goes_straight_to_confirm(self) -> None:
        state = _state()
        await state.update_data(scope="bot", chat_audience=None)
        message = _message()
        await msg_broadcast_content(message, state, _admin())
        self.assertEqual(await state.get_state(), AdminBroadcastStates.choose_button)

        cb = _callback()
        async with self.sessions() as session:
            await cb_broadcast_button_skip(cb, _admin(), state, session)
        self.assertEqual(await state.get_state(), AdminBroadcastStates.confirm)
        data = await state.get_data()
        self.assertIsNone(data.get("button_id"))

    async def test_attach_lists_existing_active_buttons(self) -> None:
        async with self.sessions() as session:
            from bot.database.repositories.link_clicks import LinkButtonRepository
            await LinkButtonRepository(session).create("Наш канал", "https://t.me/x", created_by=999)

        cb = _callback()
        async with self.sessions() as session:
            await cb_broadcast_button_attach(cb, _admin(), session)
        cb.message.edit_text.assert_awaited_once()
        markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
        labels = [btn.text for row in markup.inline_keyboard for btn in row]
        self.assertIn("Наш канал", labels)

    async def test_create_new_button_flow_reaches_confirm_with_button_id(self) -> None:
        state = _state()
        await state.update_data(scope="bot", chat_audience=None, message_id=1, chat_id=1)

        cb = _callback()
        await cb_broadcast_button_new(cb, _admin(), state)
        self.assertEqual(await state.get_state(), AdminBroadcastStates.enter_button_label)

        label_msg = _message("Наш чат")
        await msg_broadcast_button_label(label_msg, state, _admin())
        self.assertEqual(await state.get_state(), AdminBroadcastStates.enter_button_url)

        url_msg = _message("https://t.me/somechat")
        async with self.sessions() as session:
            await msg_broadcast_button_url(url_msg, state, session, _admin())

        self.assertEqual(await state.get_state(), AdminBroadcastStates.confirm)
        data = await state.get_data()
        self.assertIsNotNone(data.get("button_id"))
        url_msg.answer.assert_awaited()
        rendered = url_msg.answer.await_args.args[0]
        self.assertIn("Наш чат", rendered)


class BroadcastConfirmDispatchTests(ChatModelsTestCase):
    async def test_bot_scope_targets_all_active_users(self) -> None:
        async with self.sessions() as session:
            session.add(User(user_id=1, first_name="A", stars_balance=Decimal("0"), is_blocked=False))
            session.add(User(user_id=2, first_name="B", stars_balance=Decimal("0"), is_blocked=True))
            await session.commit()

        state = _state()
        await state.update_data(scope="bot", chat_audience=None, message_id=1, chat_id=1, button_id=None)
        await state.set_state(AdminBroadcastStates.confirm)

        cb = _callback()
        with patch("bot.handlers.admin.broadcast._run_broadcast", AsyncMock()) as mock_run:
            async with self.sessions() as session:
                await cb_broadcast_confirm(cb, state, session, _admin())

        mock_run.assert_awaited_once()
        args = mock_run.await_args.args
        target_ids = args[2]
        self.assertIn(1, target_ids)
        self.assertNotIn(2, target_ids)  # blocked user excluded

    async def test_chats_scope_opted_in_only_targets_opted_in_active_chats(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="A", status="active", broadcast_opt_in=True))
            session.add(Chat(chat_id=-2, title="B", status="active", broadcast_opt_in=False))
            session.add(Chat(chat_id=-3, title="C", status="left", broadcast_opt_in=True))
            await session.commit()

        state = _state()
        await state.update_data(scope="chats", chat_audience="opted_in", message_id=1, chat_id=1, button_id=None)
        await state.set_state(AdminBroadcastStates.confirm)

        cb = _callback()
        with patch("bot.handlers.admin.broadcast._run_broadcast", AsyncMock()) as mock_run:
            async with self.sessions() as session:
                await cb_broadcast_confirm(cb, state, session, _admin())

        target_ids = mock_run.await_args.args[2]
        self.assertEqual(target_ids, [-1])

    async def test_chats_scope_all_targets_every_active_chat(self) -> None:
        async with self.sessions() as session:
            session.add(Chat(chat_id=-1, title="A", status="active", broadcast_opt_in=True))
            session.add(Chat(chat_id=-2, title="B", status="active", broadcast_opt_in=False))
            await session.commit()

        state = _state()
        await state.update_data(scope="chats", chat_audience="all", message_id=1, chat_id=1, button_id=None)
        await state.set_state(AdminBroadcastStates.confirm)

        cb = _callback()
        with patch("bot.handlers.admin.broadcast._run_broadcast", AsyncMock()) as mock_run:
            async with self.sessions() as session:
                await cb_broadcast_confirm(cb, state, session, _admin())

        target_ids = mock_run.await_args.args[2]
        self.assertCountEqual(target_ids, [-1, -2])

    async def test_attached_button_produces_lc_reply_markup(self) -> None:
        async with self.sessions() as session:
            from bot.database.repositories.link_clicks import LinkButtonRepository
            button = await LinkButtonRepository(session).create("Click me", "https://example.com", created_by=999)
            session.add(User(user_id=1, first_name="A", stars_balance=Decimal("0")))
            await session.commit()

        state = _state()
        await state.update_data(scope="bot", chat_audience=None, message_id=1, chat_id=1, button_id=button.id)
        await state.set_state(AdminBroadcastStates.confirm)

        cb = _callback()
        with patch("bot.handlers.admin.broadcast._run_broadcast", AsyncMock()) as mock_run:
            async with self.sessions() as session:
                await cb_broadcast_confirm(cb, state, session, _admin())

        reply_markup = mock_run.await_args.args[4]
        self.assertIsNotNone(reply_markup)
        self.assertEqual(reply_markup.inline_keyboard[0][0].callback_data, f"lc:{button.id}")


if __name__ == "__main__":
    unittest.main()
